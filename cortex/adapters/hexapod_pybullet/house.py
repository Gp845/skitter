"""Skitter's sim house: a ~100 m^2 modern-minimal bungalow (white walls, oak floors, flat accent colours),
plus a west wing with a robot-scale loft.

Top view, x right / y up, metres (wall centre lines). Walls 2.6 m tall (real height, so the low camera sees
rooms as they are; pass wall_h=1.2 for an easier GUI view), 10 cm thick; doorways are full-height open
gaps (no leaves, no lintels, no thresholds): the whole interior is the flat ground plane at z = 0, floor
colours are visual-only tints. Scale 1 char = 0.25 m, 1 line = 0.4 m; gaps in walls are doorways.
R> = robot spawn (0, 0) facing +x. P---P = person walking (-0.3, 1.2) <-> (0.7, 1.2). o = plant. = = vent.

  y
 6.6 +---------------+---------+---------------+
     |  BEDROOM 1    |  BATH   |  BEDROOM 2    |
     |  4.0 x 3.2    | 2.4x3.2 |  4.0 x 3.2    |
     |     [blue bed]|  toilet |desk [pink bed]|
     |               |   shower|               |
     |wardrobe       |sink     =wardrobe       |
     |               |         =  ball         |
     |               |   basket|toy box        | o       +---------+
 3.4 +---------     -+-     ---+--     --------+       +-+  DECK   |
                      HALLWAY 10.4 x 1.4          PORCH|6|  +12 cm |
 stair hall door                       shoes      (z=0)| |         |
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

Vent: a 16 cm high, 70 cm wide, 1 m long duct through the bathroom / bedroom 2 wall (x = 3.4, y 4.15..4.85,
duct x 2.9..3.9): no use to a human; Skitter is 16.7 cm tall walking (its knees), so it must crawl (14.9 cm).

West wing (same scale across, 1 line = 0.5 m): a stair hall off the hallway's west end (door x = -3.0,
y 2.3..3.2) and a LOFT at z = 54 cm, as high as Skitter goes: three narrow flights (30 cm wide, axis
x = -4.5, 3 risers of 6 cm, 55 cm treads; first risers y = 3.55, 5.65, 7.75) climb +y with 1 m landings
(18 and 36 cm, 1 m wide, side rails) between them; the loft's edge is the last riser (y = 8.85). The loft
rooms have walls all round (door to the stairs x -4.95..-4.05), so nothing up there is an open edge.

  12.1 +---------+---------+
       |shelf    |    chest|    LOFT STUDY    2.5 x 3.2 (z 54 cm)
       |desk     |         |    LOFT PLAYROOM 2.5 x 3.2 (z 54 cm)
       |  LOFT        LOFT o|
       |  STUDY  |PLAYROOM |
       |armchair |    bean |
   8.9 +----   --+---------+
       |     |#|   F3: 36 -> 54 cm
   7.8 |    [###]  landing 2, 36 cm
       |    [###]
       |     |#|   F2: 18 -> 36 cm
   5.7 |    [###]  landing 1, 18 cm
       |    [###]
       |     |#|   F1: 0 -> 18 cm
   3.6 |     |#|
       |      ^ climb +y
       |   STAIR HALL       door to the hallway
       |o                   (house west wall)
   1.4 +-----------+
      -6   -4.5   -3.5  -3   -1  x

ROOMS (with floor z), LEVELS and room_at() name places on both levels. EXPLORE is a route from the spawn
through every room (the vent, up the loft stairs and back down) for the navigation map; scripts/test_explore.py
drives it with the real skills.
"""
import math

import pybullet as p

from .arena import PERSON_PARTS, move_person

WALL_H, WALL_T = 2.6, 0.1
HOUSE = (-3.0, -3.0, 7.4, 6.6)  # outer wall centre lines x0, y0, x1, y1 (the bungalow; the west wing is extra)
# open living-room floor east of the sofa; the north leg faces the person's walk (y = 1.2)
PATROL = [(0.0, 0.5), (1.9, 0.5), (1.9, -2.0), (0.0, -2.0)]

WALL = [0.94, 0.94, 0.92, 1]
WHITE, BLACK, CHARCOAL, GREY = [0.98, 0.98, 0.98, 1], [0.08, 0.08, 0.08, 1], [0.25, 0.25, 0.28, 1], [0.7, 0.7, 0.72, 1]
OAK, WALNUT, STEEL = [0.8, 0.63, 0.42, 1], [0.42, 0.27, 0.15, 1], [0.6, 0.62, 0.66, 1]
RED, YELLOW, BLUE, NAVY = [0.85, 0.1, 0.1, 1], [1.0, 0.8, 0.05, 1], [0.1, 0.35, 0.95, 1], [0.1, 0.15, 0.5, 1]
PINK, ORANGE, TEAL, GREEN = [1.0, 0.45, 0.7, 1], [1.0, 0.5, 0.05, 1], [0.0, 0.6, 0.6, 1], [0.15, 0.6, 0.2, 1]
TERRACOTTA, CYAN, PURPLE = [0.75, 0.38, 0.22, 1], [0.6, 0.85, 0.95, 1], [0.5, 0.2, 0.7, 1]

