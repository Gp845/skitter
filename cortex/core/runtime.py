"""Owns the robot/sim thread. All adapter calls (stepping, camera) happen on this one thread."""
import json
import math
import os
import queue
import re
import threading
import time
from collections import deque

from .executor import Executor
from .house_map import HouseMap
from .og_client import OGError
from .planner import EXPLORE_ACTIONS
from .safety import Reject, validate

PATROL = object()   # queue marker: run the security round instead of a spoken task
EXPLORE = object()  # queue marker: map the house, 0G deciding every move
TOUR = object()     # queue marker: map the house along the world's fixed route
ROOM_EVERY_M = 2.5  # ask 0G "which room is this" again after walking this far (or after stairs / a vent)
ROOM_Q = ('This is the low front camera of a small robot mapping a house. Reply with JSON only, no prose: '
          '{{"room": "<the room the robot is standing in{rooms}; judge by the floor and furniture around it, not by '
          'a room seen through a doorway>", "objects": [{{"label": "<short name, e.g. red sofa>", '
          '"x1": 0, "y1": 0, "x2": 0, "y2": 0}}]}} listing up to 8 distinct pieces of furniture or objects in view, '
          'boxes in 0-1000 image coordinates (x to the right, y down, origin top-left).')


def _mismatch(args, s):
    """Why a model's climb doesn't fit the stairs the depth camera measured from where it stands ("" = fits)."""
    if not isinstance(s, dict):
        return "no stairs measured straight ahead: face them square-on 0.6-0.8 m before the first riser"
    if abs(s["stairs_centre_left_cm"]) > 10:
        side = "left" if s["stairs_centre_left_cm"] > 0 else "right"
        return f"off_centre: the stairs' centre is {abs(s['stairs_centre_left_cm'])} cm to the {side}; line up first"
    if isinstance(s["width_cm"], int) and args["width_cm"] > s["width_cm"] + 15:
        return f"width_mismatch (measured {s['width_cm']} cm)"
    if isinstance(s["tread_cm"], int) and abs(args["tread_cm"] - s["tread_cm"]) > 8:
        return f"tread_mismatch (measured {s['tread_cm']} cm)"
    if isinstance(s["risers_in_view"], int) and args["steps"] < s["risers_in_view"]:
        return f"steps_mismatch (counted {s['risers_in_view']} risers to the landing)"
    return ""


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
        """First patrol: 0G decides every move until the whole house is mapped; saves the house map."""
        if self.busy:
            self.ex.estop()
        self.q.put(EXPLORE)
        self.on_patrol()  # mapping the house is recorded the same way as a patrol

    def tour(self):
        """Walk the world's fixed tour legs once, asking 0G about one question per room; saves the house map."""
        if self.busy:
            self.ex.estop()
        self.q.put(TOUR)
        self.on_patrol()

    def _rooms(self):
        rooms = list(getattr(self.adapter, "rooms", None) or [])
        if not rooms and getattr(self.adapter, "world", None) == "house":
            from ..adapters.hexapod_pybullet.house import ROOMS
            rooms = list(ROOMS)
        return rooms

    def _tour_route(self):
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

    def _tour(self):
        legs = self._tour_route()
        if not legs:
            self.emit({"t": time.time(), "type": "explore", "msg": "no tour route for this world"})
            return
        rooms = self._rooms()
        self.ex._estop.clear()
        self.ex.state = "EXPLORE"
        self.emit({"t": time.time(), "type": "explore", "msg": f"guided tour: {len(legs)} legs"})
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
        self.emit({"t": time.time(), "type": "explore", "msg": f"guided tour ended ({why}): {len(m.nodes)} places, "
                   f"rooms {', '.join(m.rooms()) or 'none'}, {len(m.objects)} objects; map saved to {m.path}"})

    def _sensed(self):
        """What the depth camera says about stairs straight ahead (up) and a drop (down), for the model."""
        out = {}
        for key, down in (("step_ahead", False), ("drop_ahead", True)):
            try:
                f = self.adapter.find_step(down=down, size=True)
            except Exception:  # no depth stair sensing on this robot
                f = None
            out[key] = "none" if f is None or abs(f[1]) > 0.075 else {  # higher: a wall or furniture, not a step
                "distance_m": round(f[0], 2), "riser_cm": round(abs(f[1]) * 100, 1),
                "tread_cm": round(f[4] * 100) if f[4] else "not in view",
                "width_cm": round(f[5] * 100) if f[5] else "wider than the view",
                "risers_in_view": f[6] or "not measured",
                "turn_to_square_deg": round(math.degrees(f[2]), 1), "stairs_centre_left_cm": round(f[3] * 100, 1)}
        return out

    def _free(self, m, here, free, st):
        """Free distance by world heading (30 deg bins) at map node `here`, from every look taken there; a heading
        that leads to an already mapped place within that distance is marked 'been there'."""
        for bearing, dist in (self.adapter.clearance() if hasattr(self.adapter, "clearance") else []):
            free.setdefault(here, {})[round((math.degrees(st["yaw"]) + bearing) / 30) * 30 % 360] = dist
        a, out = m.nodes[here], {}
        for h, dist in sorted(free.get(here, {}).items()):
            been = any(0.5 < math.hypot(n["x"] - a["x"], n["y"] - a["y"]) < dist + 0.5 and n["level"] == a["level"] and
                       abs((math.degrees(math.atan2(n["y"] - a["y"], n["x"] - a["x"])) - h + 180) % 360 - 180) < 20
                       for n in m.nodes.values())
            out[str(h)] = f"{dist} m" + (" (been there)" if been else "")
        return out

    def _explore(self):
        rooms, cap = self._rooms(), int(os.environ.get("EXPLORE_MAX_DECISIONS", "150"))
        names = {a["name"]: a for a in self.adapter.manifest()["actions"]}
        manifest = {**self.adapter.manifest(), "actions": [names[a] for a in EXPLORE_ACTIONS if a in names]}
        self.ex._estop.clear()
        self.ex.state = "EXPLORE"
        self.emit({"t": time.time(), "type": "explore", "msg": f"exploring with 0G deciding (up to {cap} decisions)"})
        m = self.ex.map = HouseMap()
        st = self.adapter.state()
        z0, level, history, free, why = st.get("z", 0), 0, [], {}, f"decision cap ({cap})"
        prev = m.add_node(st["x"], st["y"], st.get("z", 0), level)
        for n in range(1, cap + 1):
            if self.ex._estop.is_set():
                why = "stopped by E-STOP or a new command"
                break
            frame, st = self.adapter.get_frame(), self.adapter.state()
            named = m.rooms()
            state = {"x": round(st["x"], 2), "y": round(st["y"], 2), "heading_deg": round(math.degrees(st["yaw"])),
                     "floor_height_cm": round((st.get("z", z0) - z0) * 100), "level": level,
                     "speed_percent": st.get("speed_percent"), **self._sensed(),
                     "free_m_by_heading_here": self._free(m, prev, free, st),
                     "rooms_seen": {r: [m.nodes[k]["x"], m.nodes[k]["y"], m.nodes[k]["level"]] for r, k in named.items()},
                     "rooms_not_seen_yet": [r for r in rooms if r not in named],
                     "places_mapped": len(m.nodes), "legs_mapped": len(m.edges),
                     "climbs_done": [{k: v for k, v in e["params"].items() if k != "yaw"} for e in m.edges if e["how"] == "climb"],
                     "trail": [[v["x"], v["y"], v["level"], v["room"] or "?"] for v in list(m.nodes.values())[-12:]]}
            try:
                d = self.planner.explore(frame, state, history, rooms)
            except OGError as e:
                self.ex._safe_stop(f"0G unavailable: {e}")
                why = "0G unavailable"
                break
            except Exception as e:
                self.ex._safe_stop(f"decision error: {e}")
                why = "decision error"
                break
            plan = str(d.args.pop("plan", "") or "")[:120]
            self.emit({"t": time.time(), "type": "explore", "frame": frame, "request_id": d.request_id,
                       "msg": f"0G #{n}: {d.name} {json.dumps(d.args)} (in the {d.room or '?'}) {plan}"})
            if d.room in rooms:  # the frame was taken here, before the move
                m.nodes[prev]["room"] = d.room
            self.ex._map(d.seen, session=False)
            if d.name == "done":
                why = f"0G: done ({str(d.args.get('reason', ''))[:80]})"
                break
            try:
                name, args = validate(manifest, d.name, d.args)
            except Reject as e:
                self.ex._safe_stop(f"rejected model output: {e}")
                why = "rejected model output"
                break
            drop = state["drop_ahead"]
            unfit = _mismatch(args, drop if args.get("direction") == "down" else state["step_ahead"]) if name == "climb" else ""
            if (name == "approach" or name in ("walk", "crawl") and args["direction"] == "forward") and \
                    isinstance(drop, dict) and drop["distance_m"] < args.get("distance_cm", 150) / 100 + 0.3:
                res = f"refused: stairs going down {drop['distance_m']} m ahead (walking would fall); climb down instead"
            elif unfit:
                res = f"refused: {unfit}"
            elif name == "climb":
                params = {"yaw": round(st["yaw"], 3), **args}
                res = self.ex.climb(params)
            else:
                self.adapter.execute(name, args, names[name].get("timeout_s", 30))
                res = self.ex.wait(names[name].get("timeout_s", 30))
                res = self.adapter.result() if res == "done" else res
            history.append(f"{name} {json.dumps(args)} ({plan}) => {res}")
            if res == "robot fell":
                self.ex._safe_stop("robot fell / out of safe tilt")
            if res in ("robot fell", "stopped by E-STOP or a new command"):
                why = res
                break
            if res in ("stuck", "unstable") and abs(self.adapter.state()["z"] - m.nodes[prev]["z"]) > 0.03:
                # left part-way up the stairs: the map can't link this place
                self.ex._safe_stop(f"climb {res}")
                why = f"climb {res}"
                break
            how = "climb" if name == "climb" and res == "reached" else "crawl" if name == "crawl" else "walk"
            if how == "climb":
                level += 1 if args["direction"] == "up" else -1
            st = self.adapter.state()
            prev = m.add_node(st["x"], st["y"], st.get("z", 0), level, None, prev, how, params if how == "climb" else None)
            m.save()
        m.save()
        self.ex.state = "IDLE" if self.ex.state != "SAFE_STOP" else self.ex.state
        self.emit({"t": time.time(), "type": "explore", "msg": f"exploration ended ({why}) after {n} decisions: "
                   f"{len(m.nodes)} places, {len(m.edges)} legs, rooms {', '.join(m.rooms()) or 'none'}, "
                   f"{len(m.objects)} objects; map saved to {m.path}"})

    def submit(self, text):
        """New command interrupts the current task (the old one stops, then this one runs)."""
        if re.search(r"\bpatrol\b", text, re.I) and not re.search(r"\b(stop|end|cancel)\b", text, re.I):
            return self.patrol()  # a fixed round: no model needed
        if re.search(r"\b(guided )?tour\b", text, re.I) and not re.search(r"\b(stop|end|cancel)\b", text, re.I):
            return self.tour()  # the fixed route with ~1 0G look per room
        if re.search(r"\bexplore\b|\bmap (the|this|my) (house|home|place)\b", text, re.I) and \
                not re.search(r"\b(stop|end|cancel)\b", text, re.I):
            return self.explore()  # 0G decides every move
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
                if text is PATROL:
                    self._patrol()
                elif text is EXPLORE:
                    self._explore()
                elif text is TOUR:
                    self._tour()
                else:
                    self.ex.run_task(text)
            except Exception as e:  # never let a bug leave the robot moving
                self.adapter.stop()
                self.emit({"t": time.time(), "type": "safe_stop", "reason": f"runtime error: {e}"})
            self.busy = False
            last = time.monotonic()

    def close(self):
        self._stop.set()
        self.adapter.stop()
