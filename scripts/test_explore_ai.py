"""0G-driven exploration of the sim house, headless, with REAL 0G calls (costs money; BUDGET_USD caps the ledger).
Then go_to over the map it made. Options: --start x,y,heading_deg[,floor_cm] (teleport first, e.g. -4.5,2.3,90 at the stairs, -4.5,9.6,90,54 on the loft),
--cap N (decisions, default EXPLORE_MAX_DECISIONS or 150), --speed P, --goto "room a,room b"."""
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.core.og_client import Budget, OGClient  # noqa: E402  (imported first: the spend ledger stays the real one)
os.environ["SKITTER_HOME"] = tempfile.mkdtemp()  # the map goes to a temp dir, never over the real map
import pybullet as p  # noqa: E402
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.adapters.hexapod_pybullet.house import ROOMS, room_at  # noqa: E402
from cortex.core.house_map import HouseMap  # noqa: E402
from cortex.core.planner import Planner  # noqa: E402
from cortex.core.runtime import Runtime  # noqa: E402


def arg(name, default=None):
    for i, a in enumerate(sys.argv):
        if a.startswith(name + "="):  # --start=-4.5,2.3,90 (a value starting with "-" needs the = form)
            return a.split("=", 1)[1]
        if a == name:
            return sys.argv[i + 1]
    return default


if "--cap" in sys.argv:
    os.environ["EXPLORE_MAX_DECISIONS"] = arg("--cap")
bot = HexapodAdapter(world="house")
if arg("--start"):
    x, y, h, floor = (list(map(float, arg("--start").split(","))) + [0])[:4]
    p.resetBasePositionAndOrientation(bot.robot, [x, y, 0.12 + floor / 100], p.getQuaternionFromEuler([0, 0, math.radians(h)]))
    bot._settle()
if arg("--speed"):
    bot.execute("set_speed", {"percent": float(arg("--speed"))}, 5)
rt = Runtime(bot, Planner(OGClient(), os.environ["VLM_MODEL"], bot.manifest()))
spent0, t0, rooms_at = Budget().spent(), time.time(), set()
tick = rt.ex.on_tick
rt.ex.on_tick = lambda: (tick(), rooms_at.add(room_at(bot.state()["x"], bot.state()["y"], bot.state()["z"] - 0.048)))
emit = rt.emit


def show(e):
    if e["type"] in ("explore", "safe_stop"):
        s = bot.state()
        print(f"{e['t'] - t0:6.0f}s ({s['x']:+.2f}, {s['y']:+.2f}, {(s['z'] - 0.048) * 100:2.0f} cm) "
              f"{e.get('msg') or e.get('reason')}  {e.get('request_id', '')}", flush=True)
    emit(e)


rt.emit = rt.ex.on_event = show
rt._explore()  # on this thread: the runtime loop isn't started
m = rt.ex.map
named = m.rooms()
decisions = sum(1 for e in rt.events if e["type"] == "explore" and e.get("request_id"))
hows = [e["how"] for e in m.edges]
print(f"\n{decisions} decisions, {len(m.nodes)} places, {len(m.edges)} legs "
      f"({', '.join(f'{h} {hows.count(h)}' for h in ('walk', 'crawl', 'climb'))}), "
      f"wall {(time.time() - t0) / 60:.1f} min, spent ${Budget().spent() - spent0:.3f} (ledger ${Budget().spent():.3f})")
print(f"rooms walked through: {sorted(r for r in rooms_at if r)}")
print(f"rooms named on the map: {sorted(named)}  (missing {sorted(set(ROOMS) - set(named))})")
wrong = {k: (v['room'], room_at(v['x'], v['y'], v['z'] - 0.048)) for k, v in m.nodes.items()
         if v['room'] and v['room'] != room_at(v['x'], v['y'], v['z'] - 0.048)}
print(f"nodes labelled differently from the true room: {len(wrong)}/{sum(1 for v in m.nodes.values() if v['room'])} {wrong}")

checks = {"map saved and reloads": HouseMap.load() is not None and len(HouseMap.load().nodes) == len(m.nodes)}
targets = [r for r in (arg("--goto") or "").split(",") if r] or [r for r in sorted(named, key=lambda r: -len(r))][:2]
for room in targets:
    if rt.ex._estop.is_set() or bot.state()["fallen"]:
        break
    t = time.time()
    res = rt.ex.go_to(room)
    st = bot.state()
    k = m.rooms().get(room)
    off = math.hypot(st["x"] - m.nodes[k]["x"], st["y"] - m.nodes[k]["y"]) if k else 9.9
    print(f"go_to {room}: {res}, {off:.2f} m from its node, really in {room_at(st['x'], st['y'], st['z'] - 0.048)} "
          f"({time.time() - t:.0f}s)", flush=True)
    checks[f"go_to {room} reached within 0.3 m"] = res == "reached" and off < 0.3
bot.close()
for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
sys.exit(0 if all(checks.values()) else 1)
