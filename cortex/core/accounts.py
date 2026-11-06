"""Accounts + robots for the hub: each wallet account gets a generated robot wallet, its own EPISODE_KEY and (pasted)
0G API key, sealed at rest with ACCOUNTS_KEY (AES-256-GCM), and its own simulator process (python run.py) on a private
127.0.0.1 port that only the hub can talk to (secret header). Idle robots are stopped."""
import asyncio
import base64
import json
import os
import pathlib
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections import deque

import httpx
from Crypto.Cipher import AES
from eth_account import Account
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .episode import EPISODES, ROOT, _node
from .og_client import NO_KEY

FAUCET = "https://faucet.0g.ai"
PAGE = ('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="{}"><title>Skitter</title>'
        '<body style="font:18px system-ui;background:#0b0b0b;color:#e8e3d6;padding:60px">'
        '<p>{}</p><p><a style="color:#e8e3d6" href="/account">Account</a></p>')


def accounts_key():
    """ACCOUNTS_KEY from the env; if missing, generate one and save it to .env (never printed)."""
    k = os.environ.get("ACCOUNTS_KEY", "").strip()
    if not k:
        k, env = secrets.token_hex(32), ROOT / ".env"
        text = env.read_text(encoding="utf-8") if env.exists() else ""
        line = f"ACCOUNTS_KEY={k}"
        text = re.sub(r"^ACCOUNTS_KEY=.*$", line, text, flags=re.M) if re.search(r"^ACCOUNTS_KEY=", text, re.M) \
            else text.rstrip("\n") + ("\n" if text else "") + line + "\n"
        env.write_text(text, encoding="utf-8")
        os.environ["ACCOUNTS_KEY"] = k
        print("\n  ACCOUNTS_KEY was not set: generated one and saved it to .env. Back it up: without it no account's"
              " robot wallet or API key can be opened.\n", flush=True)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", k):
        raise SystemExit("ACCOUNTS_KEY must be 64 hex chars")
    return bytes.fromhex(k)


class Robot:
    def __init__(self, proc, port, secret):
        self.proc, self.port, self.secret, self.ready, self.last = proc, port, secret, False, time.monotonic()


