"""Stair gaits, no 0G calls.

1. Arena stairs (2 x 6 cm, 26 cm treads, 70 cm wide): too shallow head-on, so Skitter turns side-on, tucks its
   legs to fit one tread and crabs up.
2. Narrow stairs (2 x 6 cm, 55 cm treads, 26 cm wide): head-on in a narrow stance.
3. Refusals (wrong height, too narrow, too steep) and a single 6 cm step.
4. Down: up the narrow stairs onto a wide landing, turn round, back down them (direction="down").
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.adapters.hexapod_pybullet.arena import _box  # noqa: E402

STAIRS = dict(step_height_cm=6, steps=2, tread_cm=26, width_cm=70)
NARROW = dict(step_height_cm=6, steps=2, tread_cm=55, width_cm=26)


def face_stairs():
    bot = HexapodAdapter()
    bot.execute("turn", dict(direction="left", degrees=180), 60)
    while bot.busy():
        bot.step(0.05)
    return bot


def narrow_world():  # riser at x = 0.6 straight ahead of the spawn
    bot = HexapodAdapter(arena=False)
    _box([0.875, 0.0, 0.03], [0.275, 0.13, 0.03], [0.75, 0.55, 0.35, 1])
    _box([1.5, 0.0, 0.06], [0.35, 0.13, 0.06], [0.65, 0.45, 0.28, 1])
    return bot


def climb(bot, params, name, close=True):
    z0, max_tilt = bot.state()["z"], 0.0
    bot.execute("climb", params, 120)
    while bot.busy():
        bot.step(0.05)
        max_tilt = max(max_tilt, bot.state()["tilt_deg"])
    s = bot.state()
    rise = s["z"] - z0
    print(f"{name} -> {bot.result()}  x={s['x']:.2f} y={s['y']:.2f}  body rose {rise * 100:.1f} cm (stairs 12 cm)  "
          f"max tilt {max_tilt:.1f} deg")
    assert bot.result() == "reached" and abs(rise - 0.12) < 0.015 and max_tilt < 20, f"{name} failed"
    if close:
        bot.close()
    return s


s = climb(face_stairs(), STAIRS, "26 cm treads, sideways")
assert s["x"] < -0.95, "must end on the landing (starts at x = -0.86)"
s = climb(narrow_world(), NARROW, "26 cm wide, narrow stance")
assert s["x"] > 1.3, "must end on the landing (starts at x = 1.15)"


def refuse(bot, params, want, why):
    bot.execute("climb", params, 120)
    print(f"{params} -> {bot.result()}")
    assert bot.result().startswith(want), why
    bot.close()


refuse(face_stairs(), dict(STAIRS, step_height_cm=4), "height_mismatch", "must refuse a riser that doesn't match")
refuse(face_stairs(), dict(STAIRS, tread_cm=20), "too_steep", "feet can't fit one 20 cm tread even tucked")
refuse(face_stairs(), dict(STAIRS, width_cm=50), "too_steep", "side-on needs stairs wider than the 52 cm body")
refuse(narrow_world(), dict(NARROW, width_cm=20), "too_narrow", "must refuse stairs narrower than the tucked stance")

bot = HexapodAdapter(arena=False)
_box([1.1, 0.0, 0.03], [0.5, 0.5, 0.03], [0.7, 0.5, 0.3, 1])  # one 6 cm step (the max), riser at x = 0.6
z0 = bot.state()["z"]
bot.execute("climb", dict(step_height_cm=6, steps=1, tread_cm=60), 120)
while bot.busy():
    bot.step(0.05)
s = bot.state()
print(f"6 cm step -> {bot.result()}  x={s['x']:.2f}  body rose {(s['z'] - z0) * 100:.1f} cm  tilt {s['tilt_deg']:.1f}")
assert bot.result() == "reached" and abs(s["z"] - z0 - 0.06) < 0.015, "6 cm step failed"
bot.close()

bot = HexapodAdapter(arena=False)  # down: narrow stairs up onto a wide landing, turn round, come back down them
_box([0.875, 0.0, 0.03], [0.275, 0.13, 0.03], [0.75, 0.55, 0.35, 1])
_box([1.95, 0.0, 0.06], [0.8, 0.8, 0.06], [0.65, 0.45, 0.28, 1])
climb(bot, NARROW, "26 cm wide, up onto a wide landing", close=False)
for action, params in [("walk", dict(direction="forward", distance_cm=45)), ("turn", dict(direction="left", degrees=180))]:
    bot.execute(action, params, 60)
    while bot.busy():
        bot.step(0.05)
s = bot.state()
print(f"on the landing facing back: x={s['x']:.2f} y={s['y']:.2f} yaw={math.degrees(s['yaw']):.0f}")
z0, max_tilt = s["z"], 0.0
bot.execute("climb", dict(NARROW, direction="down"), 120)
while bot.busy():
    bot.step(0.05)
    max_tilt = max(max_tilt, bot.state()["tilt_deg"])
s = bot.state()
print(f"down -> {bot.result()}  x={s['x']:.2f} y={s['y']:.2f}  body dropped {(z0 - s['z']) * 100:.1f} cm  max tilt {max_tilt:.1f}")
assert bot.result() == "reached" and abs(z0 - s["z"] - 0.12) < 0.015 and max_tilt < 20 and s["x"] < 0.45, "descent failed"
bot.close()
print("stairs OK")
