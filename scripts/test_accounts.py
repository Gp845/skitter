"""Hub checks, no 0G spend: wallet sign-up, sealed account secrets, one robot process per account (real run.py, arena),
isolation between accounts, MAX_ROBOTS, idle stop, E-STOP and CSRF/Host/Origin through the hub."""
import json
import os
import pathlib
import re
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = pathlib.Path(tempfile.mkdtemp())
DEAD = "http://127.0.0.1:9"  # any 0G / chain call fails here instead of spending
OWNER_KEY = "0x" + secrets.token_hex(32)
os.environ.update({  # set before cortex imports: load_dotenv must not fill these from the owner's .env
    "ACCOUNTS_KEY": secrets.token_hex(32), "SKITTER_ACCOUNTS": str(TMP / "accounts"), "SKITTER_HOME": str(TMP / "owner"),
    "OG_API_KEY": "none", "OG_BASE_URL": DEAD + "/v1", "STORAGE_RPC": DEAD, "STORAGE_INDEXER": DEAD,
    "STORAGE_PRIVATE_KEY": OWNER_KEY, "EPISODE_KEY": "33" * 32, "SKITTER_PASSWORD": "hub owner pw",
    "OWNER_WALLETS": "0x000000000000000000000000000000000000dEaD", "SKITTER_HOSTS": "skitter.invalid"})
import uvicorn  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402

from cortex.core import server  # noqa: E402
from cortex.core.accounts import Hub  # noqa: E402

