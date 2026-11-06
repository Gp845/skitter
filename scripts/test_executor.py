"""Safety behaviour tests: a 2D dot stand-in for Skitter + fake planner. Costs nothing, never calls 0G."""
import math
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SKITTER_HOME"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_no_map")  # never a real map
from cortex.core.executor import Executor  # noqa: E402
from cortex.core.og_client import OGError  # noqa: E402
from cortex.core.planner import Decision  # noqa: E402


class MockAdapter:
    """2D dot robot (1 m/s) with a drawn camera: same interface as HexapodAdapter, no physics."""

    def __init__(self):
        self.x = self.y = self.yaw = 0.0
        self.cmd = None  # (vx, wz, time_left)
        self.goal = None  # (distance_m, travelled_m)
        self._result = None
        self.t = 0
        self.objects = [("red box", (2.0, 0.0)), ("wall", (3.0, 0.0))]

    def manifest(self):
        return {
            "name": "mock-dot",
            "description": "A 2D dot robot, 1 m/s, front camera.",
            "actions": [
                {"name": "move", "params": {"direction": ["forward", "backward", "turn_left", "turn_right"],
                                            "speed": [0.1, 1.0], "duration_s": [0.5, 6.0]}},
                {"name": "walk", "goal": True, "timeout_s": 20,
                 "description": "Walk a distance; finishes on its own.",
                 "params": {"distance_cm": [10, 500]}},
                {"name": "stop", "params": {}},
            ],
            "safety": {"max_lease_s": 6.0},
        }

    def get_frame(self):
        img = np.full((240, 320, 3), 40, np.uint8)
        for name, (ox, oy) in self.objects:
            dx, dy = ox - self.x, oy - self.y
            fwd = dx * math.cos(self.yaw) + dy * math.sin(self.yaw)
            left = -dx * math.sin(self.yaw) + dy * math.cos(self.yaw)
            if fwd > 0.1:
                px = int(160 - 160 * left / fwd)
                size = int(min(100, 40 / fwd))
                cv2.rectangle(img, (px - size, 120 - size), (px + size, 120 + size), (0, 0, 255), -1)
        return cv2.imencode(".jpg", img)[1].tobytes()

    def state(self):
        return {"x": self.x, "y": self.y, "yaw": self.yaw, "fallen": False, "step": self.t}

    def busy(self):
        return self.goal is not None or self.cmd is not None

    def result(self):
        return self._result

    def locate(self, box):
        return None

    def execute(self, action, params, lease_s):
        if action == "stop":
            return self.stop()
        if action == "walk":
            self.cmd, self._result, self.goal = None, None, [params["distance_cm"] / 100, 0.0]
            return
        d = params.get("direction", "forward")
        v = params.get("speed", 0.5)
        vx = {"forward": v, "backward": -v}.get(d, 0.0)
        wz = {"turn_left": v, "turn_right": -v}.get(d, 0.0)
        self.cmd = (vx, wz, min(params.get("duration_s", lease_s), lease_s))

    def stop(self):
        if self.goal is not None:
            self._result = "cancelled"
        self.cmd = self.goal = None

    def step(self, dt):
        self.t += 1
        if self.goal is not None:
            self.x += math.cos(self.yaw) * 1.0 * dt
            self.goal[1] += 1.0 * dt
            if self.goal[1] >= self.goal[0]:
                self.goal, self._result = None, "reached"
            return
        if not self.cmd:
            return
        vx, wz, left = self.cmd
        self.x += vx * math.cos(self.yaw) * dt
        self.y += vx * math.sin(self.yaw) * dt
        self.yaw += wz * dt
        left -= dt
        self.cmd = (vx, wz, left) if left > 0 else None


class Fake:
    """Replays scripted decisions; items may be Decision, an Exception to raise, or a sleep (secs, Decision)."""

    def __init__(self, script):
        self.script, self.i, self.histories, self.memories = list(script), 0, [], []

    def decide(self, instruction, frame, state, history, memory=()):
        self.histories.append(list(history))
        self.memories.append(list(memory))
        item = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        if isinstance(item, tuple):
            time.sleep(item[0])
            item = item[1]
        if isinstance(item, Exception):
            raise item
        return item


def D(name, **args):
    return Decision(name, args, f"req-{name}", 0.0, "fake", {})


def move(d="forward", s=1.0, dur=1.0):
    return D("move", direction=d, speed=s, duration_s=dur)


def run(title, script, expect, instruction="go", max_cp=6, stale_s=8.0):
    bot, events = MockAdapter(), []
    ex = Executor(bot, Fake(script), max_checkpoints=max_cp, on_event=events.append, stale_s=stale_s)
    t = time.time()
    out = ex.run_task(instruction)
    ok = expect(bot, ex, out, events)
    print(f"{'PASS' if ok else 'FAIL'}  {title:48s} -> {out[:55]!r} x={bot.x:.2f} cmd={bot.cmd} ({time.time() - t:.1f}s)")
    return ok


def walk(cm):
    return D("walk", distance_cm=cm)


