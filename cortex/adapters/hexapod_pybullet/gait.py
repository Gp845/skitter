"""Tripod gait + 3-DOF leg IK for the assembly_05 hexapod.

IK and leg geometry come from exp_4_universal_gait_1.py. The gait is driven by a
body-frame command (vx, wz) in [-1, 1]: vx > 0 walks forward (+x of base_link),
wz > 0 turns counter-clockwise. Motion stays on the device; 0G only picks the command.
"""
import math

# leg: (coxa, femur, tibia, mount angle in leg frame [deg], tarsus)
LEGS = {
    "L1": ("Revolute 7", "Revolute 22", "Revolute 49", 0, "Revolute 38"),
    "L2": ("Revolute 8", "Revolute 21", "Revolute 48", -60, "Revolute 39"),
    "L3": ("Revolute 9", "Revolute 20", "Revolute 47", -120, "Revolute 40"),
    "L4": ("Revolute 10", "Revolute 19", "Revolute 46", 180, "Revolute 41"),
    "L5": ("Revolute 11", "Revolute 24", "Revolute 45", 120, "Revolute 42"),
    "L6": ("Revolute 12", "Revolute 23", "Revolute 44", 60, "Revolute 43"),
}
TRIPOD_PHASE = [0.0, 0.5, 0.0, 0.5, 0.0, 0.5]
DUTY = 0.5

L1, L2, L3 = 0.040, 0.087, 0.0944
A = math.radians(52.2)
B = math.radians(76.3)

BODY_HEIGHT = 0.07
NEUTRAL_REACH = 0.15
STEP_HEIGHT = 0.05
STEP_LENGTH = 0.05
CYCLE_STEPS = 200  # sim steps per gait cycle at 240 Hz


def ik(x, y, z):
    theta1 = math.atan2(y, x)
    r = math.hypot(x, y) - L1
    c3 = (r * r + z * z - L2 * L2 - L3 * L3) / (2 * L2 * L3)
    c3 = max(-1.0, min(1.0, c3))
    theta3 = math.atan2(math.sqrt(1 - c3 * c3), c3)
    k1 = L2 + L3 * math.cos(theta3)
    k2 = L3 * math.sin(theta3)
    theta2 = math.atan2(-k2 * r - k1 * z, k1 * r - k2 * z)
    return theta1, math.pi / 2 + theta2 - A, theta3 - math.pi / 2 + A - B


def step_vector(mount_deg, vx, wz):
    """Body-frame command -> horizontal step vector in the leg frame."""
    a = math.radians(mount_deg)
    sx = vx * math.cos(a)
    sy = vx * math.sin(a) + wz  # wz > 0 = counter-clockwise (verified with an unwrapped yaw trace, ~23 deg/s)
    norm = math.hypot(sx, sy)
    if norm > 1.0:
        sx, sy = sx / norm, sy / norm
    return sx * STEP_LENGTH, sy * STEP_LENGTH


def foot_target(phase, step):
    """Foot position in the leg frame for a gait phase in [0, 1)."""
    sx, sy = step
    if phase < 1 - DUTY:  # swing: move foot forward along step, lifted
        s = phase / (1 - DUTY)
        k = s - 0.5
        lift = STEP_HEIGHT * math.sin(math.pi * s)
    else:  # stance: foot pushes back, body moves along step
        s = (phase - (1 - DUTY)) / DUTY
        k = 0.5 - s
        lift = 0.0
    return NEUTRAL_REACH + k * sx, k * sy, -BODY_HEIGHT + lift


HIP_R = 0.109                      # hip distance from the body centre (measured in sim)
FOOT_X = {leg: (HIP_R + NEUTRAL_REACH) * math.cos(math.radians(spec[3])) for leg, spec in LEGS.items()}
BELLY = (-0.12, 0.20)              # body-frame x span the belly must clear (with look-ahead)
BELLY_SIDE = (-0.12, 0.16)         # same along body y, for climbing sideways
BELLY_CLEAR = 0.045                # min body_z above the ground under the belly
MAX_EXT = 0.125                    # max hip-to-foot drop the legs can hold (IK reach ~0.18 m)
STAIR_CLEAR = 0.025                # swing foot clears the higher of take-off/landing ground by this much


def _smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


