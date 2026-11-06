"""Send one typed command to a running Cortex server and print the decision trail. Usage: live_cmd.py "walk forward" """
import json
import sys
import time
import urllib.request


def call(path, body=None):
    req = urllib.request.Request("http://localhost:3000" + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req, timeout=30))


s0 = call("/api/status")
print(call("/api/command", {"text": sys.argv[1]}))
since, t0 = 0, time.time()
while time.time() - t0 < 90:
    time.sleep(1)
    d = call(f"/api/events?since={since}")
    since = d["next"]
    for e in d["events"]:
        print(f"{e['t'] - t0:5.1f}s", e["type"], e.get("name", ""), e.get("args", ""), e.get("request_id", "")[:8],
              e.get("latency_s", ""), e.get("reason", ""), e.get("outcome", ""))
    st = call("/api/status")
    if not st["busy"] and since > 1:
        break
a, b = s0["robot"], st["robot"]
print("moved dx=%.2f dy=%.2f dyaw=%.0fdeg tilt=%.1f | spent $%.4f" % (
    b["x"] - a["x"], b["y"] - a["y"], (b["yaw"] - a["yaw"]) * 57.3, b["tilt_deg"], st["spent_usd"]))
