"""Skitter's sim home: a red box ahead, blue to the left, green to the right, a wall, and a person
who walks slowly back and forth on the left (so 'come to me' / 'follow me' have someone to find)."""
import math

import pybullet as p

PERSON_PATH = ((-0.3, 1.2), (0.7, 1.2))
PATROL = [(0.4, 0.3), (0.4, -0.3), (-0.2, -0.3), (-0.2, 0.3)]  # security round between the boxes, looped
PERSON_SPEED = 0.03  # m/s, slower than Skitter (~0.1 m/s) so it can keep up
PERSON_PARTS = [  # (shape, size, height of centre, colour)
    (p.GEOM_BOX, [0.07, 0.12, 0.25], 0.25, [0.5, 0.1, 0.6, 1]),    # legs: purple trousers (a low robot sees legs)
    (p.GEOM_BOX, [0.08, 0.16, 0.22], 0.72, [1.0, 0.8, 0.0, 1]),    # torso: yellow shirt
    (p.GEOM_SPHERE, 0.09, 1.03, [0.95, 0.75, 0.6, 1]),             # head
]


def _box(pos, half, rgba, mass=0):
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=half)
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=rgba)
    return p.createMultiBody(mass, col, vis, pos)


def build_arena():
    objs = {
        "red box": _box([1.0, 0.0, 0.1], [0.1, 0.1, 0.1], [1, 0, 0, 1]),
        "blue box": _box([0.8, 0.6, 0.1], [0.1, 0.1, 0.1], [0, 0.2, 1, 1]),
        "green box": _box([0.8, -0.6, 0.1], [0.1, 0.1, 0.1], [0, 0.8, 0.2, 1]),
        "wall": _box([2.0, 0.0, 0.2], [0.02, 1.0, 0.2], [0.8, 0.8, 0.8, 1]),
        # stairs behind the start: 2 steps of 6 cm, 26 cm treads = half the robot's ~52 cm footprint (too shallow
        # head-on, so it climbs side-on with tucked legs: see skills.py), 70 cm wide, first riser at x = -0.6
        "stairs": [_box([-0.73, 0.0, 0.03], [0.13, 0.35, 0.03], [0.75, 0.55, 0.35, 1]),
                   _box([-1.21, 0.0, 0.06], [0.35, 0.35, 0.06], [0.65, 0.45, 0.28, 1])],
    }
    objs["person"] = []
    for shape, size, z, rgba in PERSON_PARTS:
        kw = {"radius": size} if shape == p.GEOM_SPHERE else {"halfExtents": size}
        objs["person"].append((p.createMultiBody(0, p.createCollisionShape(shape, **kw),
                                                 p.createVisualShape(shape, rgbaColor=rgba, **kw)), z))
    move_person(objs["person"], 0)
    return objs


def move_person(parts, step, hz=240):
    """Ping-pong along PERSON_PATH; a pure function of the sim step, so replays see the same walk."""
    (ax, ay), (bx, by) = PERSON_PATH
    length = math.hypot(bx - ax, by - ay)
    s = (PERSON_SPEED * step / hz) % (2 * length)
    out = s <= length
    f = (s if out else 2 * length - s) / length
    x, y = ax + (bx - ax) * f, ay + (by - ay) * f
    q = p.getQuaternionFromEuler((0, 0, math.atan2(by - ay, bx - ax) + (0 if out else math.pi)))
    for body, z in parts:
        p.resetBasePositionAndOrientation(body, [x, y, z], q)
