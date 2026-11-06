"""Owns the robot/sim thread. All adapter calls (stepping, camera) happen on this one thread."""
import math
import queue
import re
import threading
import time
from collections import deque

from .executor import Executor

PATROL = object()  # queue marker: run the security round instead of a spoken task


class Runtime:
    def __init__(self, adapter, planner, max_checkpoints=6, episodes=None):
        self.adapter, self.planner, self.episodes = adapter, planner, episodes
        self.events = deque(maxlen=500)
        self._n = 0
        self.lock = threading.Lock()
        self.frame = adapter.get_frame()
        self.pose, self.person_px = {}, 0
        self.on_patrol = lambda: None  # the server hooks recording in: a patrol is always recorded
        self._last_frame = 0.0
        self.q = queue.Queue()
        self.busy = False
        self.ex = Executor(adapter, planner, max_checkpoints=max_checkpoints,
                           on_event=self.emit, on_tick=self._refresh_frame)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.thread.start()

    def emit(self, e):
        e = dict(e)
        frame = e.pop("frame", None)  # keep image bytes out of the UI feed; store them in the episode
        if self.episodes:
            if frame:
                e["frame_sha"] = self.episodes.add_frame(frame)
            self.episodes.add(e)
        with self.lock:
            self._n += 1
            self.events.append({"i": self._n, **e})

    def events_since(self, i):
        with self.lock:
            return [e for e in self.events if e["i"] > i], self._n

    def _refresh_frame(self):
        # an idle render costs ~130 ms; while moving the adapter reuses the frame its skill already rendered
        if time.monotonic() - self._last_frame > (0.1 if self.adapter.busy() else 0.2):
            self.frame = self.adapter.get_frame()
            st = self.adapter.state()
            self.pose = {k: round(st[k], 3) for k in ("x", "y", "yaw")}
            self.person_px = getattr(self.adapter, "person_px", 0)
            self._last_frame = time.monotonic()

    def patrol(self):
        """Walk the world's security round, looped, with no model calls. E-STOP or any new command ends it."""
        if self.busy:
            self.ex.estop()
        self.q.put(PATROL)
        self.on_patrol()

    def _wait(self, timeout_s):
        """Step the sim until the current skill finishes: "done", "timeout" (sim time), or why the patrol must end."""
        last, end = time.monotonic(), self.adapter.t + timeout_s * 240  # sim steps: the sim runs slower than real time
        while self.adapter.busy():
            now = time.monotonic()
            self.adapter.step(min(now - last, 0.1))
            last = now
            self._refresh_frame()
            if self.ex._estop.is_set():
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

    def _patrol(self):
        route = list(getattr(self.adapter, "patrol_route", []))
        if not route:
            self.emit({"t": time.time(), "type": "patrol", "msg": "no patrol route for this world"})
            return
        self.ex._estop.clear()
        self.ex.state = "PATROL"
        self.emit({"t": time.time(), "type": "patrol", "msg": f"patrol started: {len(route)} waypoints, looping"})
        i, why = 0, "done"
        while why in ("done", "timeout"):
            if self.ex._estop.is_set():
                why = "stopped by E-STOP or a new command"
                break
            wx, wy = route[i % len(route)]
            st = self.adapter.state()
            dx, dy = wx - st["x"], wy - st["y"]
            err = math.degrees(math.atan2(math.sin(math.atan2(dy, dx) - st["yaw"]), math.cos(math.atan2(dy, dx) - st["yaw"])))
            if math.hypot(dx, dy) <= 0.15:
                i += 1
            elif abs(err) > 8:
                self.adapter.execute("turn", {"direction": "left" if err > 0 else "right",
                                              "degrees": min(max(abs(err), 10), 180)}, 30)
                why = self._wait(30)
            else:
                self.adapter.execute("walk", {"direction": "forward", "distance_cm": min(math.hypot(dx, dy) * 100, 150)}, 30)
                why = self._wait(30)
                if why == "done" and self.adapter.result() == "blocked":
                    why = "blocked"
            if why in ("blocked", "timeout"):  # skip this waypoint, keep patrolling
                self.emit({"t": time.time(), "type": "patrol", "msg": f"waypoint {i % len(route) + 1}: {why}; skipping it"})
                i, why = i + 1, "done"
        self.ex.state = "IDLE"
        self.emit({"t": time.time(), "type": "patrol", "msg": f"patrol stopped: {why}"})

    def submit(self, text):
        """New command interrupts the current task (the old one stops, then this one runs)."""
        if re.search(r"\bpatrol\b", text, re.I) and not re.search(r"\b(stop|end|cancel)\b", text, re.I):
            return self.patrol()  # a fixed round: no model needed
        if self.busy:
            self.ex.estop()
        self.q.put(text)

    def estop(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.ex.estop()

    def _loop(self):
        last = time.monotonic()
        while not self._stop.is_set():
            try:
                text = self.q.get(timeout=0.02)
            except queue.Empty:
                now = time.monotonic()
                self.adapter.step(min(now - last, 0.1))  # keep sim alive (robot stands) when idle
                last = now
                self._refresh_frame()
                continue
            self.busy = True
            try:
                self._patrol() if text is PATROL else self.ex.run_task(text)
            except Exception as e:  # never let a bug leave the robot moving
                self.adapter.stop()
                self.emit({"t": time.time(), "type": "safe_stop", "reason": f"runtime error: {e}"})
            self.busy = False
            last = time.monotonic()

    def close(self):
        self._stop.set()
        self.adapter.stop()
