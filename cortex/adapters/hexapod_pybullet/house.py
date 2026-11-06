"""Skitter's sim house: a ~100 m^2 modern-minimal bungalow (white walls, oak floors, flat accent colours).

Top view, x right / y up, metres (wall centre lines). Walls 2.6 m tall (real height, so the low camera sees
rooms as they are; pass wall_h=1.2 for an easier GUI view), 10 cm thick; doorways are full-height open
gaps (no leaves, no lintels, no thresholds): the whole interior is the flat ground plane at z = 0, floor
colours are visual-only tints. Scale 1 char = 0.25 m, 1 line = 0.4 m; gaps in walls are doorways.
R> = robot spawn (0, 0) facing +x. P---P = person walking (-0.3, 1.2) <-> (0.7, 1.2). o = plant.

  y
 6.6 +---------------+---------+---------------+
     |  BEDROOM 1    |  BATH   |  BEDROOM 2    |
     |  4.0 x 3.2    | 2.4x3.2 |  4.0 x 3.2    |
     |     [blue bed]|  toilet |desk [pink bed]|
     |               |   shower|               |
     |wardrobe       |sink     |wardrobe       |
     |               |         |  ball         |
     |               |   basket|toy box        | o       +---------+
 3.4 +---------     -+-     ---+--     --------+       +-+  DECK   |
     |o               HALLWAY 10.4 x 1.4          PORCH|6|  +12 cm |
     |                                 shoes      (z=0)| |         |
 2.0 +--     ----------------     ---+---------+       | |         |
     |                               |         | o     +-+         |
     |          P---P                          |         +---------+
     |armchair                           fridge|
     |                               |         |
 0.0 |TV   sofa  R>          chairs  |  KITCHEN|
     |  LIVING 5.4 x 5.0     table   |  2.5x5.0|
     |                 DINING 2.5x5.0|  counter|
     |                       chairs  |         |
     |                               |         |
     |                               |         |
     |plant    lamp shelf  sideboard | counter |
-3.0 +-------------------------------+---------+
    -3           0        2.4       4.9       7.4    9.4 10       12.4  x

Rooms: living 5.4 x 5.0 (open plan to the dining area 2.5 x 5.0), kitchen 2.5 x 5.0 (door from dining at
x = 4.9), hallway 10.4 x 1.4 (front door at its east end), bedroom 1 4.0 x 3.2, bathroom 2.4 x 3.2,
bedroom 2 4.0 x 3.2; total 10.4 x 9.6 = 99.8 m^2. Outside the front door: porch at ground level, then 2
risers of 6 cm going UP in +x: step x 9.4..10.0 (y 1.9..3.5, 1.6 m wide, 6 cm), then the garden deck
x 10.0..12.4, y 1.4..4.0 at 12 cm (the landing).
"""
import pybullet as p

from .arena import PERSON_PARTS, move_person

WALL_H, WALL_T = 2.6, 0.1
HOUSE = (-3.0, -3.0, 7.4, 6.6)  # outer wall centre lines x0, y0, x1, y1
# open living-room floor east of the sofa; the north leg faces the person's walk (y = 1.2)
PATROL = [(0.0, 0.5), (1.9, 0.5), (1.9, -2.0), (0.0, -2.0)]

WALL = [0.94, 0.94, 0.92, 1]
WHITE, BLACK, CHARCOAL, GREY = [0.98, 0.98, 0.98, 1], [0.08, 0.08, 0.08, 1], [0.25, 0.25, 0.28, 1], [0.7, 0.7, 0.72, 1]
OAK, WALNUT, STEEL = [0.8, 0.63, 0.42, 1], [0.42, 0.27, 0.15, 1], [0.6, 0.62, 0.66, 1]
RED, YELLOW, BLUE, NAVY = [0.85, 0.1, 0.1, 1], [1.0, 0.8, 0.05, 1], [0.1, 0.35, 0.95, 1], [0.1, 0.15, 0.5, 1]
PINK, ORANGE, TEAL, GREEN = [1.0, 0.45, 0.7, 1], [1.0, 0.5, 0.05, 1], [0.0, 0.6, 0.6, 1], [0.15, 0.6, 0.2, 1]
TERRACOTTA, CYAN = [0.75, 0.38, 0.22, 1], [0.6, 0.85, 0.95, 1]

