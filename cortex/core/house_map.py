"""The house map Skitter builds on its exploration run: places it stood, how it got between them, what it saw."""
import heapq
import json
import math
import os


def map_path():
    return os.path.join(os.environ.get("SKITTER_HOME", "episodes"), "map.json")


class HouseMap:
    """nodes: id -> {x, y, z, level, room}; edges: {a, b, how: walk|crawl|climb, params} (climb a -> b as recorded,
    the reverse only when params has a direction to flip); objects: label -> {x, y, room, node}."""
    MERGE_M = 0.4  # a waypoint this close to a known node (same level) is that node

    def __init__(self, path=None):
        self.path = path or map_path()
        self.nodes, self.edges, self.objects = {}, [], {}

    @classmethod
    def load(cls, path=None):
        m = cls(path)
        try:
            with open(m.path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        m.nodes, m.edges, m.objects = d["nodes"], d["edges"], d["objects"]
        return m

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"nodes": self.nodes, "edges": self.edges, "rooms": self.rooms(), "objects": self.objects}, f, indent=1)
        os.replace(tmp, self.path)

    # ---- building ----
    def add_node(self, x, y, z, level, room=None, prev=None, how="walk", params=None):
        """Record a place the robot reached (from node `prev` by `how`); returns its id."""
        near = self.nearest(x, y, level)
        if near is not None and math.hypot(self.nodes[near]["x"] - x, self.nodes[near]["y"] - y) < self.MERGE_M:
            nid = near
            self.nodes[nid]["room"] = self.nodes[nid]["room"] or room
        else:
            nid = f"n{len(self.nodes)}"
            self.nodes[nid] = {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2), "level": level, "room": room}
        if prev is not None and prev != nid and not any({e["a"], e["b"]} == {prev, nid} for e in self.edges):
            self.edges.append({"a": prev, "b": nid, "how": how, **({"params": params} if params else {})})
        return nid

    def add_object(self, label, x, y, level=None):
        n = self.nearest(x, y, level)
        if n is None:
            return
        room, old = self.nodes[n]["room"], self.objects.get(label)
        if old and room and old["room"] and old["room"] != room:  # a second "plant" elsewhere is another plant
            label = f"{label} ({room})"
        self.objects[label] = {"x": round(x, 2), "y": round(y, 2), "room": room, "node": n}

    def nearest(self, x, y, level=None):
        cands = [k for k, n in self.nodes.items() if level is None or n["level"] == level] or list(self.nodes)
        return min(cands, key=lambda k: math.hypot(self.nodes[k]["x"] - x, self.nodes[k]["y"] - y), default=None)

    def level_at(self, x, y, z):
        """Level of the nearest node at the robot's height (gait bob is ~1 cm, the lowest riser 2 cm)."""
        cands = [k for k, n in self.nodes.items() if abs(n["z"] - z) < 0.04] or list(self.nodes)
        return self.nodes[min(cands, key=lambda k: math.hypot(self.nodes[k]["x"] - x, self.nodes[k]["y"] - y)
                              + 5 * abs(self.nodes[k]["z"] - z))]["level"] if self.nodes else 0

    # ---- using ----
    def rooms(self):
        """room -> centre node (its node closest to the mean of the room's nodes)."""
        out = {}
        for r in {n["room"] for n in self.nodes.values() if n["room"]}:
            ks = [k for k, n in self.nodes.items() if n["room"] == r]
            mx, my = (sum(self.nodes[k][c] for k in ks) / len(ks) for c in ("x", "y"))
            out[r] = min(ks, key=lambda k: math.hypot(self.nodes[k]["x"] - mx, self.nodes[k]["y"] - my))
        return out

    def places(self):
        return sorted(self.rooms()) + sorted(o for o in self.objects if o not in self.rooms())

    def target(self, place):
        place = str(place).strip().lower()
        rooms = self.rooms()
        for name in list(rooms) + list(self.objects):
            if name == place:
                return rooms.get(name) or self.objects[name]["node"]
        for name in list(rooms) + list(self.objects):  # "the kitchen", "bedroom two's bed" -> loose match
            if name in place or place in name:
                return rooms.get(name) or self.objects[name]["node"]
        return None

    def _links(self, k):
        for e in self.edges:
            params = e.get("params") or {}
            if e["a"] == k:
                yield e["b"], e["how"], params
            elif e["b"] == k and (e["how"] != "climb" or "direction" in params):
                flip = {"direction": {"up": "down", "down": "up"}.get(params.get("direction"))} if "direction" in params else {}
                yield e["a"], e["how"], {**params, **flip, **({"yaw": params["yaw"] + math.pi} if "yaw" in params else {})}

    def route(self, x, y, z, place):
        """Legs [(how, node_id, params)] from the robot's nearest node to `place`, or None (no_route)."""
        goal = self.target(place)
        if goal is None or not self.nodes:
            return None
        start = self.nearest(x, y, self.level_at(x, y, z))
        dist, prev, todo = {start: 0.0}, {}, [(0.0, start)]
        while todo:
            d, k = heapq.heappop(todo)
            if k == goal:
                break
            if d > dist[k]:
                continue
            for j, how, params in self._links(k):
                a, b = self.nodes[k], self.nodes[j]
                nd = d + math.hypot(a["x"] - b["x"], a["y"] - b["y"]) + (3.0 if how == "climb" else 0.0)
                if nd < dist.get(j, math.inf):
                    dist[j], prev[j] = nd, (k, how, params)
                    heapq.heappush(todo, (nd, j))
        if goal not in dist:
            return None
        legs, k = [], goal
        while k != start:
            k0, how, params = prev[k]
            legs.append((how, k, params))
            k = k0
        return [("walk", start, {})] + legs[::-1]

    def summary(self, x, y, z):
        """Compact layout for the model's State."""
        here = self.nodes[self.nearest(x, y, self.level_at(x, y, z))]
        return {"you_are_in": here["room"] or "unknown", "level": here["level"],
                "rooms": {r: [self.nodes[k]["x"], self.nodes[k]["y"], self.nodes[k]["level"]] for r, k in self.rooms().items()},
                "objects": {o: [v["x"], v["y"], v["room"]] for o, v in list(self.objects.items())[-30:]}}
