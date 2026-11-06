"""Skitter's house world: spawn free, doorways >= 0.8 m with no thresholds, hallway >= 1.0 m, steps climbable,
person path clear, nothing through walls. Renders docs/house_top.png, house_view.png, house_robot.png. No 0G calls."""
import math
import os
import sys

import numpy as np
import pybullet as p
import pybullet_data
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from cortex.adapters.hexapod_pybullet import house  # noqa: E402
from cortex.adapters.hexapod_pybullet.arena import PERSON_PATH  # noqa: E402

p.connect(p.DIRECT)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
plane = p.loadURDF("plane.urdf")
objs = house.build_house()
person = {b for b, _ in objs["person"]}
solid = [(n, b) for n, ids in objs.items() if n != "person" for b in ids if p.getCollisionShapeData(b, -1)]
walls = set(objs["walls"])


def blockers(x0, y0, x1, y1, skip=(), z0=0.005):
    """Solid bodies whose AABB enters the box [x0,x1] x [y0,y1] above z0."""
    hit = []
    for n, b in solid:
        lo, hi = p.getAABB(b)
        if b not in skip and lo[0] < x1 and hi[0] > x0 and lo[1] < y1 and hi[1] > y0 and hi[2] > z0:
            hit.append(n)
    return hit


def ray_len(a, b):
    r = p.rayTest(a, b)[0]
    return r[2] * math.dist(a, b) if r[0] >= 0 else math.dist(a, b)


def height(x, y):
    r = p.rayTest([x, y, 1.0], [x, y, -0.1])[0]
    return r[3][2] if r[0] >= 0 else 0.0


x0, y0, x1, y1 = house.HOUSE
area = (x1 - x0) * (y1 - y0)
print(f"interior {x1 - x0:.1f} x {y1 - y0:.1f} = {area:.1f} m^2, {len(solid)} solid bodies")
assert 90 <= area <= 110

assert not blockers(-0.3, -0.3, 0.3, 0.3), f"spawn blocked by {blockers(-0.3, -0.3, 0.3, 0.3)}"
print("spawn (0,0) clear in a 0.6 x 0.6 m square")

for name, d, c, lo, hi in house.doorways():
    m = (lo + hi) / 2
    pt = (lambda s, t, z: [s, t, z]) if d == "x" else (lambda s, t, z: [t, s, z])  # s along wall, t across
    width = ray_len(pt(m, c, 0.1), pt(m - 2, c, 0.1)) + ray_len(pt(m, c, 0.1), pt(m + 2, c, 0.1))
    floor = max(0.0, *(height(*pt(m, c + t, 0)[:2]) for t in np.linspace(-0.5, 0.5, 21)))
    pad = []
    for t0, t1 in ((-0.55, -0.05), (0.05, 0.55)):  # 0.5 m deep approach zone on each side
        a, b = pt(lo + 0.05, c + t0, 0), pt(hi - 0.05, c + t1, 0)
        pad += blockers(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
    print(f"  {name:16s} width {width:.2f} m  floor bump {floor * 100:.1f} cm  approach {'clear' if not pad else pad}")
    assert width >= 0.8 and floor < 0.005 and not pad, name

narrow = min((ray_len([x, 2.7, 0.1], [x, 0.0, 0.1]) + ray_len([x, 2.7, 0.1], [x, 5.0, 0.1]), x)
             for x in np.arange(-2.2, 7.35, 0.05))
print(f"hallway narrowest clear width {narrow[0]:.2f} m at x={narrow[1]:.2f} (west of the living door is a dead end)")
assert narrow[0] >= 1.0

ym = sum(house.STEP_Y) / 2
xs = np.arange(8.5, 11.8, 0.01)  # stop before the garden bench on the deck
hs = [height(x, ym) for x in xs]
risers = [(xs[i], hs[i] - hs[i - 1]) for i in range(1, len(xs)) if abs(hs[i] - hs[i - 1]) > 0.005]
assert len(risers) == 2 and all(abs(r - 0.06) < 0.003 for _, r in risers), risers
tread = risers[1][0] - risers[0][0]
landing = next((x for x, h in zip(xs, hs) if x > risers[1][0] and h < 0.11), xs[-1]) - risers[1][0]
ys = np.arange(1.0, 4.5, 0.01)
width = sum(abs(height(risers[0][0] + tread / 2, y) - 0.06) < 0.003 for y in ys) * 0.01
porch = max(height(x, ym) for x in np.arange(7.5, risers[0][0] - 0.02, 0.05))
print(f"steps: risers at x={risers[0][0]:.2f}, {risers[1][0]:.2f} (+x), heights "
      f"{[round(r * 100, 1) for _, r in risers]} cm, tread {tread:.2f} m, width {width:.2f} m, "
      f"landing {landing:.2f} m deep at 12 cm, porch bump {porch * 100:.1f} cm")
assert tread >= 0.55 and width >= 0.6 and landing >= 0.55 and porch < 0.005

(ax, ay), (bx, by) = PERSON_PATH
for f in np.linspace(0, 1, 11):
    x, y = ax + (bx - ax) * f, ay + (by - ay) * f
    hit = blockers(x - 0.3, y - 0.3, x + 0.3, y + 0.3, skip=person)
    assert not hit, f"person path blocked at ({x:.2f},{y:.2f}) by {hit}"
print(f"person path {PERSON_PATH} clear by 0.3 m on each side")

for n, b in solid:
    if b in walls:
        continue
    lo, hi = p.getAABB(b)
    for w in walls:
        wl, wh = p.getAABB(w)
        if all(lo[i] + 0.01 < wh[i] and hi[i] - 0.01 > wl[i] for i in range(3)):
            raise AssertionError(f"{n} goes into a wall")
print("no furniture inside walls")

os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)


def shot(fname, eye, target, up, fov, size=(1024, 768), near=0.1, far=80):
    w, h = size
    img = p.getCameraImage(w, h, p.computeViewMatrix(eye, target, up), p.computeProjectionMatrixFOV(fov, w / h, near, far),
                           renderer=p.ER_TINY_RENDERER, lightDirection=[2, -1, 5], shadow=0)[2]
    Image.fromarray(np.reshape(img, (h, w, 4))[:, :, :3].astype(np.uint8)).save(os.path.join(ROOT, "docs", fname))


shot("house_top.png", [4.7, 1.8, 60], [4.7, 1.8, 0], [0, 1, 0], 12.5, near=50, far=61)
shot("house_view.png", [11.5, -9.5, 12], [3.0, 1.5, 0], [0, 0, 1], 55)
shot("house_robot.png", [0, 0, 0.17], [1, 0, 0.17], [0, 0, 1], 70, size=(320, 240), near=0.02, far=10)
print("rendered docs/house_top.png, docs/house_view.png, docs/house_robot.png")
p.disconnect()
print("house OK")