ROOMS = {  # name: (x0, y0, x1, y1, floor tint)
    "living room": (-3.0, -3.0, 2.4, 2.0, [0.87, 0.78, 0.64, 1]),
    "dining area": (2.4, -3.0, 4.9, 2.0, [0.87, 0.78, 0.64, 1]),
    "kitchen": (4.9, -3.0, 7.4, 2.0, [0.86, 0.86, 0.83, 1]),
    "hallway": (-3.0, 2.0, 7.4, 3.4, [0.8, 0.72, 0.58, 1]),
    "bedroom 1": (-3.0, 3.4, 1.0, 6.6, [0.82, 0.8, 0.76, 1]),
    "bathroom": (1.0, 3.4, 3.4, 6.6, [0.38, 0.42, 0.46, 1]),
    "bedroom 2": (3.4, 3.4, 7.4, 6.6, [0.82, 0.8, 0.76, 1]),
}
WALLS = [  # (direction the wall runs, fixed coordinate, start, end, doorways as (lo, hi, name))
    ("x", -3.0, -3.0, 7.4, []),
    ("x", 2.0, -3.0, 7.4, [(-2.2, -1.2, "living room door"), (2.9, 4.1, "dining door")]),
    ("x", 3.4, -3.0, 7.4, [(-0.4, 0.6, "bedroom 1 door"), (1.6, 2.5, "bathroom door"), (4.2, 5.2, "bedroom 2 door")]),
    ("x", 6.6, -3.0, 7.4, []),
    ("y", -3.0, -3.0, 6.6, []),
    ("y", 4.9, -3.0, 2.0, [(0.7, 1.7, "kitchen door")]),
    ("y", 1.0, 3.4, 6.6, []),
    ("y", 3.4, 3.4, 6.6, []),
    ("y", 7.4, -3.0, 6.6, [(2.2, 3.2, "front door")]),
]
STEP_RISE, STEP_TREAD, STEP_Y = 0.06, 0.6, (1.9, 3.5)
STEP_X = 9.4  # first riser, climbing in +x from the porch


