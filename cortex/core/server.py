"""Web API + UI: / landing page (3D), /login, /app control room, /recordings. Voice: browser speech or WAV -> 0G -> task."""
import os
import pathlib
import re
import secrets
import time
from urllib.parse import urlparse

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .og_client import OGError

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ROBOT_PKG = pathlib.Path(__file__).resolve().parents[1] / "adapters" / "hexapod_pybullet"


LOCAL = {"127.0.0.1", "localhost", "[::1]"}
COOKIE = "skitter_session"
LOGIN = ("/api/login", "/api/login/nonce", "/api/login/wallet")
OPEN = LOGIN + ("/api/estop",)  # no session needed: you can't have one yet / stopping is never locked
MAX_FAILS, LOCKOUT_S = 5, 30
NONCE_S = 300
CHAIN_ID = 16602  # 0G Galileo testnet


def create_app(rt, og, feed=None):
    app = FastAPI()
    token = secrets.token_urlsafe(24)  # per run; only the served control room page knows it
    password = os.environ.get("SKITTER_PASSWORD") or ""
    if not password:
        password = secrets.token_urlsafe(9)
        print(f"\n  SKITTER_PASSWORD not set; control room password for this run:  {password}\n", flush=True)
    hosts = LOCAL | {h.strip().lower() for h in os.environ.get("SKITTER_HOSTS", "").split(",") if h.strip()}
    owners = {a.strip().lower() for a in os.environ.get("OWNER_WALLETS", "").split(",") if a.strip()}
    sessions, fails, locked, nonces = set(), [0], [0.0], {}
    last, blocked = {}, [0]

    def sec(level, msg, key=None):
        """Security log line: shown in the control room and kept in the (sealed) episode."""
        blocked[0] += level == "block"
        now, key = time.monotonic(), key or msg
        if now - last.get(key, -1e9) < 5:  # a hostile page looping requests must not flood the log or the episode
            return
        last[key] = now
        rt.emit({"t": time.time(), "type": "security", "level": level, "msg": msg})

    sec("ok", "control API bound to 127.0.0.1; requests with a foreign Host header are refused")
    sec("ok", "per-run control token issued; only the /app page receives it")
    sec("ok", f"control room and camera need the password; {MAX_FAILS} wrong tries lock login for {LOCKOUT_S} s")
    sec("ok" if owners else "warn", f"0G wallet login for {len(owners)} owner wallet(s)" if owners
        else "no OWNER_WALLETS set: wallet login disabled")
    key_ok = re.fullmatch(r"[0-9a-fA-F]{64}", os.environ.get("EPISODE_KEY", "")) is not None
    sec("ok" if key_ok else "warn", "episodes sealed with AES-256-GCM before upload" if key_ok
        else "no valid EPISODE_KEY: episodes will NOT be uploaded (fails closed)")
    if og:
        sec("ok", f"0G spend cap ${og.budget.cap:.2f}; no 0G decision = no motion")

    @app.middleware("http")
    async def guard(req: Request, call_next):
        # Host check stops DNS rebinding (an evil site resolving to 127.0.0.1 and reading the page + token).
        host = req.headers.get("host") or ""
        if host.rsplit(":", 1)[0].lower() not in hosts:
            sec("block", f"refused {req.method} {req.url.path}: foreign Host '{host[:60]}' (DNS rebinding?)", "host")
            return JSONResponse({"error": "bad host"}, status_code=403)
        # Any website can POST to localhost; the custom token header forces a CORS preflight it can't pass.
        # E-STOP stays open on purpose: stopping must never be locked out.
        # Feed reads are locked too: they return decrypted footage of the home.
        # Login has no token yet, so only its Origin is checked.
        path = req.url.path
        if (req.method == "POST" and path != "/api/estop") or path.startswith("/api/feed"):
            origin = urlparse(req.headers.get("origin") or "http://localhost").hostname
            sent = req.headers.get("x-skitter-token")
            why = ("foreign origin " + str(origin)[:60] if origin not in hosts else None if path in LOGIN
                   else "no token" if not sent else "wrong token" if not secrets.compare_digest(sent, token) else None)
            if why:
                sec("block", f"refused {req.method} {path}: {why} (CSRF?)", why)
                return JSONResponse({"error": "forbidden"}, status_code=403)
        if path.startswith("/api/") and path not in OPEN and req.cookies.get(COOKIE) not in sessions:
            return JSONResponse({"error": "login required"}, status_code=401)
        return await call_next(req)

    @app.get("/")
    def home():
        return FileResponse(WEB / "home.html")

    @app.get("/login")
    def login_page(req: Request, next: str = "/app"):
        if req.cookies.get(COOKIE) in sessions:
            return RedirectResponse(next if next in ("/app", "/recordings") else "/app", status_code=303)
        return FileResponse(WEB / "login.html")

    def is_locked():
        if time.monotonic() < locked[0]:
            sec("block", "refused login: locked out after repeated failed logins", "locked")
            return JSONResponse({"error": "too many tries, wait"}, status_code=429)

    def refuse(why, key):
        fails[0] += 1
        sec("block", f"refused login: {why}", "login " + key)
        if fails[0] >= MAX_FAILS:
            fails[0], locked[0] = 0, time.monotonic() + LOCKOUT_S
            sec("block", f"login locked for {LOCKOUT_S} s after {MAX_FAILS} failed logins", "lockout")
        return JSONResponse({"error": why}, status_code=401)

    def unlock(req, how):
        fails[0] = 0
        sid = secrets.token_urlsafe(32)
        sessions.add(sid)
        sec("ok", f"control room unlocked by {how} (new session)")
        r = JSONResponse({"ok": True})
        r.set_cookie(COOKIE, sid, httponly=True, samesite="strict", path="/", secure=req.url.scheme == "https")
        return r

    async def body(req):
        try:
            return await req.json()
        except Exception:
            return None

    @app.post("/api/login")
    async def login(req: Request):
        if r := is_locked():
            return r
        if (b := await body(req)) is None:
            return JSONResponse({"error": "bad json"}, status_code=400)
        if not secrets.compare_digest(str(b.get("password", "")).encode(), password.encode()):
            return refuse("wrong password", "password")
        return unlock(req, "password")

    @app.get("/api/login/nonce")
    def wallet_nonce(req: Request):
        if not owners:
            return JSONResponse({"error": "wallet login disabled"}, status_code=403)
        now = time.time()
        for k in [k for k, (_, t) in nonces.items() if now - t > NONCE_S]:
            del nonces[k]
        if len(nonces) >= 100:
            return JSONResponse({"error": "busy, retry"}, status_code=429)
        n = secrets.token_hex(16)
        msg = (f"Sign in to the Skitter control room.\n\nURI: {req.base_url}\nChain ID: {CHAIN_ID}\nNonce: {n}\n"
               f"Issued At: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}\n\nNo transaction, no gas.")
        nonces[n] = (msg, now)
        return {"message": msg, "chainId": CHAIN_ID}

    @app.post("/api/login/wallet")
    async def wallet_login(req: Request):
        if r := is_locked():
            return r
        if (b := await body(req)) is None:
            return JSONResponse({"error": "bad json"}, status_code=400)
        msg, sig = str(b.get("message", "")), str(b.get("signature", ""))
        m = re.search(r"^Nonce: ([0-9a-f]{32})$", msg, re.M)
        issued = nonces.pop(m.group(1), None) if m else None  # single use: a replayed signature finds no nonce
        if not issued or issued[0] != msg or time.time() - issued[1] > NONCE_S:
            return refuse("unknown or expired wallet challenge", "challenge")
        try:
            addr = Account.recover_message(encode_defunct(text=msg), signature=sig)
        except Exception:
            return refuse("bad wallet signature", "signature")
        if addr.lower() not in owners:
            return refuse(f"wallet {addr[:10]}… is not an owner", "owner")
        return unlock(req, f"owner wallet {addr[:10]}…")

    @app.post("/api/logout")
    def logout(req: Request):
        sessions.discard(req.cookies.get(COOKIE))
        r = JSONResponse({"ok": True})
        r.delete_cookie(COOKIE, path="/")
        return r

    mark = '<meta charset="utf-8">'
    assert mark in (WEB / "index.html").read_text(encoding="utf-8"), \
        "index.html lost its charset meta: the control room would get no token"

    def private_page(name, url):
        def serve(req: Request):
            if req.cookies.get(COOKIE) not in sessions:
                return RedirectResponse(f"/login?next={url}", status_code=303)
            f = WEB / name
            html = f.read_text(encoding="utf-8") if f.exists() else ""
            if mark not in html:
                return HTMLResponse("page not available", status_code=404)
            html = html.replace(mark, f'{mark}\n<meta name="skitter-token" content="{token}">', 1)
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        app.get(url)(serve)

    private_page("index.html", "/app")
    private_page("recordings.html", "/recordings")

    app.mount("/pkg", StaticFiles(directory=ROBOT_PKG), name="pkg")  # URDF + meshes for the 3D landing page

    @app.get("/api/frame")
    def frame():
        return Response(rt.frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    def status():
        return {"state": rt.ex.state, "busy": rt.busy, "robot": rt.adapter.state(),
                "name": rt.adapter.manifest()["name"],
                "spent_usd": round(og.budget.spent(), 4), "cap_usd": og.budget.cap, "blocked": blocked[0]}

    @app.get("/api/events")
    def events(since: int = 0):
        ev, n = rt.events_since(since)
        return {"events": ev, "next": n}

    @app.post("/api/command")
    async def command(req: Request):
        try:
            text = (await req.json()).get("text", "").strip()
        except Exception:
            return JSONResponse({"error": "bad json"}, status_code=400)
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        rt.submit(text)
        return {"ok": True}

    @app.post("/api/voice")
    async def voice(req: Request):
        wav = await req.body()
        try:
            r = og.transcribe(wav)
        except OGError as e:  # no transcript = no decision: stop
            rt.estop()
            rt.emit({"t": time.time(), "type": "safe_stop", "reason": f"0G unavailable: {e}"})
            return JSONResponse({"error": str(e)}, status_code=502)
        rt.emit({"t": time.time(), "type": "stt", "text": r.value, "request_id": r.request_id,
                 "latency_s": round(r.latency_s, 2), "model": r.model, "trace": r.trace})
        if r.value:
            rt.submit(r.value)
        return {"text": r.value, "request_id": r.request_id}

    @app.post("/api/episode/end")
    def episode_end():
        """Finish the episode, upload the bundle to 0G Storage, start a fresh one."""
        if rt.busy:
            rt.estop()
        res = rt.episodes.end_and_upload()
        rt.emit({"t": time.time(), "type": "episode_uploaded", **{k: v for k, v in res.items() if k != "bundle"}})
        return res

    @app.get("/api/episode")
    def episode():
        return {"current": rt.episodes.current.id, "records": len(rt.episodes.current.records),
                "uploads": rt.episodes.uploads}

    @app.get("/api/feed")
    def feed_status():
        return {"on": bool(feed and feed.on), "patrol": rt.ex.state == "PATROL",
                "clips": list(reversed(feed.clips[-50:])) if feed else []}

    @app.post("/api/feed/{what}")
    def feed_toggle(what: str):
        if not feed or what not in ("start", "stop"):
            return JSONResponse({"error": "no feed" if not feed else "start or stop"}, status_code=400)
        feed.start() if what == "start" else feed.stop()
        return {"on": feed.on}

    @app.get("/api/feed/verify")
    def feed_verify(root: str):
        if not feed or not re.fullmatch(r"0x[0-9a-fA-F]{64}", root):
            return JSONResponse({"error": "bad root"}, status_code=400)
        return feed.verify(root)

    if feed:
        rt.on_patrol = feed.start  # a security round is always recorded, however it was started

    @app.post("/api/patrol")
    def patrol():
        rt.patrol()
        return {"ok": True}

    @app.post("/api/estop")
    def estop(req: Request):
        rt.estop()
        sec("warn", f"E-STOP from {urlparse(req.headers.get('origin') or 'http://localhost').hostname} (never locked)")
        return {"ok": True}

    return app
