"""Surveillance: patrol the house while recording; clips must be hash-chained and complete, a person in view must be
detected. No 0G Compute calls. `--upload` also seals + uploads the last clip to 0G and verifies it end to end
(testnet gas only)."""
import hashlib
import json
import math
import os
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.core.feed import Feed  # noqa: E402
from cortex.core.runtime import Runtime  # noqa: E402

bot = HexapodAdapter(world="house")
rt = Runtime(bot, planner=None)
rt.start()
folder = tempfile.mkdtemp()
feed = Feed(rt, clip_s=10, fps=2, upload=False, folder=folder)
rt.patrol()
feed.start()
T = 150  # wall seconds; the sim runs at roughly half real time while walking (camera rendering)
route, reached, t_end = bot.patrol_route, set(), time.time() + T
t0 = time.time()
t0 = time.time()
while time.time() < t_end:
    time.sleep(0.25)
    if int(time.time() - t0) % 30 == 0 and time.time() - t0 > 1:
        print(f"  {time.time() - t0:.0f} s: sim at {bot.t / 240 / (time.time() - t0):.2f}x real time, pose {rt.pose}", flush=True)
        time.sleep(1)
    if int(time.time() - t0) % 30 == 0 and time.time() - t0 > 1:
        print(f"  {time.time() - t0:.0f} s: sim at {bot.t / 240 / (time.time() - t0):.2f}x real time, pose {rt.pose}", flush=True)
        time.sleep(1)
    for i, (x, y) in enumerate(route):
        if rt.pose and math.hypot(rt.pose["x"] - x, rt.pose["y"] - y) < 0.2:
            reached.add(i)
feed.stop()
rt.estop()
time.sleep(0.5)
events = [e for e in rt.events]
print(f"waypoints reached: {sorted(reached)}; final pose {rt.pose}; tilt {bot.state()['tilt_deg']:.1f} deg")
print("patrol log:", [e["msg"] for e in events if e["type"] == "patrol"])
assert len(reached) >= 2 and not bot.state()["fallen"], "patrol did not get around the room"

clips = feed.clips
assert len(clips) >= T // 10 - 1, f"expected ~{T // 10} ten-second clips, got {len(clips)}"
prev = None
for c in clips:
    data = open(os.path.join(folder, c["file"]), "rb").read()
    assert hashlib.sha256(data).hexdigest() == c["sha256"] and c["prev_sha256"] == prev, f"chain broken at {c['seq']}"
    with zipfile.ZipFile(os.path.join(folder, c["file"])) as z:
        meta = json.loads(z.read("clip.json"))
        assert meta["prev_sha256"] == prev
        for f in meta["frames"]:
            assert hashlib.sha256(z.read(f"frames/{f['sha256'][:16]}.jpg")).hexdigest() == f["sha256"]
    prev = c["sha256"]
frames = sum(c["frames"] for c in clips)
print(f"{len(clips)} clips, {frames} frames (~{frames / T:.1f} fps), hash chain intact, every frame hash matches")
assert frames >= T * 2 * 0.8

dets = sum(c["detections"] for c in clips)
print(f"person detections: {dets}", [e["msg"] for e in events if e["type"] == "alert"][:3])
assert dets >= 1, "the person in the living room was never detected"

if "--upload" in sys.argv:
    from cortex.core import episode as ep
    c = clips[-1]
    r = ep.upload(os.path.join(folder, c["file"]))
    reg = ep._node("register.mjs", r["rootHash"], f"skitter-feed-test/clip-{c['seq']:05d}", c["detections"], timeout=90)
    c.update(root=r["rootHash"])
    print("uploaded", r["rootHash"], "chain tx", reg.get("registryTx"))
    v = feed.verify(r["rootHash"])
    for ch in v["checks"]:
        print(f"  [{'PASS' if ch['ok'] else 'FAIL'}] {ch['name']}: {ch['detail']}")
    assert all(ch["ok"] for ch in v["checks"]) and len(v["images"]) >= 1
rt.close()
print("feed OK")