class Hub:
    def __init__(self, folder=None, max_robots=3, idle_s=900, world="house"):
        self.dir = pathlib.Path(folder or os.environ.get("SKITTER_ACCOUNTS") or ROOT / "accounts")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.key, self.max, self.idle_s, self.world = accounts_key(), max_robots, idle_s, world
        self.robots, self.tokens, self.lock, self.log = {}, {}, threading.Lock(), deque(maxlen=500)
        self.claiming = set()
        for f in self.dir.glob("*/account.json"):  # fail closed: a wrong ACCOUNTS_KEY must not start (or overwrite)
            try:
                self._secrets(f.parent.name)
            except Exception:
                raise SystemExit(f"ACCOUNTS_KEY does not open {f}: refusing to start")
        self.http = httpx.AsyncClient(timeout=300)  # verify/upload calls on a robot can take minutes
        threading.Thread(target=self._reaper, daemon=True).start()

    def emit(self, e):
        self.log.append(e)
        if e.get("level") == "block":
            print(f"[hub] {e['msg']}", flush=True)

    def token(self, acct):
        return self.tokens.setdefault(acct, secrets.token_urlsafe(24))

    # ---- accounts ----
    def _seal(self, acct, data):
        n = secrets.token_bytes(12)
        c = AES.new(self.key, AES.MODE_GCM, nonce=n)
        c.update(acct.encode())  # bound to the account: a sealed blob copied to another account won't open
        ct, tag = c.encrypt_and_digest(json.dumps(data).encode())
        return base64.b64encode(n + ct + tag).decode()

    def _secrets(self, acct):
        if acct == "owner":
            return {"robot_key": os.environ.get("STORAGE_PRIVATE_KEY", ""), "episode_key": os.environ.get("EPISODE_KEY", ""),
                    "og_api_key": os.environ.get("OG_API_KEY", ""), "og_mgmt_key": os.environ.get("OG_MGMT_KEY", "")}
        raw = base64.b64decode(json.loads((self.dir / acct / "account.json").read_text())["sealed"])
        c = AES.new(self.key, AES.MODE_GCM, nonce=raw[:12])
        c.update(acct.encode())
        return json.loads(c.decrypt_and_verify(raw[12:-16], raw[-16:]))

    def _write(self, acct, s):
        f = self.dir / acct / "account.json"
        f.parent.mkdir(exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"address": acct, "robot_wallet": Account.from_key(s["robot_key"]).address,
                                   "sealed": self._seal(acct, s)}, indent=1))
        os.replace(tmp, f)

    def signup(self, addr):
        acct = addr.lower()
        with self.lock:
            if not (self.dir / acct / "account.json").exists():
                self._write(acct, {"robot_key": "0x" + bytes(Account.create().key).hex(), "episode_key": secrets.token_hex(32),
                                   "og_api_key": ""})
        return acct

    def home(self, acct):
        return pathlib.Path(os.environ.get("SKITTER_HOME") or EPISODES) if acct == "owner" else self.dir / acct

    def set_key(self, acct, key):
        if acct == "owner":
            return JSONResponse({"error": "the owner robot uses the server's .env keys"}, status_code=400)
        if not re.fullmatch(r"[\x21-\x7e]{8,300}", key) or key == NO_KEY:
            return JSONResponse({"error": "that does not look like a 0G API key"}, status_code=400)
        mgmt = key.startswith("mk-")  # read-only management key: balance + usage of the same 0G account
        with self.lock:
            s = self._secrets(acct)
            s["og_mgmt_key" if mgmt else "og_api_key"] = key
            self._write(acct, s)
        if mgmt:
            return {"ok": True, "key_hint": "…" + key[-4:], "management": True, "restarted": False}
        restart = self.stop(acct)  # the robot reads its key at start
        if restart:
            self.ensure(acct)
        return {"ok": True, "key_hint": "…" + key[-4:], "restarted": restart}

    async def info(self, acct):
        s, r = self._secrets(acct), self.robots.get(acct)
        k = s["og_api_key"] if s["og_api_key"] != NO_KEY else ""
        try:
            wallet = Account.from_key(s["robot_key"]).address
        except Exception:
            wallet = None
        alive = r and r.proc.poll() is None
        return {"account": acct, "owner": acct == "owner", "robot_wallet": wallet, "has_key": bool(k),
                "key_hint": "…" + k[-4:] if k else None, "has_mgmt_key": bool(s.get("og_mgmt_key")),
                "faucet": FAUCET, "max_robots": self.max,
                "robot": "running" if alive and await self.ready(r) else "starting" if alive else "stopped"}

    async def balance(self, acct):
        wallet = (await self.info(acct))["robot_wallet"]
        try:
            r = await self.http.post(os.environ.get("STORAGE_RPC", "https://evmrpc-testnet.0g.ai"), timeout=10, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [wallet, "latest"]})
            return {"address": wallet, "balance_0G": int(r.json()["result"], 16) / 1e18}
        except Exception as e:
            return JSONResponse({"error": f"balance unavailable: {str(e)[:100]}"}, status_code=502)

    async def credits(self, acct):
        """The account's own 0G Private Computer balance (iAI daily credits + deposit) and its latest charged requests.
        Needs an mk- management key with account:read; sk- keys can't read /v1/account/*."""
        mk = self._secrets(acct).get("og_mgmt_key")
        if not mk:
            return {"linked": False}
        base, h = os.environ.get("OG_BASE_URL", "https://router-api.0g.ai/v1").rstrip("/"), {"Authorization": f"Bearer {mk}"}
        try:
            b, u = await asyncio.gather(
                self.http.get(f"{base}/account/balance", headers=h, timeout=10),
                self.http.get(f"{base}/account/usage/history", headers=h, timeout=10,
                              params={"limit": 5, "include_total": "false"}))
            if b.status_code != 200:
                raise ValueError(f"HTTP {b.status_code}")
            b, rows = b.json(), u.json().get("data", []) if u.status_code == 200 else []
        except Exception as e:
            return JSONResponse({"error": f"0G balance unavailable: {str(e)[:100]}"}, status_code=502)
        unit = 1e6 if b.get("currency") == "usd" else 1e18  # micro-USD or neuron
        amt = lambda d, k: int(d.get(k) or 0) / unit  # noqa: E731
        return {"linked": True, "address": b.get("address"), "currency": b.get("currency", "0g"),
                "is_you": str(b.get("address", "")).lower() == acct,  # the key's 0G account = the wallet that signed in
                "credit": amt(b, "credit_balance"), "deposit": amt(b, "deposit_balance"),
                "total": max(0.0, amt(b, "total_balance") - amt(b, "unsettled_charge")),  # unsettled: charged, not yet sent
                "recent": [{"request_id": r.get("request_id"), "model": r.get("model_id"), "t": r.get("created_at"),
                            "cost": amt(r, "cost"), "credit_used": amt(r, "credit_used")} for r in rows]}

    # ---- ENS: each robot is an agent namespace <label>.skitter.eth (ENSv2, Sepolia) ----
    async def ens(self, acct):
        f = self.home(acct) / "ens.json"
        if not f.exists():
            return {"claimed": False, "parent": os.environ.get("ENS_PARENT", "skitter.eth")}
        claim = json.loads(f.read_text())
        try:  # resolved live through the Universal Resolver, not from the local file
            return {"claimed": True, **claim, **await asyncio.to_thread(_node, "ens.mjs", "read", claim["name"], timeout=30)}
        except Exception as e:
            return JSONResponse({"claimed": True, **claim, "error": f"ENS read failed: {str(e)[-100:]}"}, status_code=502)

    async def claim_ens(self, acct, label):
        """Mint <label>.skitter.eth to the account's wallet with its own resolver; the robot wallet may set only its
        skitter.* text records. The hub pays the gas (Sepolia) and tops up the robot wallet once."""
        if not re.fullmatch(r"[a-z0-9-]{3,32}", label):
            return JSONResponse({"error": "name: 3-32 characters, a-z 0-9 and -"}, status_code=400)
        f = self.home(acct) / "ens.json"
        with self.lock:
            if f.exists() or acct in self.claiming:
                return JSONResponse({"error": "this account already has a name"}, status_code=409)
            self.claiming.add(acct)
        try:
            robot = Account.from_key(self._secrets(acct)["robot_key"]).address
            r = await asyncio.to_thread(_node, "ens.mjs", "claim", label, robot if acct == "owner" else acct, robot,
                                        timeout=240)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(r, indent=1))
            return r
        except Exception as e:
            return JSONResponse({"error": f"claim failed: {str(e)[-200:]}"}, status_code=502)
        finally:
            self.claiming.discard(acct)

    # ---- robots ----
    def ensure(self, acct):
        """The account's robot, started if needed; None when MAX_ROBOTS are already running."""
        with self.lock:
            for a in [a for a, r in self.robots.items() if r.proc.poll() is not None]:
                del self.robots[a]
            if acct in self.robots:
                return self.robots[acct]
            if len(self.robots) >= self.max:
                return None
            s, home = self._secrets(acct), self.home(acct)
            home.mkdir(parents=True, exist_ok=True)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            secret = secrets.token_urlsafe(32)
            # every key set explicitly and non-empty: anything missing would be filled from the owner's .env
            env = {**os.environ, "OG_API_KEY": s["og_api_key"] or NO_KEY, "STORAGE_PRIVATE_KEY": s["robot_key"],
                   "EPISODE_KEY": s["episode_key"] or "unset", "SKITTER_HOME": str(home.resolve()),
                   "SKITTER_ROBOT_SECRET": secret, "SKITTER_TOKEN": self.token(acct), "SKITTER_ACCOUNT": acct,
                   "PYTHONUNBUFFERED": "1"}
            with open(home / "robot.log", "ab") as log:
                proc = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--port", str(port), "--world", self.world],
                                        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            self.robots[acct] = r = Robot(proc, port, secret)
            self.emit({"t": time.time(), "type": "robot", "msg": f"robot for {acct} starting on 127.0.0.1:{port}"})
            return r

    async def ready(self, r):
        if not r.ready:
            try:
                r.ready = (await self.http.get(f"http://127.0.0.1:{r.port}/api/status", timeout=1,
                                               headers={"x-skitter-robot": r.secret})).status_code == 200
            except httpx.HTTPError:
                pass
        return r.ready

    def stop(self, acct):
        with self.lock:
            r = self.robots.pop(acct, None)
        if r and r.proc.poll() is None:
            r.proc.terminate()
            try:
                r.proc.wait(10)
            except subprocess.TimeoutExpired:
                r.proc.kill()
            return True
        return False

    def reap(self):
        for acct, r in list(self.robots.items()):
            if time.monotonic() - r.last > self.idle_s:
                self.stop(acct)
                self.emit({"t": time.time(), "type": "robot", "msg": f"robot for {acct} stopped (idle)"})

    def _reaper(self):
        while True:
            time.sleep(min(30, self.idle_s / 3))
            self.reap()

    def close(self):
        for acct in list(self.robots):
            self.stop(acct)

    async def estop(self, acct):
        targets = [r for a, r in list(self.robots.items()) if a == acct and r.ready]
        res = await asyncio.gather(*[self.http.post(f"http://127.0.0.1:{r.port}/api/estop", timeout=5,
                                                    headers={"x-skitter-robot": r.secret}) for r in targets],
                                   return_exceptions=True)
        return sum(getattr(x, "status_code", 0) == 200 for x in res)

    async def proxy(self, acct, req):
        page = not req.url.path.startswith("/api/")
        if not (r := self.ensure(acct)):
            msg = f"All {self.max} robots are in use right now, sorry. Please try again in a few minutes."
            return HTMLResponse(PAGE.format(30, msg), status_code=503) if page else \
                JSONResponse({"error": msg}, status_code=503)
        r.last = time.monotonic()
        if not await self.ready(r):
            return HTMLResponse(PAGE.format(2, "Starting your robot… this page reloads by itself."), status_code=503) \
                if page else JSONResponse({"error": "robot starting"}, status_code=503)
        h = {k: v for k in ("content-type", "origin", "x-skitter-token") if (v := req.headers.get(k))}
        try:
            resp = await self.http.request(req.method, f"http://127.0.0.1:{r.port}{req.url.path}",
                                           params=req.url.query, content=await req.body(),
                                           headers={**h, "x-skitter-robot": r.secret})
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"robot unreachable: {str(e)[:100]}"}, status_code=502)
        return Response(resp.content, resp.status_code, headers={
            k: v for k in ("content-type", "cache-control") if (v := resp.headers.get(k))})
