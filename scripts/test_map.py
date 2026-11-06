"""House map checks, pure (no sim, no 0G): save/load, node + object merging, shortest path over climb edges."""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SKITTER_HOME"] = tempfile.mkdtemp()
from cortex.core.house_map import HouseMap, map_path  # noqa: E402

m = HouseMap()
up = {"step_height_cm": 6, "steps": 3, "tread_cm": 30, "width_cm": 40, "direction": "up", "yaw": 0.0}
a = m.add_node(0, 0, 0.1, 0, "living room")
b = m.add_node(2, 0, 0.1, 0, "living room", a)
c = m.add_node(2, 2, 0.1, 0, "hallway", b)
d = m.add_node(3, 2, 0.28, 1, "loft", c, "climb", up)
e = m.add_node(5, 2, 0.28, 1, "loft", d)
f = m.add_node(5, 0, 0.1, 0, "kitchen", c, "crawl")  # through the vent
m.add_object("sofa", 1.0, 0.3, 0)
m.add_object("plant", 0.1, 0.1, 0)
m.add_object("plant", 5.2, 2.1, 1)
checks = {
    "a waypoint 0.2 m from a node merges into it": m.add_node(2.2, 0.1, 0.1, 0, None, a) == b and len(m.nodes) == 6,
    "...and adds no duplicate edge": len(m.edges) == 5,
    "room centres": m.rooms() == {"living room": a, "hallway": c, "loft": d, "kitchen": f},
    "same label in another room kept apart": set(m.objects) == {"sofa", "plant", "plant (loft)"},
}
other = m.add_node(2.0, 0.0, 0.28, 1)
checks["same position on another level is a new node"] = other not in (a, b)
del m.nodes[other]
m.save()
m2 = HouseMap.load()
checks["save/load round trip (atomic, no tmp left)"] = (
    m2.nodes == m.nodes and m2.edges == m.edges and m2.objects == m.objects and not os.path.exists(map_path() + ".tmp"))
checks["no file -> load returns None"] = HouseMap.load(os.path.join(tempfile.mkdtemp(), "map.json")) is None

r = m2.route(0.1, 0.1, 0.1, "loft")
checks["ground -> loft goes up the stairs"] = [h for h, _, _ in r] == ["walk", "walk", "walk", "climb"] and r[-1][2]["direction"] == "up"
r = m2.route(5.0, 2.0, 0.28, "kitchen")
legs = [(h, k) for h, k, _ in r]
down = [p for h, _, p in r if h == "climb"]
checks["loft -> kitchen: climbs DOWN facing back, then the vent"] = (
    legs == [("walk", e), ("walk", d), ("climb", c), ("crawl", f)] and down[0]["direction"] == "down"
    and abs(down[0]["yaw"] - math.pi) < 1e-9)
checks["robot on level 1 above a level-0 node starts from level 1"] = m2.route(2.0, 0.1, 0.28, "loft")[0][1] == d
checks["object by name -> its node"] = m2.route(0, 0, 0.1, "the sofa")[-1][1] == m2.objects["sofa"]["node"]
checks["unknown place -> no route"] = m2.route(0, 0, 0.1, "garage") is None
m2.edges = [x for x in m2.edges if x["how"] != "climb"]
checks["unreachable place -> no route"] = m2.route(0, 0, 0.1, "loft") is None
m3 = HouseMap()
m3.add_node(0, 0, 0.1, 0, "porch")
m3.add_node(1, 0, 0.3, 1, "deck", "n0", "climb", {"steps": 2})  # stairs with no direction: one-way
checks["climb edge without a direction is one-way"] = (m3.route(0, 0, 0.1, "deck") is not None
                                                       and m3.route(1, 0, 0.3, "porch") is None)
s = m.summary(0, 0, 0.1)
checks["summary: where the robot is + rooms + objects"] = s["you_are_in"] == "living room" and s["rooms"]["loft"][2] == 1 \
    and s["objects"]["sofa"] == [1.0, 0.3, "living room"]

for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
sys.exit(0 if all(checks.values()) else 1)