LOFT_Z = 0.54
LEVELS = {"ground floor": 0.0, "loft": LOFT_Z}  # floor z of each level
ROOMS = {  # name: (x0, y0, x1, y1, floor z, floor tint)
    "living room": (-3.0, -3.0, 2.4, 2.0, 0.0, [0.87, 0.78, 0.64, 1]),
    "dining area": (2.4, -3.0, 4.9, 2.0, 0.0, [0.87, 0.78, 0.64, 1]),
    "kitchen": (4.9, -3.0, 7.4, 2.0, 0.0, [0.86, 0.86, 0.83, 1]),
    "hallway": (-3.0, 2.0, 7.4, 3.4, 0.0, [0.8, 0.72, 0.58, 1]),
    "bedroom 1": (-3.0, 3.4, 1.0, 6.6, 0.0, [0.82, 0.8, 0.76, 1]),
    "bathroom": (1.0, 3.4, 3.4, 6.6, 0.0, [0.38, 0.42, 0.46, 1]),
    "bedroom 2": (3.4, 3.4, 7.4, 6.6, 0.0, [0.82, 0.8, 0.76, 1]),
    "stair hall": (-6.0, 1.4, -3.0, 8.9, 0.0, [0.78, 0.76, 0.72, 1]),
    "loft study": (-6.0, 8.9, -3.5, 12.1, LOFT_Z, [0.85, 0.75, 0.6, 1]),
    "loft playroom": (-3.5, 8.9, -1.0, 12.1, LOFT_Z, [0.75, 0.85, 0.8, 1]),
}
VENT_H = 0.16
WALLS = [  # (direction the wall runs, fixed coordinate, start, end, doorways (lo, hi, name) / vents (lo, hi, name, top))
    ("x", -3.0, -3.0, 7.4, []),
    ("x", 2.0, -3.0, 7.4, [(-2.2, -1.2, "living room door"), (2.9, 4.1, "dining door")]),
    ("x", 3.4, -3.0, 7.4, [(-0.4, 0.6, "bedroom 1 door"), (1.6, 2.5, "bathroom door"), (4.2, 5.2, "bedroom 2 door")]),
    ("x", 6.6, -3.0, 7.4, []),
    ("y", -3.0, -3.0, 8.8, [(2.3, 3.2, "stair hall door")]),  # north of 6.6: the stair hall's east wall
    ("y", 4.9, -3.0, 2.0, [(0.7, 1.7, "kitchen door")]),
    ("y", 1.0, 3.4, 6.6, []),
    ("y", 3.4, 3.4, 6.6, [(4.15, 4.85, "bathroom vent", VENT_H)]),
    ("y", 7.4, -3.0, 6.6, [(2.2, 3.2, "front door")]),
    ("x", 1.4, -6.0, -3.0, []),  # stair hall; its side walls stop at the loft's edge (y = 8.85)
    ("y", -6.0, 1.4, 8.8, []),
]
LOFT_WALLS = [  # same format, standing on the loft floor
    ("x", 8.9, -6.0, -1.0, [(-4.95, -4.05, "loft stairs")]),
    ("x", 12.1, -6.0, -1.0, []),
    ("y", -6.0, 8.9, 12.1, []),
    ("y", -1.0, 8.9, 12.1, []),
    ("y", -3.5, 8.9, 12.1, [(9.9, 10.8, "playroom door")]),
]
STEP_RISE, STEP_TREAD, STEP_Y = 0.06, 0.6, (1.9, 3.5)
STEP_X = 9.4  # first riser, climbing in +x from the porch
STAIR_X, STAIR_W, STAIR_TREAD, FLIGHT_Y = -4.5, 0.3, 0.55, (3.55, 5.65, 7.75)  # loft stairs: axis, width, first risers
CLIMB = dict(step_height_cm=6, steps=3, tread_cm=55, width_cm=30)  # one flight, as the climb action takes it
UP, DOWN = dict(CLIMB, direction="up", yaw=math.pi / 2), dict(CLIMB, direction="down", yaw=-math.pi / 2)
# Mapping run from the spawn through every room. ("goto", x, y): straight walk, line of sight clear;
# ("crawl", x, y): the same crouched; ("climb", params): the robot already stands 0.6-0.7 m before the edge
# facing it (yaw = that heading, for executors that turn first; the climb action itself ignores it).
EXPLORE = [
    ("goto", 1.5, 0.0), ("goto", 3.5, 1.2), ("goto", 5.6, 1.2),                           # living, dining, kitchen
    ("goto", 3.5, 1.2), ("goto", 3.5, 2.7), ("goto", 4.7, 2.7), ("goto", 4.7, 4.3),        # hallway, bedroom 2
    ("goto", 4.9, 4.5), ("goto", 4.5, 4.5), ("crawl", 2.1, 4.5),                          # vent -> bathroom
    ("goto", 2.05, 2.7), ("goto", 0.1, 2.75), ("goto", 0.1, 4.2), ("goto", 0.1, 2.75),    # bedroom 1
    ("goto", -2.5, 2.75), ("goto", -3.5, 2.75),                                           # square up at the door
    ("goto", -4.5, 2.75), ("goto", -4.5, 2.95),                                           # stair hall
    ("climb", UP), ("climb", UP), ("climb", UP),                                         # 3 flights to the loft
    ("goto", -4.75, 10.5), ("goto", -2.25, 10.35),                                        # loft study, playroom
    ("goto", -3.9, 10.35), ("goto", -4.5, 10.0), ("goto", -4.5, 9.5),
    ("climb", DOWN), ("climb", DOWN), ("climb", DOWN),                                   # back down
    ("goto", -4.5, 2.75), ("goto", -3.5, 2.75), ("goto", -2.5, 2.75), ("goto", -1.7, 2.75), ("goto", -1.7, 0.9), ("goto", -0.2, 0.5), ("goto", 0.0, 0.0),
]