def estop_midgoal():
    import threading
    bot, events = MockAdapter(), []
    ex = Executor(bot, Fake([walk(500), D("done", reason="x")]), on_event=events.append)
    threading.Timer(1.0, ex.estop).start()  # button pressed 1 s into a 5 s walk
    out = ex.run_task("go")
    ok = out == "estop" and bot.goal is None and bot.result() == "cancelled" and 0.5 < bot.x < 2.0
    print(f"{'PASS' if ok else 'FAIL'}  {'estop during goal action halts it':48s} -> {out!r} x={bot.x:.2f} result={bot.result()}")
    return ok


def order(ev):
    return [e["type"] for e in ev if e["type"] in ("decision", "action", "action_done")]


def memory_across_commands():
    bot = MockAdapter()
    fake = Fake([walk(50), D("done", reason="x"), D("done", reason="y")])
    ex = Executor(bot, fake)
    ex.run_task("walk half a metre")
    ex.run_task("go back")
    first_call_of_second_task = fake.memories[2]
    ok = (fake.memories[0] == [] and len(first_call_of_second_task) == 1
          and '"walk half a metre"' in first_call_of_second_task[0] and "reached" in first_call_of_second_task[0]
          and fake.histories[2] == [])
    print(f"{'PASS' if ok else 'FAIL'}  {'2nd command sees the 1st (memory), fresh history':48s} -> {first_call_of_second_task}")
    return ok


def object_map():
    class Seeing(MockAdapter):
        def locate(self, box):
            return (1.0, 1.0) if box["x1"] < 500 else None
    bot, fake = Seeing(), Fake([D("done", reason="x")])
    fake.script[0].seen = [{"label": "Blue Box", "x1": 10, "y1": 0, "x2": 90, "y2": 50},
                           {"label": "junk", "x1": "nan?"}, {"label": "far", "x1": 900, "y1": 0, "x2": 990, "y2": 9}]
    ex = Executor(bot, fake)
    ex.run_task("look")
    known = ex._known(bot.state())
    ok = list(ex.objects) == ["blue box"] and known["blue box"] == "1.41 m, 45 deg to the left"
    print(f"{'PASS' if ok else 'FAIL'}  {'object map: bad boxes skipped, bearing relative':48s} -> {known}")
    return ok


results = [
    object_map(),
    memory_across_commands(),
    run("goal action: re-asks only after it finishes, with its result", [walk(100), (0.3, D("done", reason="x"))],
        lambda b, e, o, ev: o.startswith("done") and abs(b.x - 1.0) < 0.15
        and order(ev) == ["decision", "action", "action_done", "decision"]
        and e.planner.histories[1] == ["walk {'distance_cm': 100.0} => reached"]),
    run("goal action: no 0G call while it runs (no stale prefetch)", [walk(200), (0.1, D("done", reason="x"))],
        lambda b, e, o, ev: e.planner.i == 2 and order(ev).index("action_done") < len(order(ev)) - 1),
    estop_midgoal(),
    run("moves, then done stops the robot", [move(dur=3.0), (0.5, D("done", reason="ok"))],
        lambda b, e, o, ev: o.startswith("done") and b.x > 0.3 and b.cmd is None),
    run("decision pipelined during motion (no pause)", [move(dur=3.0), (0.5, move(dur=3.0)), (0.5, D("done", reason="ok"))],
        lambda b, e, o, ev: o.startswith("done") and b.x > 1.0 and not any(x["type"] == "lease_expired" for x in ev)),
    run("0G failure => safe stop, robot halted", [move(dur=3.0), OGError("timeout")],
        lambda b, e, o, ev: o.startswith("safe_stop") and b.cmd is None and e.state == "SAFE_STOP"),
    run("invalid action => safe stop", [D("fly", altitude=5)],
        lambda b, e, o, ev: o.startswith("safe_stop") and b.x == 0),
    run("params clamped (speed 99 -> 1.0, dur 99 -> 6.0)", [move(s=99, dur=99), D("done", reason="x")],
        lambda b, e, o, ev: next(x for x in ev if x["type"] == "action")["args"]["speed"] == 1.0
        and next(x for x in ev if x["type"] == "action")["lease_s"] == 6.0),
    run("slow 0G => robot stops, stale decision refused", [move(dur=1.0), (4.0, move(dur=1.0))],
        lambda b, e, o, ev: any(x["type"] == "lease_expired" for x in ev) and "stale" in o and b.x < 1.2, stale_s=3.0),
    run("slow 0G while standing still => decision still used", [(4.0, walk(50)), D("done", reason="x")],
        lambda b, e, o, ev: "stale" not in o and any(x["type"] == "action_done" for x in ev), stale_s=3.0),
    run("bare 'stop' = local reflex, no model call", [OGError("must not be called")],
        lambda b, e, o, ev: "reflex" in o, instruction="stop"),
    run("'stop before the wall' still goes to the model", [move(), D("done", reason="x")],
        lambda b, e, o, ev: o.startswith("done"), instruction="stop before the wall"),
    run("checkpoint cap ends a runaway task", [move(dur=1.0)],
        lambda b, e, o, ev: "checkpoint cap" in o and b.cmd is None, max_cp=3),
]
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
