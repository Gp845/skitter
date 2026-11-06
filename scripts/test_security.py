"""Security checks, no 0G calls: control API lock-down (CSRF, DNS rebinding, token) and episode sealing."""
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

from cortex.core import episode as ep  # noqa: E402
from cortex.core.server import create_app  # noqa: E402


class Runtime:  # just what the tested routes touch
    def __init__(self):
        self.commands, self.stops, self.events = [], 0, []

    def emit(self, e):
        self.events.append(e)

    def submit(self, text):
        self.commands.append(text)

    def estop(self):
        self.stops += 1


rt = Runtime()
PORT = 3917
server = uvicorn.Server(uvicorn.Config(create_app(rt, og=None), host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)


def req(path, method="GET", body=b"", **headers):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body if method == "POST" else None,
                               method=method, headers=headers)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


cmd = b'{"text": "walk to the stairs"}'
checks = {
    # a web page you visit can send a "simple" cross-site POST (text/plain) to localhost without any preflight
    "CSRF text/plain POST, no token": req("/api/command", "POST", cmd, **{"Content-Type": "text/plain",
                                                                         "Origin": "https://evil.example"})[0] == 403,
    "JSON POST without token": req("/api/command", "POST", cmd, **{"Content-Type": "application/json"})[0] == 403,
    "DNS rebinding (foreign Host)": req("/app", Host="evil.example:3917")[0] == 403,
}
status, page = req("/app")
token = re.search(r'name="skitter-token" content="([^"]+)"', page).group(1)
checks["token + foreign Origin"] = req("/api/command", "POST", cmd, **{"X-Skitter-Token": token,
                                                                     "Origin": "https://evil.example"})[0] == 403
checks["wrong token"] = req("/api/command", "POST", cmd, **{"X-Skitter-Token": token[:-1] + "x"})[0] == 403
checks["control room with token"] = req("/api/command", "POST", cmd, **{"X-Skitter-Token": token,
                                                                      "Origin": f"http://localhost:{PORT}"})[0] == 200
checks["E-STOP never locked"] = req("/api/estop", "POST", Origin="https://evil.example")[0] == 200
checks["only the real command got through"] = rt.commands == ["walk to the stairs"] and rt.stops == 1
log = [e["msg"] for e in rt.events if e["type"] == "security"]
checks["every refusal kind + E-STOP in the security log"] = all(
    any(s in m for m in log) for s in ("foreign Host", "no token", "wrong token", "foreign origin", "E-STOP"))
n = len(rt.events)
for _ in range(50):  # a hostile page hammering the API
    req("/api/command", "POST", cmd, **{"Content-Type": "application/json"})
checks["request flood does not flood the log"] = len(rt.events) == n

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
print("security OK")
