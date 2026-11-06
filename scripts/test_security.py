"""Security checks, no 0G calls: control API lock-down (login, CSRF, DNS rebinding, token) and episode sealing."""
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import uvicorn  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402

from cortex.core import episode as ep  # noqa: E402
from cortex.core.server import COOKIE, MAX_FAILS, create_app  # noqa: E402


class Runtime:  # just what the tested routes touch
    def __init__(self):
        self.commands, self.stops, self.events = [], 0, []
        self.frame = b"\xff\xd8 not really a jpeg"
        self.ex = type("Executor", (), {"state": "IDLE"})()

    def emit(self, e):
        self.events.append(e)

    def submit(self, text):
        self.commands.append(text)

    def estop(self):
        self.stops += 1


rt = Runtime()
PORT = 3917
PASSWORD = "correct horse battery"
os.environ["SKITTER_PASSWORD"] = PASSWORD
OWNER, STRANGER = Account.create(), Account.create()
os.environ["OWNER_WALLETS"] = OWNER.address
server = uvicorn.Server(uvicorn.Config(create_app(rt, og=None), host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


open_ = urllib.request.build_opener(NoRedirect).open


def req(path, method="GET", body=b"", **headers):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body if method == "POST" else None,
                               method=method, headers=headers)
    for attempt in range(5):
        try:
            with open_(r) as resp:
                return resp.status, resp.read().decode(errors="replace"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace"), e.headers
        except ConnectionError:  # Windows: an early refusal closes the socket with the body unread (RST)
            if attempt == 4:
                raise


def login(pw, **headers):
    return req("/api/login", "POST", json.dumps({"password": pw}).encode(), **{"Content-Type": "application/json", **headers})


cmd = b'{"text": "walk to the stairs"}'
s, _, h = req("/app")
checks = {"/app without login redirects to /login": s == 303 and h["Location"] == "/login?next=/app"}
s, _, h = req("/recordings")
checks["/recordings without login redirects to /login"] = s == 303 and h["Location"] == "/login?next=/recordings"
checks["camera frame without login"] = req("/api/frame")[0] == 401
checks["status without login"] = req("/api/status")[0] == 401
checks["login page is public"] = req("/login")[0] == 200
checks["wrong password"] = login("guess")[0] == 401
checks["login from a foreign origin"] = login(PASSWORD, Origin="https://evil.example")[0] == 403
s, _, h = login(PASSWORD, Origin=f"http://localhost:{PORT}")
cookie = h.get("Set-Cookie", "")
checks["right password: HttpOnly, SameSite=Strict session cookie"] = (
    s == 200 and cookie.startswith(COOKIE + "=") and "httponly" in cookie.lower() and "samesite=strict" in cookie.lower())
me = {"Cookie": cookie.split(";")[0]}

# a web page you visit can send a "simple" cross-site POST (text/plain) to localhost without any preflight
checks["CSRF text/plain POST, no token"] = req("/api/command", "POST", cmd, **me, **{
    "Content-Type": "text/plain", "Origin": "https://evil.example"})[0] == 403
checks["JSON POST without token"] = req("/api/command", "POST", cmd, **me, **{"Content-Type": "application/json"})[0] == 403
checks["DNS rebinding (foreign Host)"] = req("/app", Host="evil.example:3917", **me)[0] == 403
status, page, _ = req("/app", **me)
token = re.search(r'name="skitter-token" content="([^"]+)"', page).group(1)
checks["/recordings with login is not redirected"] = req("/recordings", **me)[0] != 303
checks["token + foreign Origin"] = req("/api/command", "POST", cmd, **me, **{"X-Skitter-Token": token,
                                                                           "Origin": "https://evil.example"})[0] == 403
checks["wrong token"] = req("/api/command", "POST", cmd, **me, **{"X-Skitter-Token": token[:-1] + "x"})[0] == 403
checks["token without login"] = req("/api/command", "POST", cmd, **{"X-Skitter-Token": token})[0] == 401
checks["feed with token, without login"] = req("/api/feed", **{"X-Skitter-Token": token})[0] == 401
checks["feed with token + login"] = req("/api/feed", **me, **{"X-Skitter-Token": token})[0] == 200
checks["camera frame with login"] = req("/api/frame", **me)[:2] == (200, rt.frame.decode(errors="replace"))
checks["control room with token"] = req("/api/command", "POST", cmd, **me, **{"X-Skitter-Token": token,
                                                                            "Origin": f"http://localhost:{PORT}"})[0] == 200
checks["E-STOP never locked (no login, no token)"] = req("/api/estop", "POST", Origin="https://evil.example")[0] == 200
checks["only the real command got through"] = rt.commands == ["walk to the stairs"] and rt.stops == 1
n = len(rt.events)
for _ in range(50):  # a hostile page hammering the API
    req("/api/command", "POST", cmd, **{"Content-Type": "application/json"})
checks["request flood does not flood the log"] = len(rt.events) == n

checks["logout needs the token"] = req("/api/logout", "POST", **me)[0] == 403
checks["logout"] = req("/api/logout", "POST", **me, **{"X-Skitter-Token": token})[0] == 200
checks["session dead after logout"] = req("/api/frame", **me)[0] == 401 and req("/app", **me)[0] == 303
checks["E-STOP after logout"] = req("/api/estop", "POST")[0] == 200 and rt.stops == 2



def wallet_login(acct, edit=lambda m: m, sig=None):
    msg = json.loads(req("/api/login/nonce")[1])["message"]
    sig = sig or acct.sign_message(encode_defunct(text=msg)).signature.hex()
    body = json.dumps({"message": edit(msg), "signature": sig}).encode()
    return req("/api/login/wallet", "POST", body, **{"Content-Type": "application/json"}), msg, sig


(s, _, h), msg, sig = wallet_login(OWNER)
checks["owner wallet signature unlocks"] = s == 200 and h.get("Set-Cookie", "").startswith(COOKIE + "=")
checks["wallet session opens /app"] = req("/app", Cookie=h["Set-Cookie"].split(";")[0])[0] == 200
replay = json.dumps({"message": msg, "signature": sig}).encode()
checks["replayed wallet signature"] = req("/api/login/wallet", "POST", replay,
                                          **{"Content-Type": "application/json"})[0] == 401
checks["non-owner wallet"] = wallet_login(STRANGER)[0][0] == 401
checks["owner signature on an edited message"] = wallet_login(
    OWNER, lambda m: m.replace("Skitter", "Skittr"))[0][0] == 401
login(PASSWORD)  # a good login resets the fail count the 3 wallet refusals above built up

tries = [login("guess")[0] for _ in range(MAX_FAILS)]
checks[f"lockout: {MAX_FAILS} wrong, then even the right password is refused"] = (
    tries == [401] * MAX_FAILS and login(PASSWORD)[0] == 429)
log = [e["msg"] for e in rt.events if e["type"] == "security"]
checks["every refusal kind, login, lockout + E-STOP in the security log"] = all(
    any(s in m for m in log) for s in ("foreign Host", "no token", "wrong token", "foreign origin", "E-STOP",
                                       "wrong password", "unlocked", "login locked", "not an owner",
                                       "unlocked by owner wallet"))

# sealing: round trip, tamper detection, wrong key, no key = no upload
os.environ["EPISODE_KEY"] = "11" * 32
d = pathlib.Path(tempfile.mkdtemp())
(d / "e.zip").write_bytes(b"camera frames from someone's living room" * 50)
ep._node("seal.mjs", "encrypt", d / "e.zip", d / "e.sealed")
sealed = (d / "e.sealed").read_bytes()
checks["sealed bytes hide the content"] = b"living room" not in sealed and sealed[:4] == b"SKT1"
checks["owner can unseal"] = ep.unseal(d / "e.sealed", d / "back.zip") and (d / "back.zip").read_bytes() == (d / "e.zip").read_bytes()


def fails(fn):
    try:
        fn()
        return False
    except Exception:
        return True


(d / "t.sealed").write_bytes(sealed[:40] + bytes([sealed[40] ^ 1]) + sealed[41:])
checks["tampered bundle rejected"] = fails(lambda: ep.unseal(d / "t.sealed", d / "t.zip"))
os.environ["EPISODE_KEY"] = "22" * 32
checks["wrong key rejected"] = fails(lambda: ep.unseal(d / "e.sealed", d / "w.zip"))
os.environ["EPISODE_KEY"] = "unset"  # not "": on Windows that deletes the var and .env would supply the real key
checks["no key = no upload"] = fails(lambda: ep.upload(d / "e.zip"))

for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
server.should_exit = True
assert all(checks.values()), "security checks failed"
print(f"security OK ({len(checks)}/{len(checks)})")