class StairGait:
    """Hardcoded straight-ahead tripod for steps of known riser height.

    `ground(x)` is the floor height x metres ahead of where the climb started; joint_targets(s) takes how far
    the body has moved along that line. Each foot plans its landing height at take-off and swings
    up-over-down (box profile) so the toe never drags into a riser. body_z (hip-to-start-floor, i.e. the flat
    gait's BODY_HEIGHT) rides high enough for the belly to clear the nosing, never beyond the legs' reach.
    """

    def __init__(self, ground, speed=0.6, half_width=None, sideways=False):
        self.ground, self.speed, self.tick = ground, speed, 0
        self.g = {leg: 0.0 for leg in LEGS}           # ground height under each planted foot
        self.lift, self.land = dict(self.g), dict(self.g)
        self.swinging = {leg: False for leg in LEGS}
        self.body_z = BODY_HEIGHT
        self.walking = True
        # leg-frame neutral foot point: wide by default; narrow stance swaps it in leg by leg, mid-swing
        self.target = {leg: narrow_neutral(leg, half_width) if half_width else (NEUTRAL_REACH, 0.0) for leg in LEGS}
        self.cur = {leg: (NEUTRAL_REACH, 0.0) for leg in LEGS}
        self.prev = dict(self.cur)
        # sideways: walk toward body +y (left side leads); 'ahead' is then body y
        self.dir, self.belly = (90, BELLY_SIDE) if sideways else (0, BELLY)
        self.foot_x = {leg: (_body_y if sideways else _body_x)(leg, *self.target[leg]) for leg in LEGS}

    def joint_targets(self, s, wz=0.0):
        sl = self.speed * STEP_LENGTH
        if self.walking or self.tick % CYCLE_STEPS:
            self.tick += 1  # like the flat gait: finish the cycle before standing
        lo, hi = min(self.g.values()), max(self.g.values())
        b0, b1 = self.belly
        belly = max(self.ground(s + b0 + 0.01 * i) for i in range(int((b1 - b0) / 0.01) + 1))
        want = min(max(lo + BODY_HEIGHT, belly + BELLY_CLEAR, hi + 0.03), lo + MAX_EXT)
        self.body_z += 0.03 * (want - self.body_z)
        targets = {}
        for k, (leg, (coxa, femur, tibia, mount, tarsus)) in enumerate(LEGS.items()):
            sx, sy = step_vector(mount + self.dir, self.speed, wz)
            phase = (self.tick / CYCLE_STEPS + TRIPOD_PHASE[k]) % 1.0
            nx, ny = self.cur[leg]
            if not (self.walking or self.tick % CYCLE_STEPS):
                kx, zw = 0.0, self.g[leg]
            elif phase < 1 - DUTY:  # swing: up, over, down
                if not self.swinging[leg]:
                    self.swinging[leg] = True
                    self.prev[leg], self.cur[leg] = self.cur[leg], self.target[leg]
                    self.lift[leg] = self.g[leg]
                    self.land[leg] = self.ground(s + self.foot_x[leg] + 1.5 * sl)  # body moves sl while it swings
                u = phase / (1 - DUTY)
                (px, py), (cx, cy), b = self.prev[leg], self.cur[leg], _smooth(u)
                nx, ny = px + (cx - px) * b, py + (cy - py) * b
                g0, g1 = self.lift[leg], self.land[leg]
                top = max(g0, g1) + STAIR_CLEAR
                kx = -0.5 + _smooth((u - 0.3) / 0.4)
                zw = g0 + (top - g0) * _smooth(u / 0.3) if u < 0.7 else top + (g1 - top) * _smooth((u - 0.7) / 0.3)
            else:  # stance: foot planted on its ground, body moves over it
                if self.swinging[leg]:
                    self.swinging[leg], self.g[leg] = False, self.land[leg]
                kx, zw = 0.5 - (phase - (1 - DUTY)) / DUTY, self.g[leg]
            t1, t2, t3 = ik(nx + kx * sx, ny + kx * sy, zw - self.body_z)
            targets[coxa], targets[femur], targets[tibia], targets[tarsus] = t1, t2, t3, 0.0
        return targets


def _body_x(leg, lx, ly):
    """Body-frame x of a leg-frame foot point (the leg frame is the body frame turned by -mount, at the hip)."""
    a = math.radians(LEGS[leg][3])
    return HIP_R * math.cos(a) + lx * math.cos(a) + ly * math.sin(a)


def _body_y(leg, lx, ly):
    a = math.radians(LEGS[leg][3])
    return -HIP_R * math.sin(a) - lx * math.sin(a) + ly * math.cos(a)


def narrow_neutral(leg, half_width):
    """Leg-frame neutral foot point that keeps the foot within |y| <= half_width of the body axis: the side
    legs' coxas swing them toward the front/back (hips sit ~9.4 cm off the axis, so ~10 cm still fits)."""
    a = math.radians(LEGS[leg][3])
    hx, hy = HIP_R * math.cos(a), -HIP_R * math.sin(a)                 # hip, body frame
    wy = hy - NEUTRAL_REACH * math.sin(a)                              # wide stance foot y
    if abs(wy) <= half_width:
        return NEUTRAL_REACH, 0.0
    fy = math.copysign(half_width, wy)
    fx = hx + math.copysign(math.sqrt(max(0.0, NEUTRAL_REACH ** 2 - (fy - hy) ** 2)), hx)
    dx, dy = fx - hx, fy - hy                                          # body -> leg frame: turn by +mount
    return dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)


class Gait:
    def __init__(self):
        self.tick = 0
        self.vx = 0.0
        self.wz = 0.0

    def joint_targets(self, cmd_vx, cmd_wz):
        """Advance one sim step; return {joint_name: angle}."""
        # low-pass the command so direction changes don't jerk the legs
        self.vx += 0.02 * (cmd_vx - self.vx)
        self.wz += 0.02 * (cmd_wz - self.wz)
        moving = abs(self.vx) > 0.02 or abs(self.wz) > 0.02
        if moving or self.tick % CYCLE_STEPS:
            self.tick += 1  # finish the current cycle before standing still
        targets = {}
        for k, (leg, (coxa, femur, tibia, mount, tarsus)) in enumerate(LEGS.items()):
            if moving or self.tick % CYCLE_STEPS:
                phase = (self.tick / CYCLE_STEPS + TRIPOD_PHASE[k]) % 1.0
                x, y, z = foot_target(phase, step_vector(mount, self.vx, self.wz))
            else:
                x, y, z = NEUTRAL_REACH, 0.0, -BODY_HEIGHT
            t1, t2, t3 = ik(x, y, z)
            targets[coxa], targets[femur], targets[tibia], targets[tarsus] = t1, t2, t3, 0.0
        return targets
