"""Headless check: does each command move/turn the hexapod the right way without falling?"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402

bot = HexapodAdapter(gui="--gui" in sys.argv, arena=False)
s0 = bot.state()
print("settled:", {k: round(v, 3) if isinstance(v, float) else v for k, v in s0.items()})

for direction in ["forward", "backward", "turn_left", "turn_right"]:
    a = bot.state()
    for _ in range(4):  # 4 x 3 s
        bot.execute("move", {"direction": direction, "speed": 1.0, "duration_s": 3.0}, 3.0)
        bot.step(3.0)
    bot.stop()
    bot.step(1.0)
    b = bot.state()
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    # displacement in the robot's own initial-heading frame
    fwd = dx * math.cos(a["yaw"]) + dy * math.sin(a["yaw"])
    left = -dx * math.sin(a["yaw"]) + dy * math.cos(a["yaw"])
    dyaw = math.degrees((b["yaw"] - a["yaw"] + math.pi) % (2 * math.pi) - math.pi)
    print(f"{direction:10s} fwd={fwd:+.3f}m left={left:+.3f}m dyaw={dyaw:+6.1f}deg "
          f"tilt={b['tilt_deg']:.1f} z={b['z']:.3f} fallen={b['fallen']}")
bot.close()
