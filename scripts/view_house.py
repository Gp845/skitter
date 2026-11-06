"""Fly around Skitter's house in the PyBullet GUI (mouse: ctrl+drag orbit, scroll zoom). `--low` builds 1.2 m walls.
The robot is just placed at the spawn (no gait); the person walks. Close the window or Ctrl+C to quit."""
import os
import sys
import time

import pybullet as p
import pybullet_data

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import URDF  # noqa: E402
from cortex.adapters.hexapod_pybullet.arena import move_person  # noqa: E402
from cortex.adapters.hexapod_pybullet.house import WALL_H, build_house  # noqa: E402

p.connect(p.GUI)
p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.setGravity(0, 0, -9.81)
p.loadURDF("plane.urdf")
objs = build_house(1.2 if "--low" in sys.argv else WALL_H)
p.loadURDF(URDF, [0, 0, 0.12])
p.resetDebugVisualizerCamera(9, 30, -50, [3.0, 1.5, 0])
t = 0
while p.isConnected():
    p.stepSimulation()
    t += 1
    move_person(objs["person"], t)
    time.sleep(1 / 240)
