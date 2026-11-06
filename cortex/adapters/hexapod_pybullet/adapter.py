"""Skitter: the hexapod (assembly_05) in PyBullet. Gait/IK + closed-loop skills are its 'spinal cord'."""
import math
import os
import time

import cv2
import numpy as np
import pybullet as p
import pybullet_data

from .arena import move_person
from .gait import Gait
from .skills import BLOCK_DEPTH, TRACK_EVERY, Skill

HERE = os.path.dirname(os.path.abspath(__file__))
URDF = os.path.join(HERE, "assembly_05_description", "urdf", "assembly_05.urdf")
SIM_HZ = 240
W, H, NEAR, FAR = 320, 240, 0.02, 10.0
GOAL_KINDS = ("turn", "walk", "face", "approach", "follow", "climb")

BBOX = {k: [0, 1000] for k in ("x1", "y1", "x2", "y2")}
BBOX_HELP = ("Give the target's bounding box in the CURRENT image as integers 0-1000 "
             "(x to the right, y down, origin top-left); `label` is its name.")


class HexapodAdapter:
    def __init__(self, gui=False, arena=True, world="arena"):
        self.gui = gui
        self.cid = p.connect(p.GUI if gui else p.DIRECT)
        p.setAdditionalSearchPath(pybullet_data.getDataPath())
        p.setGravity(0, 0, -9.81)
        p.setTimeStep(1.0 / SIM_HZ)
        p.loadURDF("plane.urdf")
        p.setAdditionalSearchPath(HERE)  # resolves package://assembly_05_description/meshes/...
        self.robot = p.loadURDF(URDF, [0, 0, 0.12])
        self.joints = {}
        for i in range(p.getNumJoints(self.robot)):
            info = p.getJointInfo(self.robot, i)
            if info[2] == p.JOINT_REVOLUTE:
                self.joints[info[1].decode()] = i
                p.changeDynamics(self.robot, i, jointLowerLimit=-math.pi, jointUpperLimit=math.pi)
        self.gait = Gait()
        self.cmd = None            # timed 'move': (vx, wz, time_left)
        self.skill = None
        self._result = None
        self.t = 0
        self.sense_id, self.sensed_bgr, self.sensed_depth, self.range_ahead = 0, None, None, 9.9
        self.sensed_t = -10 ** 9
        self.objects = {}
        self.world = world if arena else None
        self.patrol_route, self.person_px = [], 0
        if self.world == "house":
            from .house import PATROL, build_house
            self.objects, self.patrol_route = build_house(), PATROL
        elif self.world:
            from .arena import PATROL, build_arena
            self.objects, self.patrol_route = build_arena(), PATROL
        self._person_ids = {b for b, _ in self.objects.get("person", [])}
        self._settle()

    def _apply(self, targets):
        for name, angle in targets.items():
            p.setJointMotorControl2(self.robot, self.joints[name], p.POSITION_CONTROL,
                                    targetPosition=angle, force=6.0)

    def _settle(self, steps=480):
        for _ in range(steps):
            self._apply(self.gait.joint_targets(0.0, 0.0))
            p.stepSimulation()

    # ---- sensing ----
    def render(self):
        """Front camera: (bgr uint8 HxWx3, depth in metres HxW)."""
        pos, (_, _, yaw) = self.pose()
        f = (math.cos(yaw), math.sin(yaw), 0)
        eye = [pos[0] + 0.20 * f[0], pos[1] + 0.20 * f[1], pos[2] + 0.12]
        target = [eye[0] + f[0], eye[1] + f[1], eye[2] - 0.12]
        view = p.computeViewMatrix(eye, target, [0, 0, 1])
        proj = p.computeProjectionMatrixFOV(70, W / H, NEAR, FAR)
        _, _, rgb, z, seg = p.getCameraImage(W, H, view, proj, renderer=p.ER_TINY_RENDERER)
        if self._person_ids:  # sim stand-in for an on-robot person detector: pixels of the person in view
            self.person_px = int(np.isin(np.asarray(seg), list(self._person_ids)).sum())
        img = np.reshape(np.asarray(rgb), (H, W, 4))[:, :, :3].astype(np.uint8)
        z = np.reshape(np.asarray(z), (H, W))
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR), FAR * NEAR / (FAR - (FAR - NEAR) * z)

    def _sense(self):
        self.sensed_bgr, self.sensed_depth = self.render()
        self.sensed_t = self.t
        band = self.sensed_depth[90:150, 128:192]  # what is straight ahead of the body
        self.range_ahead = float(np.percentile(band, 5))
        self.sense_id += 1

    def pose(self):
        pos, orn = p.getBasePositionAndOrientation(self.robot)
        return pos, p.getEulerFromQuaternion(orn)

    # ---- interface used by the executor ----
    def manifest(self):
        return {
            "name": "skitter",
            "description": "Skitter, a small six-legged home assistant robot (about 30 cm wide) with a front camera. "
                           "It walks about 10 cm/s and cannot see behind itself. It helps its user by going places, "
                           "finding things, coming to or following them, and answering questions about what it sees. "
                           "The user is the person in the home (yellow shirt, purple trousers): 'me' means them.",
            "actions": [
                {"name": "turn", "goal": True, "timeout_s": 30,
                 "description": "Turn in place by a number of degrees; finishes on its own.",
                 "params": {"direction": ["left", "right"], "degrees": [10, 180]}},
                {"name": "walk", "goal": True, "timeout_s": 30,
                 "description": "Walk straight for a distance; finishes on its own, stops early if blocked.",
                 "params": {"direction": ["forward", "backward"], "distance_cm": [5, 150]}},
                {"name": "face", "goal": True, "timeout_s": 30,
                 "description": "Turn in place until the target is centred in the camera. " + BBOX_HELP,
                 "params": {"label": "string", **BBOX}},
                {"name": "approach", "goal": True, "timeout_s": 40,
                 "description": "Walk toward the target (steering to keep it centred) until about 35 cm from it. "
                                "Result 'reached' means it is now right in front of the robot. " + BBOX_HELP,
                 "params": {"label": "string", **BBOX}},
                {"name": "follow", "goal": True, "timeout_s": 90,
                 "description": "Keep following a (moving) person or object, staying about 60 cm away, for up to "
                                "90 s or until it is lost. Use for 'follow me'. For 'come here' use approach. "
                                + BBOX_HELP,
                 "params": {"label": "string", **BBOX}},
                {"name": "climb", "goal": True, "timeout_s": 120,
                 "description": "Climb a step or short staircase straight ahead with a special stair gait. Face the "
                                "stairs square-on first and start 0.3-1 m before the first step. step_height_cm is "
                                "the riser height (use the user's number if they give one); steps, tread_cm and "
                                "width_cm describe the stairs (width_cm 100 if unknown). Narrow stairs: Skitter tucks its legs "
                                "in; shallow treads: it turns side-on and crabs up; both "
                                "automatic. Skitter measures the first step with its depth camera and "
                                "refuses (height_mismatch / too_steep / too_narrow / no_step) rather than guess.",
                 "params": {"step_height_cm": [2, 6], "steps": [1, 3], "tread_cm": [25, 60], "width_cm": [20, 200]}},
                {"name": "look_around", "goal": True, "timeout_s": 30,
                 "description": "Turn 90 degrees left to look for something (or someone) that is not in view and "
                                "not in known_objects. Repeat up to 4 times before asking the user.",
                 "params": {}},
                {"name": "stop", "params": {}},
            ],
            "safety": {"max_lease_s": 6.0, "max_tilt_deg": 35},
        }

    def state(self):
        pos, (roll, pitch, yaw) = self.pose()
        tilt = math.degrees(max(abs(roll), abs(pitch)))
        return {"x": pos[0], "y": pos[1], "z": pos[2], "yaw": yaw, "tilt_deg": tilt,
                "fallen": tilt > 35 or pos[2] < 0.02, "step": self.t,
                "busy": self.busy(), "last_result": self._result}

    def get_frame(self):
        moving = self.skill is not None or self.cmd is not None  # skills already render at 10 Hz: reuse that image
        img = self.sensed_bgr if moving and self.t - self.sensed_t <= TRACK_EVERY else self.render()[0]
        return cv2.imencode(".jpg", img)[1].tobytes()

    def find_step(self, max_dist=1.5):
        """First riser straight ahead from the depth camera, or None:
        (distance from body centre, riser height, yaw to turn to be square to it [rad, + = left])."""
        _, depth = self.render()
        pos, _ = self.pose()
        cam_h = pos[2] + 0.12                                   # eye is 0.12 m above the base origin
        pitch, f = math.atan2(0.12, 1.0), (H / 2) / math.tan(math.radians(35))

        def scan(c):  # (dist from centre, height) per row, looking down image column strip c-20..c+20
            out = []
            for v in range(H // 2, 180):                        # rows below ~180 see Skitter's own shell
                phi = math.atan((v + 0.5 - H / 2) / f)
                r = float(np.median(depth[v, c - 20: c + 20])) / math.cos(phi)
                out.append((r * math.cos(pitch + phi) + 0.20, cam_h - r * math.sin(pitch + phi)))
            return out

        def first(hits):
            raised = [d for d, z in hits if z > 0.01 and d < max_dist]
            return min(raised) if raised else None

        hits = scan(W // 2)
        edge = first(hits)
        if edge is None:
            return None
        height = max(z for d, z in hits if edge - 0.01 <= d <= edge + 0.06)
        for off in (W // 5, W // 10):                           # side strips: wide for accuracy, close for narrow stairs
            dl, dr = first(scan(W // 2 - off)), first(scan(W // 2 + off))
            if dl is not None and dr is not None:
                lat = lambda d: (d - 0.20) * off / f            # strip's sideways offset at that distance
                return edge, height, math.atan2(dr - dl, lat(dr) + lat(dl))
        return edge, height, 0.0

    def locate(self, box):
        _, depth = self.render()
        x1, x2 = sorted((box["x1"], box["x2"]))
        y1, y2 = sorted((box["y1"], box["y2"]))
        px1, px2, py1, py2 = int(x1 * W / 1000), int(x2 * W / 1000), int(y1 * H / 1000), int(y2 * H / 1000)
        qx, qy = (px2 - px1) // 4, (py2 - py1) // 4
        core = depth[py1 + qy: py2 - qy + 1, px1 + qx: px2 - qx + 1]  # centre of the box: object, not background
        if core.size == 0 or np.median(core) > 0.95 * FAR:
            return None
        d = float(np.median(core))
        pos, (_, _, yaw) = self.pose()
        right = d * ((x1 + x2) / 2000 - 0.5) * 2 * math.tan(math.radians(35)) * W / H  # vertical FOV 70
        ex, ey = pos[0] + 0.20 * math.cos(yaw), pos[1] + 0.20 * math.sin(yaw)  # camera eye
        return (ex + d * math.cos(yaw) + right * math.sin(yaw), ey + d * math.sin(yaw) - right * math.cos(yaw))

    def busy(self):
        return self.skill is not None or self.cmd is not None

    def result(self):
        return self._result

    def execute(self, action, params, lease_s):
        if action == "stop":
            return self.stop()
        self._result, self.skill, self.cmd = None, None, None
        if action == "look_around":
            action, params = "turn", {"direction": "left", "degrees": 90}
        if action in GOAL_KINDS:
            self._sense()
            self.skill = Skill(action, params, self, lease_s)
            if self.skill.result:  # e.g. bad_target at init
                self._result, self.skill = self.skill.result, None
            return
        d = params.get("direction", "forward")  # legacy timed move (not advertised to the model)
        v = float(params.get("speed", 0.5))
        vx = {"forward": v, "backward": -v}.get(d, 0.0)
        wz = {"turn_left": v, "turn_right": -v}.get(d, 0.0)
        self.cmd = (vx, wz, min(float(params.get("duration_s", lease_s)), lease_s))

    def stop(self):
        if self.skill is not None:
            self._result = "cancelled"
        self.skill, self.cmd = None, None

    def step(self, dt):
        for _ in range(max(1, round(dt * SIM_HZ))):
            vx = wz = 0.0
            if self.skill is not None or self.cmd is not None:
                if self.t % TRACK_EVERY == 0:
                    self._sense()
            if self.skill is not None:
                vx, wz = self.skill.update()
                if self.skill.result:
                    self._result, self.skill = self.skill.result, None
            elif self.cmd is not None:
                vx, wz, left = self.cmd
                left -= 1.0 / SIM_HZ
                self.cmd = (vx, wz, left) if left > 0 else None
                if vx > 0 and self.range_ahead < BLOCK_DEPTH:  # reflex: may only stop, never steer
                    vx = 0.0
            climbing = self.skill is not None and self.skill.kind == "climb"
            self._apply(self.skill.joint_targets() if climbing else self.gait.joint_targets(vx, wz))
            if "person" in self.objects:
                move_person(self.objects["person"], self.t)
            p.stepSimulation()
            self.t += 1
            if self.gui:
                time.sleep(1.0 / SIM_HZ)

    def close(self):
        if p.isConnected(self.cid):
            p.disconnect(self.cid)