def B(x0, y0, z0, x1, y1, z1, rgba):
    """Box part by its extents (easier to put flush against walls than centre + half size)."""
    return p.GEOM_BOX, [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2], [(x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2], rgba


def C(x, y, z0, z1, r, rgba):
    return p.GEOM_CYLINDER, [x, y, (z0 + z1) / 2], (r, z1 - z0), rgba


def up(parts, z=LOFT_Z):
    return [(kind, [pos[0], pos[1], pos[2] + z], size, rgba) for kind, pos, size, rgba in parts]


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


def duct(c, lo, hi, top, length=1.0, t=0.05):
    """Crawl duct through the 'y' wall at x = c, open over y lo..hi; stops at the wall faces (the lintel is wall)."""
    parts = []
    for x0, x1 in ((c - length / 2, c - WALL_T / 2), (c + WALL_T / 2, c + length / 2)):
        parts += [B(x0, lo - t, top, x1, hi + t, top + 0.04, GREY), B(x0, lo - t, 0, x1, lo, top, GREY),
                  B(x0, hi, 0, x1, hi + t, top, GREY)]
    return parts


def stairs():
    """The narrow flights (2 treads each; the 3rd riser is the edge of the next landing / the loft) and landings."""
    parts = []
    for f, y0 in enumerate(FLIGHT_Y):
        parts += [B(STAIR_X - STAIR_W / 2, y0 + i * STAIR_TREAD, 0, STAIR_X + STAIR_W / 2, y0 + (i + 1) * STAIR_TREAD,
                    (3 * f + i + 1) * STEP_RISE, [0.72, 0.7, 0.64, 1]) for i in range(2)]
        if f < 2:
            parts.append(B(STAIR_X - 0.5, y0 + 2 * STAIR_TREAD, 0, STAIR_X + 0.5, y0 + 2 * STAIR_TREAD + 1.0,
                           3 * (f + 1) * STEP_RISE, [0.65, 0.5, 0.35, 1]))
    return parts + [B(-6.05, 8.85, 0, -0.95, 12.15, LOFT_Z, [0.65, 0.5, 0.35, 1])]  # loft floor, under its walls


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
    "vent duct": duct(3.4, 4.15, 4.85, VENT_H),
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
    # stair hall: rails along both sides of each landing
    "stair hall plant": plant(-5.6, 1.85, h=1.3, r=0.18),
    "landing rails": [B(x0, y0 + 2 * STAIR_TREAD, 0, x0 + 0.05, y0 + 2 * STAIR_TREAD + 1.0, 3 * (f + 1) * STEP_RISE + 0.7,
                        CHARCOAL) for f, y0 in enumerate(FLIGHT_Y[:2]) for x0 in (STAIR_X - 0.55, STAIR_X + 0.5)],
    # loft study and playroom
    "loft desk": up(table(-5.93, 11.0, -5.33, 12.03, 0.72, WHITE, BLACK)),
    "loft bookshelf": up([B(-5.2, 11.75, 0, -3.65, 12.03, 1.6, WALNUT)]),
    "teal reading chair": up(seat(-5.93, 9.3, -5.2, 10.0, "w", TEAL)),
    "red toy chest": up([B(-1.65, 11.45, 0, -1.07, 12.03, 0.45, RED)]),
    "purple bean bag": up([C(-1.6, 9.5, 0, 0.35, 0.3, PURPLE)]),
    "loft plant": plant(-1.35, 10.6, z=LOFT_Z),
}
STEPS = [B(STEP_X, STEP_Y[0], 0, STEP_X + STEP_TREAD, STEP_Y[1], STEP_RISE, [0.72, 0.7, 0.64, 1]),
         B(STEP_X + STEP_TREAD, 1.4, 0, 12.4, 4.0, 2 * STEP_RISE, [0.55, 0.38, 0.22, 1])]  # deck = landing
