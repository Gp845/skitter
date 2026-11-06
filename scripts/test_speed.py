"""set_speed on flat ground, no 0G calls: steady walking speed, foot slip (speed vs. what the stride and step rate
should give), sideways drift, tilt, and 90 deg turns at 50..300 %. 300 % is the cap (faster slips and overshoots)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.adapters.hexapod_pybullet.gait import SPEEDS  # noqa: E402


def run(bot, action, params, timeout=60):
    tilt = 0.0
    bot.execute(action, params, timeout)
    while bot.busy():
        bot.step(0.05)
        tilt = max(tilt, bot.state()["tilt_deg"])
    return bot.result(), tilt


speeds = []
for pct in (50, 100, 200, SPEEDS[1], 400):
    bot = HexapodAdapter(arena=False)
    res, _ = run(bot, "set_speed", {"percent": pct})
    assert bot.state()["speed_percent"] == min(pct, SPEEDS[1]), res
    bot.execute("walk", dict(direction="forward", distance_cm=150), 60)
    tilt, mark = 0.0, None
    while bot.busy():
        bot.step(0.05)
        s = bot.state()
        tilt = max(tilt, s["tilt_deg"])
        mark = mark or (s["x"] > 0.3 and (s["x"], s["step"]))
    v = (s["x"] - mark[0]) / ((s["step"] - mark[1]) / 240)
    slip = 1 - v / (0.12 * bot.gait.rate * bot.gait.stride)  # 2 x 5 cm stance strokes per 200-step cycle
    out = (f"{pct:3d}% ({res}): {v * 100:4.1f} cm/s, slip {slip:3.0%}, drift {s['y'] * 100:+.1f} cm over 1.5 m, "
           f"tilt {tilt:.1f} deg, turns")
    for d in ("left", "right"):
        y0 = bot.state()["yaw"]
        _, t = run(bot, "turn", dict(direction=d, degrees=90))
        bot.step(2.0)
        tilt = max(tilt, t)
        out += f" {math.degrees(math.atan2(math.sin(bot.state()['yaw'] - y0), math.cos(bot.state()['yaw'] - y0))):+.0f}"
    print(out, flush=True)
    assert tilt < 5 and slip < 0.25 and abs(s["y"]) < 0.1 and not bot.state()["fallen"], f"{pct}% unstable"
    speeds.append(v)
    bot.close()
assert speeds[:4] == sorted(speeds[:4]) and abs(speeds[4] - speeds[3]) < 0.01, "faster each step; 400 clamps to 300"
print(f"top speed {speeds[3] * 100:.1f} cm/s at {SPEEDS[1]}%\nspeed OK")
