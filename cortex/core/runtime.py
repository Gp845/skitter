"""Owns the robot/sim thread. All adapter calls (stepping, camera) happen on this one thread."""
import json
import math
import queue
import re
import threading
import time
from collections import deque

from .executor import Executor
from .house_map import HouseMap

PATROL = object()   # queue marker: run the security round instead of a spoken task
EXPLORE = object()  # queue marker: map the house
ROOM_EVERY_M = 2.5  # ask 0G "which room is this" again after walking this far (or after stairs / a vent)
ROOM_Q = ('This is the low front camera of a small robot mapping a house. Reply with JSON only, no prose: '
          '{{"room": "<the room the robot is standing in{rooms}; judge by the floor and furniture around it, not by '
          'a room seen through a doorway>", "objects": [{{"label": "<short name, e.g. red sofa>", '
          '"x1": 0, "y1": 0, "x2": 0, "y2": 0}}]}} listing up to 8 distinct pieces of furniture or objects in view, '
          'boxes in 0-1000 image coordinates (x to the right, y down, origin top-left).')


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

    def _patrol(self):
        route = list(getattr(self.adapter, "patrol_route", []))
        if not route:
            self.emit({"t": time.time(), "type": "patrol", "msg": "no patrol route for this world"})
            return
        self.ex._estop.clear()
        self.ex.state = "PATROL"
        self.emit({"t": time.time(), "type": "patrol", "msg": f"patrol started: {len(route)} waypoints, looping"})
        i, why = 0, "reached"
        while why == "reached":
            if self.ex._estop.is_set():
                why = "stopped by E-STOP or a new command"
                break
            why = self.ex.drive_to(*route[i % len(route)])
            if why in ("blocked", "timeout"):  # skip this waypoint, keep patrolling
                self.emit({"t": time.time(), "type": "patrol", "msg": f"waypoint {i % len(route) + 1}: {why}; skipping it"})
                why = "reached"
            i += 1
        self.ex.state = "IDLE"
        self.emit({"t": time.time(), "type": "patrol", "msg": f"patrol stopped: {why}"})

    def explore(self):
        """Walk the world's exploration legs once, asking 0G about one question per room; saves the house map."""
        if self.busy:
            self.ex.estop()
        self.q.put(EXPLORE)
        self.on_patrol()  # mapping the house is recorded the same way as a patrol

    def _explore_route(self):
        legs = getattr(self.adapter, "explore_route", None)
        if not legs and getattr(self.adapter, "world", None) == "house":
            try:
                from ..adapters.hexapod_pybullet.house import EXPLORE as legs
            except ImportError:
                legs = None
        return list(legs or [("goto", x, y) for x, y in getattr(self.adapter, "patrol_route", [])])

    def _look(self, rooms):
        """One 0G question at a waypoint: which room is this, and what is in it (boxes -> map via depth)."""
        frame = self.adapter.get_frame()
        try:
            r = self.planner.og.describe(self.planner.model, frame, ROOM_Q.format(
                rooms=f" (one of: {', '.join(rooms)})" if rooms else ""), max_tokens=400)
            d = json.loads(r.value[r.value.index("{"): r.value.rindex("}") + 1])
            room = str(d.get("room") or "").strip().lower()[:40] or None
            objs = [o for o in d.get("objects") or [] if isinstance(o, dict)]
        except Exception as e:  # 0G down, budget cap, or a non-JSON answer: keep mapping without labels
            self.emit({"t": time.time(), "type": "explore", "msg": f"0G look failed: {e}"})
            return None, []
        if rooms and room not in rooms:
            room = None
        self.emit({"t": time.time(), "type": "explore", "frame": frame, "request_id": r.request_id,
                   "msg": f"0G: this is the {room or '?'}; sees {', '.join(str(o.get('label')) for o in objs) or 'nothing'}"})
        return room, objs

    def _explore(self):
        legs = self._explore_route()
        if not legs:
            self.emit({"t": time.time(), "type": "explore", "msg": "no exploration route for this world"})
            return
        rooms = list(getattr(self.adapter, "rooms", None) or [])
        if not rooms and getattr(self.adapter, "world", None) == "house":
            from ..adapters.hexapod_pybullet.house import ROOMS
            rooms = list(ROOMS)
        self.ex._estop.clear()
        self.ex.state = "EXPLORE"
        self.emit({"t": time.time(), "type": "explore", "msg": f"exploring: {len(legs)} legs"})
        m = self.ex.map = HouseMap()
        st = self.adapter.state()
        level, room, asked, why = 0, None, None, "done"
        prev = m.add_node(st["x"], st["y"], st.get("z", 0), level)
        for n, leg in enumerate(legs, 1):
            if self.ex._estop.is_set():
                why = "stopped by E-STOP or a new command"
                break
            params = None
            if leg[0] == "climb":
                params = {"yaw": self.adapter.state()["yaw"], **leg[1]}
                res, how = self.ex.climb(params), "climb"
                if res != "reached":  # every later leg assumes the robot made it up (or down)
                    why = f"climb at leg {n}: {res}"
                    break
                level += 1 if params.get("direction", "up") == "up" else -1
            else:
                how = "crawl" if leg[0] == "crawl" else "walk"
                res = self.ex.drive_to(leg[1], leg[2], how=how)
                if res in ("blocked", "timeout"):  # still a place it reached, mapped where it stopped
                    self.emit({"t": time.time(), "type": "explore", "msg": f"leg {n}: {res}; mapping where it stopped"})
                elif res != "reached":
                    why = res
                    break
            st = self.adapter.state()
            prev = m.add_node(st["x"], st["y"], st.get("z", 0), level, room, prev, how, params)
            if asked is None or how != "walk" or math.hypot(st["x"] - asked[0], st["y"] - asked[1]) > ROOM_EVERY_M:
                asked = st["x"], st["y"]
                seen_room, objs = self._look(rooms)
                room = seen_room or room
                for node in m.nodes.values():  # places walked before the first answer were in this room too
                    node["room"] = node["room"] or room
                m.nodes[prev]["room"] = room
                self.ex._map(objs, session=False)
                m.save()
        m.save()
        self.ex.state = "IDLE"
        self.emit({"t": time.time(), "type": "explore", "msg": f"exploration ended ({why}): {len(m.nodes)} places, "
                   f"rooms {', '.join(m.rooms()) or 'none'}, {len(m.objects)} objects; map saved to {m.path}"})

    def submit(self, text):
        """New command interrupts the current task (the old one stops, then this one runs)."""
        if re.search(r"\bpatrol\b", text, re.I) and not re.search(r"\b(stop|end|cancel)\b", text, re.I):
            return self.patrol()  # a fixed round: no model needed
        if re.search(r"\bexplore\b|\bmap (the|this|my) (house|home|place)\b", text, re.I) and \
                not re.search(r"\b(stop|end|cancel)\b", text, re.I):
            return self.explore()  # a fixed route with ~1 0G look per room, not a decision loop
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
                self._patrol() if text is PATROL else self._explore() if text is EXPLORE else self.ex.run_task(text)
            except Exception as e:  # never let a bug leave the robot moving
                self.adapter.stop()
                self.emit({"t": time.time(), "type": "safe_stop", "reason": f"runtime error: {e}"})
            self.busy = False
            last = time.monotonic()

    def close(self):
        self._stop.set()
        self.adapter.stop()
