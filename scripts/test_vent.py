"""Crouching through the house vent, no 0G calls.

1. Skitter's real height (highest point of any link) standing, walking and crawling on flat ground.
2. In the house, lined up 0.6 m before the bathroom vent (16 cm high): walk stops 'blocked' at the mouth without
   touching it; crawl goes all the way through the 1 m duct (with a mid-duct turn) without getting stuck or tipping.
"""
import math
import os
import sys

import pybullet as p

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet import house  # noqa: E402
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402


def top(bot):
    return max(p.getAABB(bot.robot, j)[1][2] for j in range(-1, p.getNumJoints(bot.robot)))


def run(bot, action, params, timeout=60):
    hi = tilt = 0.0
    bot.execute(action, params, timeout)
    while bot.busy():
        bot.step(0.05)
        hi, tilt = max(hi, top(bot)), max(tilt, bot.state()["tilt_deg"])
    return bot.result(), hi, tilt


bot = HexapodAdapter(arena=False)
stand = top(bot)
_, walk_top, _ = run(bot, "walk", dict(direction="forward", distance_cm=50))
bot.step(1.0)  # crouch settles over ~0.2 s
_, crawl_top, _ = run(bot, "crawl", dict(direction="forward", distance_cm=50))
print(f"height: standing {stand * 100:.1f} cm, walking {walk_top * 100:.1f} cm, crawling {crawl_top * 100:.1f} cm; "
      f"vent {house.VENT_H * 100:.0f} cm")
assert crawl_top + 0.01 < house.VENT_H < walk_top, "the vent must need a crouch, and a crouch must fit"
bot.close()


def at_vent():
    bot = HexapodAdapter(world="house")
    p.resetBasePositionAndOrientation(bot.robot, [4.5, 4.5, 0.08], p.getQuaternionFromEuler((0, 0, math.pi)))
    bot._settle()
    return bot


duct = None
bot = at_vent()
duct = set(bot.objects["vent duct"] + bot.objects["walls"])
touch = lambda: any(c[2] in duct for c in p.getContactPoints(bot.robot))
res, hi, tilt = run(bot, "walk", dict(direction="forward", distance_cm=200))
s = bot.state()
print(f"walk into the vent -> {res} at x={s['x']:.2f} (duct mouth x=3.90), touching it: {touch()}, tilt {tilt:.1f}")
assert res == "blocked" and s["x"] > 3.9 and not touch() and not s["fallen"]
bot.close()

bot = at_vent()
duct = set(bot.objects["vent duct"] + bot.objects["walls"])
hits, legs = 0, [("crawl", dict(direction="forward", distance_cm=110)), ("turn", dict(direction="right", degrees=10)),
                 ("turn", dict(direction="left", degrees=10)), ("crawl", dict(direction="forward", distance_cm=130))]
for action, params in legs:
    bot.execute(action, params, 60)
    while bot.busy():
        bot.step(0.05)
        hits += touch()
    s = bot.state()
    print(f"  {action} {params} -> {bot.result()} x={s['x']:.2f} y={s['y']:.2f} z={s['z']:.3f} crouched={s['crouched']} "
          f"tilt {s['tilt_deg']:.1f}")
    assert bot.result() == "reached" and not s["fallen"], action
print(f"crawl through: now x={s['x']:.2f} (duct x 2.9..3.9), room {house.room_at(s['x'], s['y'])}, "
      f"touching the duct in {hits} checks (every 0.05 s)")
assert s["x"] < 2.5 and house.room_at(s["x"], s["y"]) == "bathroom"
bot.execute("walk", dict(direction="forward", distance_cm=10), 30)
assert not bot.state()["crouched"], "walk stands back up"
bot.close()
print("vent OK")
