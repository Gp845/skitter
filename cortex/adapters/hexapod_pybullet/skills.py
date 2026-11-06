"""Device-side goal skills (the robot's 'spinal cord'). 0G picks the goal; these close the loop locally.

Each skill returns (vx, wz) every sim step and sets .result when finished:
  reached | blocked | lost | timeout | bad_target
climb drives its own StairGait (joint_targets) and can also end: no_step | height_mismatch | too_steep |
stuck | unstable
"""
import math

import cv2
import numpy as np

from .gait import BELLY_CLEAR, BODY_HEIGHT, CYCLE_STEPS, FOOT_X, MAX_EXT, StairGait

TRACK_EVERY = 24      # sim steps between sensor/tracker updates (10 Hz)
REACH_DEPTH = 0.35    # m from camera: close enough to the target
BLOCK_DEPTH = 0.28    # m from camera: reflex stops forward motion (never steers)
FOOT_MARGIN = 0.03    # m between a foot and the side edge of a stair
MIN_HALF = 0.08       # m: side feet can't tuck closer to the axis than this (hips sit 9.4 cm out) -> stairs >= 22 cm
FOLLOW_GAP = 0.6      # m from camera: follow stands still (keeps watching) when the target is this close
MIN_EDGE = 0.5        # m from body centre: nearer edges are below the camera's view (it sees floor from ~0.55 m)
BACKUP = 0.35         # m climb backs off when the edge is nearer than MIN_EDGE (e.g. right after other stairs)


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class Skill:
    def __init__(self, kind, params, bot, timeout_s):
        self.kind, self.p, self.bot = kind, params, bot
        self.result = None
        self.t0, self.timeout_steps = bot.t, int(timeout_s * 240)
        pos, (_, _, yaw) = bot.pose()
        self.start_xy, self.start_yaw = pos[:2], yaw
        self.stable = self.lost = 0
        self.last_sense = -1
        self.cx = 0.5
        if kind in ("face", "approach", "follow"):
            self._init_tracker()
        if kind == "climb":
            self.backup = None
            self._init_climb()

    # ---- stairs: known riser height, edge found by the depth camera ----
    def _init_climb(self):
        h, n, tread = self.p["step_height_cm"] / 100, int(self.p["steps"]), self.p["tread_cm"] / 100
        down = self.p.get("direction", "up") == "down"
        found = self.bot.find_step(down=down)
        if (found is None or found[0] < MIN_EDGE) and self.backup is None:  # edge too close to see: back off once
            self.backup, self.turning = "back", False
            return
        if found is None:
            self.result = "no_step"
            return
        edge, measured, square, self.offset = found
        self.climb_yaw = self.start_yaw + square  # climb square to the riser, not along wherever the turn ended
        if abs(measured - h) > 0.015:  # never climb with the wrong geometry
            self.result = f"height_mismatch (measured {measured * 100:.1f} cm)"
            return
        width = self.p.get("width_cm", 100) / 100
        span = FOOT_X["L1"] - FOOT_X["L4"]  # front-to-back foot span (~52 cm): the steps the feet straddle
        self.side = h * min(n, math.ceil(span / tread)) + BELLY_CLEAR > MAX_EXT  # belly vs. leg reach
        if self.side:  # treads too shallow head-on: turn 90 deg, tuck the legs to fit one tread, crab up
            half = tread / 2 - FOOT_MARGIN
            if half < MIN_HALF or width < span + 2 * FOOT_MARGIN or h + BELLY_CLEAR > MAX_EXT:
                self.result = "too_steep"
                return
            rear, self.offset = half, 0.0   # rear-most foot sits half a stance behind the centre
        else:
            half = width / 2 - FOOT_MARGIN  # how far a foot may sit off the body axis
            if half < MIN_HALF:
                self.result = "too_narrow"
                return
            rear = -FOOT_X["L4"]
        h = -h if down else h  # going down is the same gait on negative risers
        self.top = n * h
        self.end_s = edge + (n - 1) * tread + rear + 0.05  # rear foot 5 cm past the last nosing
        self.stair = StairGait(lambda x: h * min(n, int((x - edge) // tread) + 1) if x >= edge else 0.0,
                               half_width=half, sideways=self.side)
        self.s, self.check, self.wz, self.vy = 0.0, (0.0, self.bot.t), 0.0, 0.0
        self.turning = self.side
        self.heading = self.climb_yaw - (math.pi / 2 if self.side else 0.0)  # left side leads when crabbing

    def joint_targets(self):
        if self.backup in ("back", "settle"):
            return self.bot.gait.joint_targets(-1.0 if self.backup == "back" else 0.0, 0.0)
        if self.turning:
            return self.bot.gait.joint_targets(0.0, self.wz)
        return self.stair.joint_targets(self.s, self.wz, self.vy)

    def _climb(self):
        pos, (roll, pitch, yaw) = self.bot.pose()
        dx, dy = pos[0] - self.start_xy[0], pos[1] - self.start_xy[1]
        self.s = dx * math.cos(self.start_yaw) + dy * math.sin(self.start_yaw)
        # hold the start line: the gait drifts sideways, and stairs are only a little wider than the robot
        if math.degrees(max(abs(roll), abs(pitch))) > 20:
            return self._done("unstable")
        if self.backup in ("back", "settle"):  # walk back, stand still a second, then measure the stairs again
            if self.backup == "back" and -self.s >= BACKUP:
                self.backup, self.check = "settle", ("settle", self.bot.t)
            if self.backup == "settle" and self.bot.t - self.check[1] > 240 and not self.bot.gait.phase:
                self.start_xy, self.start_yaw, self.backup = pos[:2], yaw, "done"
                self._init_climb()
            return 0.0, 0.0
        if self.turning:  # turn in place (flat gait) until side-on, then let the legs settle for a second
            err = wrap(self.heading - yaw)
            self.wz = max(-1.0, min(1.0, 2.5 * err)) if abs(err) > 0.03 else 0.0
            if self.wz == 0.0 and self.check[0] != "settle":
                self.check = ("settle", self.bot.t)
            if self.check[0] == "settle" and self.bot.t - self.check[1] > 240:
                self.turning, self.check = False, (self.s, self.bot.t)
            return 0.0, 0.0
        lateral = -dx * math.sin(self.climb_yaw) + dy * math.cos(self.climb_yaw) - self.offset  # + = left of the line
        # sidestep back onto the line; steering instead would swing the rear feet off a narrow tread
        self.wz = max(-0.4, min(0.4, 3.0 * wrap(self.heading - yaw)))
        self.vy = max(-0.4, min(0.4, -8.0 * lateral))
        if self.bot.t - self.check[1] >= 3 * CYCLE_STEPS:  # 3 gait cycles with < 1 cm progress: a foot is caught
            if self.stair.walking and self.s - self.check[0] < 0.01:
                return self._done("stuck")
            self.check = (self.s, self.bot.t)
        st = self.stair
        if st.walking and self.s >= self.end_s and all(abs(g - self.top) < 1e-6 for g in st.g.values()):
            st.walking = False
        if not st.walking and st.tick % CYCLE_STEPS == 0 and abs(st.body_z - (self.top + BODY_HEIGHT)) < 0.003:
            return self._done("reached")
        return 0.0, 0.0

    # ---- target tracking (appearance model from the 0G model's box) ----
    def _init_tracker(self):
        bgr, _ = self.bot.render()
        H, W = bgr.shape[:2]
        x1, x2 = sorted((self.p["x1"], self.p["x2"]))
        y1, y2 = sorted((self.p["y1"], self.p["y2"]))
        if x2 - x1 < 15 or y2 - y1 < 15:
            self.result = "bad_target"
            return
        cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) * 0.6, (y2 - y1) * 0.6  # core of the box
        px1, px2 = int((cx - w / 2) * W / 1000), int((cx + w / 2) * W / 1000)
        py1, py2 = int((cy - h / 2) * H / 1000), int((cy + h / 2) * H / 1000)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        roi = hsv[py1:py2, px1:px2]
        if roi.size == 0:
            self.result = "bad_target"
            return
        mask = cv2.inRange(roi, (0, 60, 30), (180, 255, 255))  # ignore grey/white background
        if mask.mean() / 255 < 0.05:  # only a grey/white target falls back to all pixels; a person's shirt is ~10%
            mask = None
        self.hist = cv2.calcHist([roi], [0, 1], mask, [18, 8], [0, 180, 0, 256])
        cv2.normalize(self.hist, self.hist, 0, 255, cv2.NORM_MINMAX)
        self.win = (px1, py1, max(px2 - px1, 4), max(py2 - py1, 4))
        self.cx = (px1 + px2) / 2 / W

    def _track(self, bgr, depth):
        H, W = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        back = cv2.calcBackProject([hsv], [0, 1], self.hist, [0, 180, 0, 256], 1)
        _, win = cv2.meanShift(back, self.win, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 1))
        x, y, w, h = win
        quality = float(back[y:y + h, x:x + w].mean()) if w > 0 and h > 0 else 0.0
        if quality < 12:
            self.lost += 1
            return None
        self.lost, self.win = 0, win
        self.cx = (x + w / 2) / W
        core = depth[y + h // 4: y + 3 * h // 4 + 1, x + w // 4: x + 3 * w // 4 + 1]
        return float(np.median(core)) if core.size else None

    # ---- per-step control ----
    def _done(self, result):
        self.result = result
        return 0.0, 0.0

    def update(self):
        if self.result:
            return 0.0, 0.0
        bot = self.bot
        if bot.t - self.t0 > self.timeout_steps:
            return self._done("timeout")
        pos, (_, _, yaw) = bot.pose()

        if self.kind == "climb":
            return self._climb()

        if self.kind == "turn":
            sign = 1 if self.p["direction"] == "left" else -1
            err = wrap(self.start_yaw + sign * math.radians(self.p["degrees"]) - yaw)
            if abs(err) < math.radians(3):
                return self._done("reached")
            return 0.0, math.copysign(min(1.0, max(0.3, abs(err) / 0.5)), err)

        if self.kind == "walk":
            fwd = 1 if self.p["direction"] == "forward" else -1
            dx, dy = pos[0] - self.start_xy[0], pos[1] - self.start_xy[1]
            moved = (dx * math.cos(self.start_yaw) + dy * math.sin(self.start_yaw)) * fwd
            if moved >= self.p["distance_cm"] / 100:
                return self._done("reached")
            if fwd > 0 and bot.range_ahead < BLOCK_DEPTH:
                return self._done("blocked")
            return float(fwd), max(-0.3, min(0.3, 3.0 * wrap(self.start_yaw - yaw)))  # hold the heading

        # face / approach / follow: track the target on every new sensor frame (10 Hz)
        if bot.sense_id != self.last_sense:
            self.last_sense = bot.sense_id
            d = self._track(bot.sensed_bgr, bot.sensed_depth)
            self.target_depth = d
            if self.lost >= 5:
                return self._done("lost")
        ex = self.cx - 0.5
        if self.kind == "face":
            self.stable = self.stable + 1 if abs(ex) < 0.05 else 0
            if self.stable > TRACK_EVERY * 3:  # centred for ~0.3 s
                return self._done("reached")
        d = getattr(self, "target_depth", None)
        if self.kind == "approach" and ((d is not None and d < REACH_DEPTH) or bot.range_ahead < BLOCK_DEPTH):
            return self._done("reached")
        wz = 0.0 if abs(ex) < 0.04 else -math.copysign(min(1.0, max(0.25, 3 * abs(ex))), ex)
        vx = 0.0
        if self.kind in ("approach", "follow"):
            vx = 1.0 if abs(ex) < 0.12 else (0.4 if abs(ex) < 0.25 else 0.0)
        if self.kind == "follow" and (d is None or d < FOLLOW_GAP or bot.range_ahead < BLOCK_DEPTH):
            vx = 0.0  # keep a polite gap, keep turning to watch; follow ends only on lost/timeout/stop
        return vx, wz