DECOR = {  # visual only (no collision), a few mm thick so the floor stays perfectly flat for the robot
    "grass": [B(-15, -15, 0, 25, 18, 0.001, [0.45, 0.65, 0.35, 1])],
    "floors": [B(x0, y0, z, x1, y1, z + 0.002, rgba) for x0, y0, x1, y1, z, rgba in ROOMS.values()],
    "porch": [B(7.45, 1.4, 0, STEP_X, 4.0, 0.002, [0.62, 0.62, 0.6, 1]), B(7.5, 2.3, 0, 8.0, 3.1, 0.004, WALNUT)],
    "blue rug": [B(-2.45, -1.9, 0, -1.55, 0.3, 0.004, [0.2, 0.3, 0.7, 1])],
    "bath mat": [B(1.7, 5.1, 0, 2.3, 5.5, 0.004, WHITE)],
    "play rug": up([B(-3.0, 9.6, 0, -2.0, 10.9, 0.004, [0.95, 0.75, 0.2, 1])]),
}


def room_at(x, y, z=0.0):
    """Name of the room at (x, y) on the level nearest to height z (stairs and landings count as the stair hall)."""
    hits = [(abs(z - rz), n) for n, (x0, y0, x1, y1, rz, _) in ROOMS.items() if x0 <= x <= x1 and y0 <= y <= y1]
    return min(hits)[1] if hits else None


def doorways():
    """(name, direction, fixed coordinate, lo, hi) for every ground-floor doorway in WALLS (not vents)."""
    return [(g[2], d, c, g[0], g[1]) for d, c, _, _, gaps in WALLS for g in gaps if len(g) == 3]


def _body(kind, pos, size, rgba, collide=True):
    if kind == p.GEOM_BOX:
        vis = col = dict(halfExtents=size)
    elif kind == p.GEOM_CYLINDER:  # visual takes length=, collision takes height=
        vis, col = dict(radius=size[0], length=size[1]), dict(radius=size[0], height=size[1])
    else:
        vis = col = dict(radius=size)
    shape = p.createCollisionShape(kind, **col) if collide else -1
    return p.createMultiBody(0, shape, p.createVisualShape(kind, rgbaColor=rgba, **vis), pos)


def _walls(h, walls=WALLS, z0=0.0):
    parts = []
    for d, c, a, b, gaps in walls:
        cuts = [a - WALL_T / 2] + [v for g in gaps for v in g[:2]] + [b + WALL_T / 2]
        spans = [(cuts[i], cuts[i + 1], z0) for i in range(0, len(cuts), 2)] + [(g[0], g[1], g[3]) for g in gaps if len(g) == 4]
        for s0, s1, z in spans:  # wall segments between the gaps, and the lintel over each vent
            box = (s0, c - WALL_T / 2, z, s1, c + WALL_T / 2, z0 + h) if d == "x" else (c - WALL_T / 2, s0, z, c + WALL_T / 2, s1, z0 + h)
            parts.append(B(*box, WALL))
    return parts


def build_house(wall_h=WALL_H):
    """Build the house around the robot spawn; returns name -> body id list (like build_arena)."""
    objs = {name: [_body(*part, collide=False) for part in parts] for name, parts in DECOR.items()}
    objs["walls"] = [_body(*part) for part in _walls(wall_h) + _walls(wall_h - LOFT_Z, LOFT_WALLS, LOFT_Z)]
    objs.update({name: [_body(*part) for part in parts] for name, parts in FURNITURE.items()})
    objs["steps"] = [_body(*part) for part in STEPS + stairs()]
    objs["person"] = []  # walks arena.PERSON_PATH, which lies in free living-room floor
    for shape, size, z, rgba in PERSON_PARTS:
        kw = {"radius": size} if shape == p.GEOM_SPHERE else {"halfExtents": size}
        objs["person"].append((p.createMultiBody(0, p.createCollisionShape(shape, **kw),
                                                 p.createVisualShape(shape, rgbaColor=rgba, **kw)), z))
    move_person(objs["person"], 0)
    return objs
