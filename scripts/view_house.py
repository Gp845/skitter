"""Fly around Skitter's house in the PyBullet GUI (mouse: ctrl+drag orbit, scroll zoom). `--low` builds 1.2 m walls.
The robot is just placed at the spawn (no gait); the person walks. Close the window or Ctrl+C to quit.
`--png PATH` instead renders a top-down view of the whole house (with the loft wing) to PATH, headless."""
import os
import sys
import time

import numpy as np
import pybullet as p
import pybullet_data
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import URDF  # noqa: E402
from cortex.adapters.hexapod_pybullet.arena import move_person  # noqa: E402
from cortex.adapters.hexapod_pybullet.house import WALL_H, build_house  # noqa: E402

png = sys.argv[sys.argv.index("--png") + 1] if "--png" in sys.argv else None
p.connect(p.DIRECT if png else p.GUI)
p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.setGravity(0, 0, -9.81)
p.loadURDF("plane.urdf")
objs = build_house(1.2 if "--low" in sys.argv else WALL_H)
p.loadURDF(URDF, [0, 0, 0.12])
if png:
    w, h = 1400, 1200
    img = p.getCameraImage(w, h, p.computeViewMatrix([3.2, 4.5, 60], [3.2, 4.5, 0], [0, 1, 0]),
                           p.computeProjectionMatrixFOV(16, w / h, 50, 61), renderer=p.ER_TINY_RENDERER,
                           lightDirection=[2, -1, 5], shadow=0)[2]
    Image.fromarray(np.reshape(img, (h, w, 4))[:, :, :3].astype(np.uint8)).save(png)
    print("saved", png)
    sys.exit()
p.resetDebugVisualizerCamera(13, 30, -55, [1.0, 4.0, 0])
t = 0
while p.isConnected():
    p.stepSimulation()
    t += 1
    move_person(objs["person"], t)
    time.sleep(1 / 240)