def B(x0, y0, z0, x1, y1, z1, rgba):
    """Box part by its extents (easier to put flush against walls than centre + half size)."""
    return p.GEOM_BOX, [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2], [(x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2], rgba


def C(x, y, z0, z1, r, rgba):
    return p.GEOM_CYLINDER, [x, y, (z0 + z1) / 2], (r, z1 - z0), rgba


def table(x0, y0, x1, y1, h, top, leg, t=0.03):
    legs = [B(x, y, 0, x + t, y + t, h - 0.04, leg) for x in (x0 + 0.02, x1 - 0.02 - t) for y in (y0 + 0.02, y1 - 0.02 - t)]
    return [B(x0, y0, h - 0.04, x1, y1, h, top)] + legs


def chair(x, y, back, rgba, s=0.21):
    """Seat on thin legs (the low camera sees under it) with the backrest on side back = 'n'/'s'."""
    by = y + (s - 0.02) * (1 if back == "n" else -1)
    return table(x - s, y - s, x + s, y + s, 0.46, rgba, BLACK) + [B(x - s, by - 0.02, 0.46, x + s, by + 0.02, 0.9, rgba)]


def seat(x0, y0, x1, y1, back, rgba, d=0.2):
    """Sofa / armchair facing away from its backrest on side back = 'e'/'w', arms on the y sides."""
    bx = x1 - d if back == "e" else x0
    return [B(x0, y0, 0, x1, y1, 0.42, rgba), B(bx, y0, 0.42, bx + d, y1, 0.85, rgba),
            B(x0, y0, 0.42, x1, y0 + d, 0.62, rgba), B(x0, y1 - d, 0.42, x1, y1, 0.62, rgba)]


def bed(x0, y0, x1, y1, duvet):
    """Headboard on the north (y1) side; solid frame so the robot doesn't try to crawl under."""
    return [B(x0, y0, 0, x1, y1, 0.3, WALNUT), B(x0 + 0.03, y0 + 0.03, 0.3, x1 - 0.03, y1 - 0.45, 0.48, duvet),
            B(x0 + 0.1, y1 - 0.42, 0.3, x1 - 0.1, y1 - 0.1, 0.44, WHITE), B(x0, y1 - 0.08, 0, x1, y1, 1.0, WALNUT)]


def plant(x, y, z=0.0, h=1.1, r=0.22):
    return [C(x, y, z, z + 0.35, 0.16, TERRACOTTA), C(x, y, z + 0.35, z + h, r, GREEN)]


FURNITURE = {  # name: parts; everything static with collision
    # living room: TV on the west wall, sofa facing it, robot spawns just east of the sofa
    "tv unit": [B(-2.93, -1.6, 0, -2.53, 0.0, 0.5, WALNUT), B(-2.84, -1.45, 0.5, -2.79, -0.15, 1.25, BLACK)],
    "red sofa": seat(-1.5, -1.8, -0.6, 0.2, "e", RED),
    "coffee table": table(-2.25, -1.3, -1.75, -0.3, 0.4, OAK, BLACK),
    "yellow armchair": seat(-2.9, 0.5, -2.1, 1.3, "w", YELLOW),
    "orange bookshelf": [B(0.3, -2.93, 0, 1.7, -2.6, 1.8, ORANGE)],
    "floor lamp": [C(-0.3, -2.5, 0, 0.03, 0.15, BLACK), C(-0.3, -2.5, 0.03, 1.35, 0.02, BLACK),
                   C(-0.3, -2.5, 1.35, 1.65, 0.2, WHITE)],
    "green plant": plant(-2.6, -2.6),
    # dining area (open to the living room)
    "dining table": table(2.8, -1.0, 4.2, -0.2, 0.75, OAK, CHARCOAL),
    "teal dining chairs": chair(3.15, -1.35, "s", TEAL) + chair(3.85, -1.35, "s", TEAL)
                          + chair(3.15, 0.15, "n", TEAL) + chair(3.85, 0.15, "n", TEAL),
    "grey sideboard": [B(2.7, -2.93, 0, 4.4, -2.5, 0.8, CHARCOAL)],
    # kitchen: L counter on the south + east walls, fridge in the north-east corner
    "kitchen counter": [B(4.97, -2.93, 0, 7.33, -2.33, 0.86, GREY), B(4.97, -2.93, 0.86, 7.33, -2.33, 0.9, CHARCOAL),
                        B(6.73, -2.33, 0, 7.33, 0.3, 0.86, GREY), B(6.73, -2.33, 0.86, 7.33, 0.3, 0.9, CHARCOAL),
                        B(5.7, -2.83, 0.9, 6.3, -2.43, 0.91, BLACK), B(6.83, -0.9, 0.85, 7.23, -0.4, 0.91, STEEL),
                        B(4.97, -2.93, 1.5, 7.33, -2.6, 2.2, GREY)],
    "blue fridge": [B(6.68, 0.45, 0, 7.33, 1.1, 1.85, BLUE)],
    "steel bin": [C(6.4, 1.75, 0, 0.5, 0.15, STEEL)],
    # hallway: front door leaf swung open against the north wall, shoe rack opposite
    "front door": [B(6.33, 3.28, 0.01, 7.33, 3.33, 2.06, CHARCOAL)],
    "shoe rack": [B(5.4, 2.07, 0, 6.3, 2.32, 0.5, WALNUT), B(5.5, 2.1, 0.5, 5.8, 2.29, 0.6, RED),
                  B(5.95, 2.1, 0.5, 6.25, 2.29, 0.6, BLUE)],
    "hallway plant": plant(-2.7, 2.7, h=1.3, r=0.18),
    # bedroom 1: double bed on the north wall
    "blue bed": bed(-2.2, 4.5, -0.6, 6.53, NAVY),
    "bedside tables": [B(-2.65, 6.1, 0, -2.25, 6.5, 0.5, OAK), B(-0.55, 6.1, 0, -0.15, 6.5, 0.5, OAK)],
    "bedroom 1 wardrobe": [B(-2.93, 3.6, 0, -2.33, 5.0, 2.0, OAK)],
    "bedroom 1 plant": plant(0.6, 6.2),
    # bathroom
    "toilet": [B(1.45, 5.85, 0, 1.85, 6.3, 0.4, WHITE), B(1.4, 6.3, 0, 1.9, 6.53, 0.8, WHITE),
               B(1.45, 5.85, 0.4, 1.85, 6.3, 0.43, GREY)],
    "sink": [B(1.07, 4.2, 0, 1.55, 5.0, 0.8, WALNUT), B(1.12, 4.35, 0.8, 1.5, 4.85, 0.9, WHITE),
             B(1.07, 4.3, 1.2, 1.09, 4.9, 1.8, STEEL)],
    "shower": [B(2.45, 5.65, 0, 3.33, 6.53, 2.0, CYAN)],
    "blue laundry basket": [B(3.0, 3.6, 0, 3.3, 3.9, 0.45, BLUE)],
    # bedroom 2: single bed, desk, toys on the floor
    "pink bed": bed(6.2, 4.5, 7.3, 6.53, PINK),
    "bedroom 2 wardrobe": [B(3.47, 5.0, 0, 4.07, 6.5, 2.0, OAK)],
    "white desk": table(4.5, 6.0, 5.7, 6.53, 0.75, WHITE, BLACK),
    "desk chair": chair(5.1, 5.7, "s", TEAL),
    "yellow toy box": [B(3.5, 3.5, 0, 3.9, 3.85, 0.3, YELLOW)],
    "orange ball": [(p.GEOM_SPHERE, [5.6, 4.6, 0.12], 0.12, ORANGE)],
    # porch (ground level) and the raised garden deck behind the steps
    "porch plants": plant(7.8, 1.8) + plant(7.8, 3.8),
    "garden bench": [B(11.9, 2.2, 0.12, 12.35, 3.2, 0.55, CHARCOAL), B(12.25, 2.2, 0.55, 12.35, 3.2, 0.95, CHARCOAL)],
    "deck plants": plant(12.1, 1.7, z=0.12) + plant(12.1, 3.7, z=0.12),
}
STEPS = [B(STEP_X, STEP_Y[0], 0, STEP_X + STEP_TREAD, STEP_Y[1], STEP_RISE, [0.72, 0.7, 0.64, 1]),
         B(STEP_X + STEP_TREAD, 1.4, 0, 12.4, 4.0, 2 * STEP_RISE, [0.55, 0.38, 0.22, 1])]  # deck = landing
DECOR = {  # visual only (no collision), a few mm thick so the floor stays perfectly flat for the robot
    "grass": [B(-15, -15, 0, 25, 18, 0.001, [0.45, 0.65, 0.35, 1])],
    "floors": [B(x0, y0, 0, x1, y1, 0.002, rgba) for x0, y0, x1, y1, rgba in ROOMS.values()],
    "porch": [B(7.45, 1.4, 0, STEP_X, 4.0, 0.002, [0.62, 0.62, 0.6, 1]), B(7.5, 2.3, 0, 8.0, 3.1, 0.004, WALNUT)],
    "blue rug": [B(-2.45, -1.9, 0, -1.55, 0.3, 0.004, [0.2, 0.3, 0.7, 1])],
    "bath mat": [B(1.7, 5.1, 0, 2.3, 5.5, 0.004, WHITE)],
}


def doorways():
    """(name, direction, fixed coordinate, lo, hi) for every doorway in WALLS."""
    return [(name, d, c, lo, hi) for d, c, _, _, gaps in WALLS for lo, hi, name in gaps]


def _body(kind, pos, size, rgba, collide=True):
    if kind == p.GEOM_BOX:
        vis = col = dict(halfExtents=size)
    elif kind == p.GEOM_CYLINDER:  # visual takes length=, collision takes height=
        vis, col = dict(radius=size[0], length=size[1]), dict(radius=size[0], height=size[1])
    else:
        vis = col = dict(radius=size)
    shape = p.createCollisionShape(kind, **col) if collide else -1
    return p.createMultiBody(0, shape, p.createVisualShape(kind, rgbaColor=rgba, **vis), pos)


def _walls(h):
    parts = []
    for d, c, a, b, gaps in WALLS:
        cuts = [a - WALL_T / 2] + [v for lo, hi, _ in gaps for v in (lo, hi)] + [b + WALL_T / 2]
        for i in range(0, len(cuts), 2):
            s0, s1 = cuts[i], cuts[i + 1]
            box = (s0, c - WALL_T / 2, 0, s1, c + WALL_T / 2, h) if d == "x" else (c - WALL_T / 2, s0, 0, c + WALL_T / 2, s1, h)
            parts.append(B(*box, WALL))
    return parts


def build_house(wall_h=WALL_H):
    """Build the house around the robot spawn; returns name -> body id list (like build_arena)."""
    objs = {name: [_body(*part, collide=False) for part in parts] for name, parts in DECOR.items()}
    objs["walls"] = [_body(*part) for part in _walls(wall_h)]
    objs.update({name: [_body(*part) for part in parts] for name, parts in FURNITURE.items()})
    objs["steps"] = [_body(*part) for part in STEPS]
    objs["person"] = []  # walks arena.PERSON_PATH, which lies in free living-room floor
    for shape, size, z, rgba in PERSON_PARTS:
        kw = {"radius": size} if shape == p.GEOM_SPHERE else {"halfExtents": size}
        objs["person"].append((p.createMultiBody(0, p.createCollisionShape(shape, **kw),
                                                 p.createVisualShape(shape, rgbaColor=rgba, **kw)), z))
    move_person(objs["person"], 0)
    return objs