PORT = 3927
hub = Hub(max_robots=2, idle_s=3600, world="arena")
srv = uvicorn.Server(uvicorn.Config(server.create_app(None, None, hub=hub), host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
while not srv.started:
    time.sleep(0.05)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


open_ = urllib.request.build_opener(NoRedirect).open


def req(path, method="GET", body=b"", port=PORT, **headers):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body if method == "POST" else None,
                               method=method, headers=headers)
    for attempt in range(5):
        try:
            with open_(r, timeout=60) as resp:
                return resp.status, resp.read().decode(errors="replace"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace"), e.headers
        except ConnectionError:  # Windows: an early refusal closes the socket with the body unread (RST)
            if attempt == 4:
                raise


JSON = {"Content-Type": "application/json"}


def post(path, data, **h):
    return req(path, "POST", json.dumps(data).encode(), **{**JSON, **h})


def wallet_login(acct):
    msg = json.loads(req("/api/login/nonce")[1])["message"]
    sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
    s, _, h = post("/api/login/wallet", {"message": msg, "signature": sig})
    return s, {"Cookie": h.get("Set-Cookie", "").split(";")[0]}


def open_app(me, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        s, page, _ = req("/app", **me)
        if s == 200:
            return re.search(r'name="skitter-token" content="([^"]+)"', page).group(1)
        assert s == 503 and "Starting" in page, (s, page[:200])
        time.sleep(1)
    raise TimeoutError("robot did not start")


def events(me):
    return json.loads(req("/api/events", **me)[1])["events"]


checks = {}
checks["landing page public"] = req("/")[0] == 200
s, _, h = req("/account")
checks["/account without login redirects"] = s == 303 and h["Location"] == "/login?next=/account"
checks["/app without login redirects"] = req("/app")[0] == 303
checks["proxied API without login"] = req("/api/status")[0] == 401

A, B, C = Account.create(), Account.create(), Account.create()
s, me_a = wallet_login(A)
a = A.address.lower()
f = TMP / "accounts" / a / "account.json"
checks["open sign-up: any wallet gets an account"] = s == 200 and f.exists()
info = json.loads(req("/api/account", **me_a)[1])
robot_key = hub._secrets(a)["robot_key"]
checks["account has a generated robot wallet"] = (info["robot_wallet"] == Account.from_key(robot_key).address
                                                  and info["robot_wallet"].lower() != a and not info["has_key"])
checks["robot private key encrypted on disk"] = robot_key[2:] not in f.read_text() and "robot_key" not in f.read_text()
_, page, _ = req("/account", **me_a)
tok_a = re.search(r'name="skitter-token" content="([^"]+)"', page).group(1)
API_KEY = "sk-test-SECRETVALUE-1234"
checks["setting the API key needs the token"] = post("/api/account/key", {"key": API_KEY}, **me_a)[0] == 403
s, body, _ = post("/api/account/key", {"key": API_KEY}, **me_a, **{"X-Skitter-Token": tok_a})
checks["API key saved, only last 4 shown"] = s == 200 and json.loads(body)["key_hint"] == "…1234" and "SECRET" not in body
checks["API key encrypted on disk"] = "SECRETVALUE" not in f.read_text() and hub._secrets(a)["og_api_key"] == API_KEY
info_txt = req("/api/account", **me_a)[1]
checks["API key + private key never returned"] = ("SECRETVALUE" not in info_txt and robot_key[2:] not in info_txt
                                                  and json.loads(info_txt)["has_key"])

s, me_b = wallet_login(B)
b = B.address.lower()
feed_b = TMP / "accounts" / b / "feed"
feed_b.mkdir(parents=True)
(feed_b / "index.json").write_text(json.dumps([{"seq": 0, "file": "clip-00000.zip", "start": 1, "end": 2, "frames": 1,
                                                "detections": 0, "sha256": "ab" * 32, "prev_sha256": None,
                                                "status": "local"}]))
t0 = time.time()
tok_a2, tok_b = open_app(me_a), open_app(me_b)
print(f"two robots up in {time.time() - t0:.0f} s")
ra, rb = hub.robots[a], hub.robots[b]
checks["two accounts: two robot processes, ports, homes"] = (ra.proc.pid != rb.proc.pid and ra.port != rb.port
                                                             and hub.home(a) != hub.home(b))
checks["control room token is the account's"] = tok_a2 == tok_a and tok_b != tok_a
sa, sb = (json.loads(req("/api/status", **m)[1]) for m in (me_a, me_b))
checks["each cookie reaches its own robot"] = sa["account"] == a and sb["account"] == b
checks["robot without a key says so"] = sa["has_key"] and not sb["has_key"]
fa = json.loads(req("/api/feed", **me_a, **{"X-Skitter-Token": tok_a})[1])
fb = json.loads(req("/api/feed", **me_b, **{"X-Skitter-Token": tok_b})[1])
checks["recordings are per account"] = fa["clips"] == [] and len(fb["clips"]) == 1
checks["A's cookie with B's token refused"] = req("/api/feed", **me_a, **{"X-Skitter-Token": tok_b})[0] == 403
checks["robot port refuses direct access"] = req("/api/status", port=rb.port)[0] == 403 and \
    req("/app", port=rb.port, **{"X-Skitter-Robot": "guess"})[0] == 403
checks["robot has no login of its own"] = post("/api/login", {"password": "hub owner pw"}, port=rb.port,
                                               **{"X-Skitter-Robot": rb.secret})[0] == 404

cmd = {"text": "walk to the red box"}
checks["hub: command without token"] = post("/api/command", cmd, **me_a)[0] == 403
checks["hub: token + foreign Origin"] = post("/api/command", cmd, **me_a, **{"X-Skitter-Token": tok_a,
                                                                           "Origin": "https://evil.example"})[0] == 403
checks["hub: CSRF text/plain"] = req("/api/command", "POST", b'{"text":"x"}', **me_a, **{
    "Content-Type": "text/plain", "Origin": "https://evil.example"})[0] == 403
checks["hub: foreign Host (DNS rebinding)"] = req("/app", Host="evil.example", **me_a)[0] == 403
checks["hub: token without session"] = post("/api/command", cmd, **{"X-Skitter-Token": tok_a})[0] in (401, 403)
s = post("/api/command", cmd, **me_b, **{"X-Skitter-Token": tok_b, "Origin": f"http://localhost:{PORT}"})[0]
end = time.time() + 20
while time.time() < end and not any("API key" in json.dumps(e) for e in events(me_b)):
    time.sleep(0.5)
checks["no key: model command refused locally (safe stop)"] = s == 200 and any("API key" in json.dumps(e) for e in events(me_b))
checks["E-STOP through the hub, no token"] = post("/api/estop", {}, **me_a, Origin="https://evil.example")[0] == 200
time.sleep(0.5)
checks["E-STOP reached A's robot"] = any(e.get("type") == "security" and "E-STOP" in e["msg"] for e in events(me_a))
checks["E-STOP without a session does not stop other robots"] = post("/api/estop", {})[0] == 401

s, me_c = wallet_login(C)
s, page, _ = req("/app", **me_c)
checks["MAX_ROBOTS: third robot refused politely"] = s == 503 and "in use" in page
ra.last = 0
hub.reap()
checks["idle robot stopped"] = a not in hub.robots and ra.proc.poll() is not None
checks["account page shows it stopped"] = json.loads(req("/api/account", **me_a)[1])["robot"] == "stopped"
s, page, _ = req("/app", **me_c)
checks["freed slot starts the next robot"] = s == 503 and "Starting" in page

s, _, h = post("/api/login", {"password": "hub owner pw"})
me_o = {"Cookie": h.get("Set-Cookie", "").split(";")[0]}
oi = json.loads(req("/api/account", **me_o)[1])
checks["password login = owner account with .env keys"] = oi["owner"] and oi["robot_wallet"] == Account.from_key(OWNER_KEY).address
checks["owner key comes from .env only"] = post("/api/account/key", {"key": API_KEY}, **me_o,
                                                **{"X-Skitter-Token": hub.token("owner")})[0] == 400
server.SESSION_S, keep = 0, server.SESSION_S
checks["expired session refused"] = req("/api/account", **me_b)[0] == 401
server.SESSION_S = keep
checks["logout"] = post("/api/logout", {}, **me_b, **{"X-Skitter-Token": tok_b})[0] == 200 and \
    req("/api/account", **me_b)[0] == 401
checks["no 0G spend: no ledger in any home"] = not list(TMP.rglob("spend.json"))
os.environ["ACCOUNTS_KEY"], good = secrets.token_hex(32), os.environ["ACCOUNTS_KEY"]
try:
    Hub(max_robots=1)
    checks["wrong ACCOUNTS_KEY fails closed"] = False
except SystemExit:
    checks["wrong ACCOUNTS_KEY fails closed"] = True
os.environ["ACCOUNTS_KEY"] = good

hub.close()
srv.should_exit = True
for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
assert all(checks.values()), "account checks failed"
print(f"accounts OK ({len(checks)}/{len(checks)})")
