"""Closed-loop skills on the hexapod, no 0G calls. Boxes below are what qwen3-vl-plus returned for the start frame."""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402

RED = dict(label="red box", x1=420, y1=360, x2=580, y2=520)
BLUE = dict(label="blue box", x1=0, y1=350, x2=125, y2=500)


def run(bot, action, params, timeout=40):
    a, t0 = bot.state(), time.time()
    bot.execute(action, params, timeout)
    while bot.busy():
        bot.step(0.05)
    b = bot.state()
    dyaw = math.degrees((b["yaw"] - a["yaw"] + math.pi) % (2 * math.pi) - math.pi)
    print(f"{action:9s} {str(params)[:60]:60s} -> {bot.result():8s} x={b['x']:+.2f} y={b['y']:+.2f} "
          f"yaw={math.degrees(b['yaw']):+6.1f} (dyaw {dyaw:+.0f}) sim={(b['step'] - a['step']) / 240:.1f}s wall={time.time() - t0:.1f}s")
    return bot.result()


bot = HexapodAdapter()
# locate(): boxes gemini returned for the start frame -> world position (box centres: red 1.0,0  green 0.8,-0.6)
for label, box, true in [("red", dict(x1=421, y1=378, x2=578, y2=583), (0.9, 0.0)),
                         ("green", dict(x1=874, y1=370, x2=1000, y2=639), (0.8, -0.5))]:  # front face / visible part
    x, y = bot.locate(box)
    print(f"locate {label:5s} -> ({x:+.2f}, {y:+.2f})  expected about {true}")
    assert math.dist((x, y), true) < 0.15, label
run(bot, "turn", dict(direction="left", degrees=90))
run(bot, "turn", dict(direction="right", degrees=90))
run(bot, "walk", dict(direction="forward", distance_cm=30))
run(bot, "walk", dict(direction="backward", distance_cm=30))
bot.close()

bot = HexapodAdapter()
print("-- face the blue box (it is far left), then approach the red box")
bot.execute("turn", dict(direction="right", degrees=10), 10)  # tiny nudge so start isn't exactly symmetric
while bot.busy():
    bot.step(0.05)
bot2 = bot
run(bot2, "face", {**BLUE}, 40)
bot.close()

bot = HexapodAdapter()
print("-- follow the walking person for 60 s (box from colour here; the VLM gives it live)")
import cv2  # noqa: E402
from cortex.adapters.hexapod_pybullet.arena import PERSON_PATH  # noqa: E402
run(bot, "turn", dict(direction="left", degrees=80))
bgr, _ = bot.render()
hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)  # yellow shirt | purple trousers
mask = cv2.inRange(hsv, (20, 150, 100), (35, 255, 255)) | cv2.inRange(hsv, (135, 100, 50), (160, 255, 255))
ys, xs = mask.nonzero()
assert len(xs) > 50, "person not in view"
Hh, Ww = mask.shape
box = dict(label="person", x1=int(xs.min() * 1000 / Ww), x2=int(xs.max() * 1000 / Ww),
           y1=int(ys.min() * 1000 / Hh), y2=int(ys.max() * 1000 / Hh))
bot.execute("follow", box, 60)
gaps = []
while bot.busy():
    bot.step(0.05)
    if bot.t % 240 == 0:
        person = __import__("pybullet").getBasePositionAndOrientation(bot.objects["person"][0][0])[0]
        gaps.append(math.dist((bot.state()["x"], bot.state()["y"]), person[:2]))
s = bot.state()
moved = math.hypot(s["x"], s["y"])
print(f"follow -> {bot.result()} robot moved {moved:.2f} m; gap to person (centres): start {gaps[0]:.2f} m, "
      f"min {min(gaps):.2f}, last {gaps[-1]:.2f}")
assert bot.result() == "timeout" and moved > 0.2 and min(gaps) > 0.5 and gaps[-1] < 1.1, "follow failed"
bot.close()

bot = HexapodAdapter()
print("-- approach the red box (dead ahead, ~0.9 m)")
run(bot, "approach", {**RED}, 40)
s = bot.state()
print("final state: x=%.2f (red box face is at x=0.90), tilt=%.1f" % (s["x"], s["tilt_deg"]))
bot.close()
