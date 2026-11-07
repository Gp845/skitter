"""Runs a spoken task: ask 0G, validate, execute under a lease, re-ask before the lease ends.

Rules the robot always obeys:
  * it only moves on a fresh, validated 0G decision, and only for the lease duration;
  * any 0G failure, invalid output, fall or e-stop means STOP (never a guessed action);
  * the next decision is requested while the current action is still running (pipelining).
"""
import math
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .house_map import HouseMap
from .og_client import OGError
from .planner import go_to_spec
from .safety import Reject, is_stop_reflex, validate

PREFETCH_S = 4.0     # ask for the next decision when this much lease is left
TICK_S = 0.02
MAX_DT = 0.1


class Executor:
    def __init__(self, adapter, planner, max_checkpoints=6, on_event=None, stale_s=8.0, on_tick=None):
        self.adapter, self.planner, self.stale_s = adapter, planner, stale_s
        self.max_checkpoints = max_checkpoints
        self.on_event = on_event or (lambda e: None)
        self.on_tick = on_tick or (lambda: None)
        self.state = "IDLE"
        self._estop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=1)
        self.max_lease = adapter.manifest().get("safety", {}).get("max_lease_s", 3.0)
        self.memory = deque(maxlen=6)  # earlier commands this session, so follow-ups ("go back") have context
        self.objects = {}              # label -> world (x, y), from boxes the model reported + the robot's depth
        self.map = HouseMap.load()     # made by Runtime.explore(); None until the house was explored

    # ---- helpers ----
    def _emit(self, type_, **kw):
        self.on_event({"t": time.time(), "type": type_, **kw})

    def _safe_stop(self, reason):
        self.adapter.stop()
        self.state = "SAFE_STOP"
        self._emit("safe_stop", reason=reason)
        return f"safe_stop: {reason}"

    def estop(self):
        """Callable from any thread (UI button). Independent of the network."""
        self._estop.set()
        self.adapter.stop()

    def _map(self, sightings, session=True):
        """session=False: house map only (exploration), not this session's known_objects."""
        for s in sightings:
            try:
                xy = self.adapter.locate({k: min(max(float(s[k]), 0), 1000) for k in ("x1", "y1", "x2", "y2")})
            except Exception:  # model output is untrusted; a bad box just isn't mapped
                continue
            if xy:
                label = str(s.get("label", "?")).strip().lower()[:40]
                if session:
                    self.objects[label] = xy
                if self.map:
                    st = self.adapter.state()
                    self.map.add_object(label, *xy, self.map.level_at(st["x"], st["y"], st.get("z", 0)))

    def _known(self, st):
        """Map entries relative to the robot now: what the model needs to pick the shortest turn."""
        out = {}
        for label, (ox, oy) in self.objects.items():
            dx, dy = ox - st["x"], oy - st["y"]
            b = (math.degrees(math.atan2(dy, dx) - st["yaw"]) + 180) % 360 - 180
            out[label] = f"{math.hypot(dx, dy):.2f} m, " + ("ahead" if abs(b) < 5 else
                                                             f"{abs(b):.0f} deg to the {'left' if b > 0 else 'right'}")
        return out

    def _submit(self, instruction, history, moving=False):
        frame, st = self.adapter.get_frame(), self.adapter.state()
        if self.objects and "yaw" in st:
            st["known_objects"] = self._known(st)
        st["map"] = (self.map.summary(st["x"], st["y"], st.get("z", 0)) if self._places() else
                     "none yet: the house has not been explored, so go_to is not available")
        # a frame only goes stale if the robot moves while 0G thinks; standing still, any latency is fine
        return self._pool.submit(self.planner.decide, instruction, frame, st, list(history), list(self.memory)), \
            time.monotonic() if moving else None

    def _pose(self):
        st = self.adapter.state()
        return {k: round(st[k], 2) for k in ("x", "y", "yaw") if k in st}

    def _places(self):
        return self.map.places() if self.map else []

    def _manifest(self):
        m = self.adapter.manifest()
        return {**m, "actions": m["actions"] + [go_to_spec(self._places())]} if self._places() else m

    # ---- driving (shared by go_to, the patrol and exploration; all run on the sim thread) ----
    def wait(self, timeout_s):
        """Step the sim until the current skill finishes: "done", "timeout" (sim time), or why it must stop."""
        last, end = time.monotonic(), self.adapter.t + timeout_s * 240  # sim steps: the sim runs slower than real time
        while self.adapter.busy():
            now = time.monotonic()
            self.adapter.step(min(now - last, MAX_DT))
            last = now
            self.on_tick()
            if self._estop.is_set():
                self.adapter.stop()
                return "stopped by E-STOP or a new command"
            if self.adapter.state().get("fallen"):
                self.adapter.stop()
                return "robot fell"
            if self.adapter.t > end:
                self.adapter.stop()
                return "timeout"
            time.sleep(0.005)
        return "done"

    def _turn(self, err_deg):
        self.adapter.execute("turn", {"direction": "left" if err_deg > 0 else "right",
                                      "degrees": min(max(abs(err_deg), 10), 180)}, 30)
        return self.wait(30)

    def drive_to(self, x, y, tol=0.15, how="walk"):
        """Turn + walk legs to (x, y): reached | blocked | timeout | why it had to stop."""
        crawl = how == "crawl" and any(a["name"] == "crawl" for a in self.adapter.manifest()["actions"])
        for _ in range(40):
            st = self.adapter.state()
            dx, dy = x - st["x"], y - st["y"]
            err = math.degrees(math.atan2(math.sin(math.atan2(dy, dx) - st["yaw"]), math.cos(math.atan2(dy, dx) - st["yaw"])))
            if math.hypot(dx, dy) <= tol:
                return "reached"
            if abs(err) > 8:
                why = self._turn(err)
            else:
                self.adapter.execute("crawl" if crawl else "walk",
                                     {"direction": "forward", "distance_cm": min(math.hypot(dx, dy) * 100, 150)}, 30)
                why = self.wait(30)
                if why == "done" and self.adapter.result() == "blocked":
                    return "blocked"
            if why != "done":
                return why
        return "timeout"

    def climb(self, params):
        """Stairs: optional params["yaw"] (world heading, rad) to face first; the rest goes to the climb skill."""
        params = dict(params)
        yaw = params.pop("yaw", None)
        if yaw is not None:
            err = math.degrees(math.atan2(math.sin(yaw - self.adapter.state()["yaw"]), math.cos(yaw - self.adapter.state()["yaw"])))
            why = self._turn(err) if abs(err) > 8 else "done"
            if why != "done":
                return why
        self.adapter.execute("climb", params, 120)
        why = self.wait(120)
        return self.adapter.result() if why == "done" else why

    def go_to(self, place):
        """Follow the mapped route leg by leg: reached | no_route | blocked | timeout | a climb refusal | stop reason."""
        st = self.adapter.state()
        legs = self.map.route(st["x"], st["y"], st.get("z", 0), place) if self.map else None
        if legs is None:
            return "no_route"
        for i, (how, k, params) in enumerate(legs):
            res = self.climb(params) if how == "climb" else self.drive_to(self.map.nodes[k]["x"], self.map.nodes[k]["y"], how=how)
            st = self.adapter.state()
            # explored places can sit against a wall: coming from another side, the block reflex stops short of them
            near = math.hypot(st["x"] - self.map.nodes[k]["x"], st["y"] - self.map.nodes[k]["y"]) < 0.5
            if res != "reached" and not (res == "blocked" and near and i < len(legs) - 1 and legs[i + 1][0] != "climb"):
                return res
        return "reached"

    # ---- main entry ----
    def run_task(self, instruction: str) -> str:
        before, history = self._pose(), []
        out = self._run(instruction, history)
        if self.map:  # objects the model reported during the task
            self.map.save()
        self.memory.append(f'"{instruction}": {"; ".join(history) or "no action"} -> {out[:80]} '
                           f"(pose before {before}, after {self._pose()})")
        return out

    def _run(self, instruction, history):
        self._estop.clear()
        self._emit("utterance", text=instruction)
        if is_stop_reflex(instruction):  # local safety reflex: stopping never needs a model
            self.adapter.stop()
            self.state = "IDLE"
            self._emit("reflex_stop", text=instruction)
            return "stopped (reflex)"

        n_decisions = 1
        pending = self._submit(instruction, history)
        self.state, lease_end, acting, goal = "THINKING", 0.0, False, False
        last = time.monotonic()
        while True:
            now = time.monotonic()
            self.adapter.step(min(now - last, MAX_DT))
            last = now
            self.on_tick()

            if self._estop.is_set():
                self.state = "IDLE"
                self._emit("estop")
                return "estop"
            if self.adapter.state().get("fallen"):
                return self._safe_stop("robot fell / out of safe tilt")

            if acting and goal and not self.adapter.busy():  # device finished its own goal
                res = self.adapter.result()
                acting = goal = False
                history[-1] += f" => {res}"
                self._emit("action_done", result=res)

            if acting and now >= lease_end:  # watchdog: lease over, nobody renewed it
                self.adapter.stop()
                if goal:
                    history[-1] += " => timeout"
                acting = goal = False
                self._emit("lease_expired")

            # timed actions: ask for the next decision while still moving. Goal actions are NOT
            # prefetched: a frame taken mid-goal can't show the goal's effect (stale), so we re-ask on completion.
            if acting and not goal and pending is None and now >= lease_end - PREFETCH_S:
                if n_decisions >= self.max_checkpoints:
                    if now >= lease_end:
                        self.adapter.stop()
                        self.state = "IDLE"
                        self._emit("task_end", outcome="checkpoint_cap")
                        return "stopped: checkpoint cap"
                else:
                    pending, n_decisions = self._submit(instruction, history, moving=True), n_decisions + 1

            if not acting and pending is None:
                if n_decisions >= self.max_checkpoints:
                    self.state = "IDLE"
                    self._emit("task_end", outcome="checkpoint_cap")
                    return "stopped: checkpoint cap"
                pending, n_decisions = self._submit(instruction, history), n_decisions + 1

            if pending is not None and pending[0].done():
                (fut, t_sub), pending = pending, None
                if t_sub is not None and time.monotonic() - t_sub > self.stale_s:  # decided on a frame too old to trust
                    fut.cancel()
                    return self._safe_stop(f"stale decision ({time.monotonic() - t_sub:.1f}s old)")
                try:
                    d = fut.result()
                except OGError as e:
                    return self._safe_stop(f"0G unavailable: {e}")
                except Exception as e:  # any bug in the decision path also means stop
                    return self._safe_stop(f"decision error: {e}")
                self._emit("decision", name=d.name, args=d.args, request_id=d.request_id,
                           model=d.model, latency_s=round(d.latency_s, 2), trace=d.trace, frame=d.frame, seen=d.seen)
                # shortcut: maps with the robot's current pose; exact for goal actions (decided while standing),
                # slightly off for prefetched timed moves. Upgrade: locate against the decision frame's pose/depth.
                self._map(list(d.seen) + ([d.args] if "label" in d.args and "x1" in d.args else []))
                if d.name in ("done", "ask_user", "say", "stop"):
                    self.adapter.stop()
                    self.state = "IDLE"
                    self._emit("task_end", outcome=d.name, detail=d.args)
                    return f"{d.name}: {d.args}"
                try:
                    name, args = validate(self._manifest(), d.name, d.args)
                except Reject as e:
                    return self._safe_stop(f"rejected model output: {e}")
                if name == "go_to":  # runs to the end here (E-STOP / fall checked every step), then re-ask
                    self.state = "ACTING"
                    history.append(f"go_to {args}")
                    self._emit("action", name=name, args=args, lease_s=0)
                    res = self.go_to(args["place"])
                    history[-1] += f" => {res}"
                    self._emit("action_done", result=res)
                    last = time.monotonic()
                    continue
                spec = next(a for a in self.adapter.manifest()["actions"] if a["name"] == name)
                goal = bool(spec.get("goal"))
                lease = (spec.get("timeout_s", 30.0) if goal
                         else min(args.get("duration_s", self.max_lease), self.max_lease))
                self.adapter.execute(name, args, lease)
                lease_end, acting, self.state = time.monotonic() + lease, True, "ACTING"
                history.append(f"{name} {args}")
                self._emit("action", name=name, args=args, lease_s=lease)
            time.sleep(TICK_S)
