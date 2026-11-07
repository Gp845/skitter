"""Guided tour of the sim house (fallback route = patrol legs) with a fake 0G, then go_to a room via a fake decision. No 0G calls."""
import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SKITTER_HOME"] = tempfile.mkdtemp()
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.core.house_map import HouseMap, map_path  # noqa: E402
from cortex.core.planner import Decision  # noqa: E402
from cortex.core.runtime import Runtime  # noqa: E402


class FakeOG:
    """Answers the explore look: first look 'living room', later ones 'dining area' (any label works for the test)."""
    def __init__(self):
        self.looks = []

    def describe(self, model, jpeg, prompt, max_tokens=60):
        self.looks.append(prompt)
        room = "living room" if len(self.looks) == 1 else "dining area"
        text = json.dumps({"room": room, "objects": [{"label": f"thing {len(self.looks)}", "x1": 450, "y1": 300, "x2": 550, "y2": 600}]})
        return type("R", (), {"value": f"```json\n{text}\n```", "request_id": f"look-{len(self.looks)}"})()


class FakePlanner:
    def __init__(self, script):
        self.og, self.model, self.script, self.states = FakeOG(), "fake", script, []

    def decide(self, instruction, frame, state, history, memory=()):
        self.states.append(state)
        return Decision(*self.script[min(len(self.states), len(self.script)) - 1], "req", 0.0, "fake", {})


def wait_for(rt, pred, limit_s):
    end = time.time() + limit_s
    while time.time() < end:
        hit = [e for e in list(rt.events) if pred(e)]
        if hit:
            return hit[0]
        time.sleep(0.2)
    return None


bot = HexapodAdapter(world="house")
fake = FakePlanner([("go_to", {"place": "living room"}), ("done", {"reason": "arrived"})])
rt = Runtime(bot, fake)
checks = {}
import cortex.adapters.hexapod_pybullet.house as house  # noqa: E402
if not hasattr(house, "EXPLORE"):
    checks["fallback route = patrol waypoints as goto legs"] = rt._tour_route() == [("goto", x, y) for x, y in house.PATROL]
bot.explore_route = [("goto", x, y) for x, y in house.PATROL]  # the CURRENT house's round, whatever EXPLORE becomes
rt.start()
t = time.time()
rt.submit("guided tour")
end = wait_for(rt, lambda e: e["type"] == "explore" and e["msg"].startswith("guided tour ended"), 600)
print(f"tour: {end and end['msg']} ({time.time() - t:.0f}s)")
m = HouseMap.load()
checks["explore ran to the end and saved the map"] = bool(end) and "(done)" in end["msg"] and m is not None
checks["about one 0G look per room (2 for this 8 m round)"] = len(fake.og.looks) == 2 and "one of: living room" in fake.og.looks[0]
checks["map has the waypoints, rooms and located objects"] = (
    m is not None and len(m.nodes) == 5 and set(m.rooms()) == {"living room", "dining area"} and len(m.objects) >= 1)
checks["no model decision during exploration"] = fake.states == []

t = time.time()
rt.submit("go to the living room")
done = wait_for(rt, lambda e: e["type"] == "task_end", 600)
target = m.nodes[m.rooms()["living room"]]
st = bot.state()
off = math.hypot(st["x"] - target["x"], st["y"] - target["y"])
print(f"go_to: {done and done['outcome']}, {off:.2f} m from the living room node ({time.time() - t:.0f}s)")
s0 = fake.states[0]
checks["model state carries the map summary"] = isinstance(s0.get("map"), dict) and s0["map"]["you_are_in"] == "dining area"
results = [e.get("result") for e in rt.events if e["type"] == "action_done"]
checks["go_to reached, then the model was re-asked with the result"] = results == ["reached"] and done and done["outcome"] == "done"
checks["arrived within 0.3 m of the room's node"] = off < 0.3
rt.close()

for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed  (map: {map_path()})")
sys.exit(0 if all(checks.values()) else 1)
