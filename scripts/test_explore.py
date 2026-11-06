"""Drive house.EXPLORE with the real skills, no 0G calls: every room on both levels, through the vent, up the
three narrow flights to the loft and back down. goto/crawl = turn + walk legs like runtime._patrol (15 cm
tolerance); climb turns to the leg's yaw first if it is off by > 8 deg, like executor.climb.
`--speed 150` sets the walking speed first."""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.adapters.hexapod_pybullet.house import EXPLORE, ROOMS, room_at  # noqa: E402

bot, max_tilt = HexapodAdapter(world="house"), 0.0


def run(action, params, timeout):
    global max_tilt
    bot.execute(action, params, timeout)
    while bot.busy():
        bot.step(0.05)
        max_tilt = max(max_tilt, bot.state()["tilt_deg"])
    if "--verbose" in sys.argv:
        s = bot.state()
        print(f"     {action} {params} -> {bot.result()} at ({s['x']:+.2f}, {s['y']:+.2f}) yaw {math.degrees(s['yaw']):+.0f}")
    return bot.result()


def turn(err_deg):
    return run("turn", {"direction": "left" if err_deg > 0 else "right", "degrees": min(max(abs(err_deg), 10), 180)}, 30)


def goto(x, y, how):
    for _ in range(12):
        st = bot.state()
        dx, dy = x - st["x"], y - st["y"]
        err = math.degrees(math.atan2(math.sin(math.atan2(dy, dx) - st["yaw"]), math.cos(math.atan2(dy, dx) - st["yaw"])))
        if math.hypot(dx, dy) <= 0.15:
            return "reached"
        res = turn(err) if abs(err) > 8 else run(how, {"direction": "forward", "distance_cm": min(math.hypot(dx, dy) * 100, 150)}, 40)
        if res != "reached":
            return res
    return "timeout"


def climb(params):
    params = dict(params)
    yaw = params.pop("yaw", None)
    if yaw is not None:
        err = math.degrees(math.atan2(math.sin(yaw - bot.state()["yaw"]), math.cos(yaw - bot.state()["yaw"])))
        if abs(err) > 8 and turn(err) != "reached":
            return "turn failed"
    return run("climb", params, 120)


if "--speed" in sys.argv:
    print("set_speed ->", run("set_speed", {"percent": float(sys.argv[sys.argv.index("--speed") + 1])}, 5))
t0, visited, failed = time.time(), set(), []
for n, leg in enumerate(EXPLORE, 1):
    s0 = bot.state()
    res = climb(leg[1]) if leg[0] == "climb" else goto(leg[1], leg[2], "crawl" if leg[0] == "crawl" else "walk")
    s = bot.state()
    room = room_at(s["x"], s["y"], s["z"] - 0.048)
    visited.add(room)
    ok = res == "reached" and not s["fallen"]
    if not ok:
        failed.append(n)
    what = f"climb {leg[1]['direction']}" if leg[0] == "climb" else f"{leg[0]} ({leg[1]}, {leg[2]})"
    print(f"{n:2d} {what:22s} -> {res:16s} at ({s['x']:+.2f}, {s['y']:+.2f}) floor {(s['z'] - 0.048) * 100:3.0f} cm "
          f"{str(room):14s} {(s['step'] - s0['step']) / 240:5.1f}s", flush=True)
    if not ok:
        break
s = bot.state()
print(f"\n{len(EXPLORE) - len(failed) if not failed else failed[0] - 1}/{len(EXPLORE)} legs reached, sim {s['step'] / 240 / 60:.1f} min "
      f"(wall {(time.time() - t0) / 60:.1f} min), max tilt {max_tilt:.1f} deg, rooms visited {len(visited & set(ROOMS))}/{len(ROOMS)}"
      f"{'' if visited >= set(ROOMS) else ' missing ' + str(set(ROOMS) - visited)}")
bot.close()
sys.exit(0 if not failed and visited >= set(ROOMS) else 1)
