"""Watch the stair gait in a PyBullet window: turn to the stairs, climb 2 x 6 cm on 26 cm treads: too shallow head-on, so it turns side-on, tucks its legs and crabs up, 26 cm wide (narrow stance). No 0G calls.

  python scripts/demo_stairs.py [--speed 1.0]
"""
import argparse
import os
import sys
import time

import pybullet as p

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--speed", type=float, default=1.0, help="playback speed (2 = twice real time)")
speed = ap.parse_args().speed

bot = HexapodAdapter(gui=True)
p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
p.configureDebugVisualizer(p.COV_ENABLE_MOUSE_PICKING, 0)  # a click in the window would otherwise drag the robot


def run(action, params):
    print(f"{action} {params}")
    bot.execute(action, params, 120)
    while bot.busy() and p.isConnected(bot.cid):
        t0 = time.time()
        bot.step(1 / 30)
        s = bot.state()
        p.resetDebugVisualizerCamera(0.9, 60, -18, [s["x"], s["y"], s["z"]])  # side view, follows the body
        time.sleep(max(0, 1 / 30 / speed - (time.time() - t0)))
    s = bot.state()
    print(f"  -> {bot.result()}  x={s['x']:.2f}  z={s['z']:.3f}  tilt={s['tilt_deg']:.1f} deg")


run("turn", dict(direction="left", degrees=180))
run("climb", dict(step_height_cm=6, steps=2, tread_cm=26, width_cm=70))
print("done; close the window to exit")
while p.isConnected(bot.cid):
    time.sleep(0.2)
