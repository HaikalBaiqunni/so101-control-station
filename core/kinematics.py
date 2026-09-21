"""
Cartesian kinematics for the digital twin's arm: forward kinematics, the
Jacobian, and a resolved-rate jog step - what JAKA-style "X/Y/Z, Rx/Ry/Rz"
buttons need underneath them.

Built on the SAME MJCF the digital twin renders, through MuJoCo's own
kinematics, so there is no second hand-typed DH table to drift out of sync with
the model the user is actually looking at. It owns a private MjModel/MjData and
never touches GL, so it is safe (and ~2 us per Jacobian) on the GUI thread.

Two coordinate frames, the distinction JAKA makes and this module exists for:

  world - X/Y/Z/Rx/Ry/Rz are directions in the robot's fixed base frame. "+Z"
          is always straight up, whatever the wrist is doing.
  tool  - the same six directions expressed in the TCP's own frame, which
          rotates with the arm. "+Z" is along the gripper's approach axis, so
          it is the natural "push forward / retract" jog once the tool points
          somewhere other than up.

Rotations are angular velocities about the chosen frame's axes, applied at the
TCP (so jogging Rz in the tool frame spins the gripper about its own axis
without swinging its tip around).

Deliberately velocity-level (resolved-rate) rather than "solve IK for a target
pose": a held jog button is a velocity command, integrating it tick by tick is
what an industrial pendant does, and it needs no convergence criteria, no
multiple-solution branch choice and no failure mode besides "this direction is
blocked right now" - which is reported, not hidden.

Joint values here are MJCF radians. Converting to/from the GUI's degrees is the
caller's job - see fractions_to_q/q_to_fractions for the mapping the twin uses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

AXES = ("x", "y", "z", "rx", "ry", "rz")
FRAMES = ("world", "tool")

# Tried in order when a profile doesn't name a TCP site explicitly. Covers the
# names the common SO-100/SO-101 MJCF/URDF exports and MuJoCo Menagerie use.
TCP_SITE_CANDIDATES = (
    "gripperframe", "gripper_frame", "tcp", "ee", "eef", "end_effector", "pinch", "attachment_site",
)

# Damped least squares with the damping switched on only NEAR a singularity
# (Nakamura & Hanafusa). A constant damping term is a permanent speed loss: on
# an SO-101-sized arm the smallest singular value of a perfectly healthy pose is
# only 0.02-0.05 (measured), so even a "small" constant 0.02 cut every move short
# (a commanded 24 mm/s came out at ~18 mm/s), and so did an eps of 0.03 that sat
# inside that healthy range. SINGULARITY_EPS is therefore set below it: damping
# is exactly zero for any healthy pose, and ramps up to DEFAULT_DAMPING only as
# the smallest singular value heads toward zero. The joint-rate cap below is the
# backstop either way.
DEFAULT_DAMPING = 0.05
SINGULARITY_EPS = 0.01
DEFAULT_MAX_JOINT_RATE_DEG_S = 60.0
_LIMIT_EPS = 1e-4  # rad - "at the limit" for the purpose of blocking further motion
_RANK_TOL = 1e-3   # singular values below this fraction of the largest count as lost DoF


@dataclass(frozen=True)
class TcpSpec:
    """Where the tool centre point is. Resolution order: an explicitly named
    `site`, then any of TCP_SITE_CANDIDATES, then `body` (+ `offset`, metres in
    that body's local frame), and as a last resort the body that carries the
    last arm joint - so a model nobody has annotated still gets *a* TCP, and the
    UI says which one it settled for."""
    site: str = ""
    body: str = ""
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Tcp:
    kind: str                 # "site" | "body"
    obj_id: int
    offset: np.ndarray = field(default_factory=lambda: np.zeros(3), compare=False)
    description: str = ""


def resolve_tcp(model: mujoco.MjModel, spec: TcpSpec, last_arm_joint: str | None = None) -> Tcp:
    def site_id(name: str) -> int:
        return mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, name)

    def body_id(name: str) -> int:
        return mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)

    for name in ((spec.site,) if spec.site else ()) + TCP_SITE_CANDIDATES:
        if name and site_id(name) >= 0:
            return Tcp("site", site_id(name), np.zeros(3), f"site '{name}'")

    if spec.body and body_id(spec.body) >= 0:
        off = np.array(spec.offset, dtype=float)
        tail = f" + {off.round(4).tolist()} m" if np.any(off) else ""
        return Tcp("body", body_id(spec.body), off, f"body '{spec.body}'{tail}")

    if last_arm_joint:
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, last_arm_joint)
        if jid >= 0:
            bid = int(model.jnt_bodyid[jid])
            name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid) or str(bid)
            return Tcp("body", bid, np.zeros(3), f"body '{name}' origin (no TCP site found in the model)")

    raise ValueError("could not resolve a TCP: no matching site/body and no arm joint to fall back on")


def tcp_pose(model: mujoco.MjModel, data: mujoco.MjData, tcp: Tcp) -> tuple[np.ndarray, np.ndarray]:
    """(position[3], rotation[3x3]) of the TCP in the world frame. The rotation's
    COLUMNS are the tool's x/y/z axes expressed in world coordinates. Needs
    kinematics to be current (mj_kinematics or mj_forward)."""
    if tcp.kind == "site":
        return data.site_xpos[tcp.obj_id].copy(), data.site_xmat[tcp.obj_id].reshape(3, 3).copy()
    rot = data.xmat[tcp.obj_id].reshape(3, 3).copy()
    return data.xpos[tcp.obj_id] + rot @ tcp.offset, rot


def rpy_deg_from_matrix(rot: np.ndarray) -> tuple[float, float, float]:
    """Roll (about X), pitch (about Y), yaw (about Z) in degrees, with
    R = Rz(yaw) @ Ry(pitch) @ Rx(roll) - the usual robot-pendant convention.
    Display only; jogging never goes through Euler angles."""
    sy = -rot[2, 0]
    pitch = math.asin(max(-1.0, min(1.0, sy)))
    if abs(sy) < 0.999999:
        roll = math.atan2(rot[2, 1], rot[2, 2])
        yaw = math.atan2(rot[1, 0], rot[0, 0])
    else:  # gimbal lock: roll and yaw are the same rotation, fold it all into yaw
        roll = 0.0
        yaw = math.atan2(-rot[0, 1], rot[1, 1])
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


@dataclass
class StepResult:
    q: np.ndarray
    blocked: list[str]      # joints held at a limit this step
    saturated: bool         # the joint-rate cap had to slow the move down


class KinematicChain:
    def __init__(
        self,
        mjcf_path: str,
        arm_joints: list[str] | tuple[str, ...],
        spec: TcpSpec | None = None,
        damping: float = DEFAULT_DAMPING,
        max_joint_rate_deg_s: float = DEFAULT_MAX_JOINT_RATE_DEG_S,
    ):
        self.model = mujoco.MjModel.from_xml_path(mjcf_path)
        self.data = mujoco.MjData(self.model)
        self.damping = damping
        self.max_joint_rate = math.radians(max_joint_rate_deg_s)

        # Only joints this MJCF actually has: a user-supplied SO-101 model may
        # name things slightly differently, and a chain missing a joint should
        # degrade to "fewer DoF", not refuse to load.
        self.joint_names: list[str] = []
        self._qadr: list[int] = []
        self._dofadr: list[int] = []
        self._lo: list[float] = []
        self._hi: list[float] = []
        for name in arm_joints:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid < 0:
                continue
            self.joint_names.append(name)
            self._qadr.append(int(self.model.jnt_qposadr[jid]))
            self._dofadr.append(int(self.model.jnt_dofadr[jid]))
            lo, hi = self.model.jnt_range[jid]
            # Same rule DigitalTwin uses: a joint with no declared limit reports
            # (0, 0) and must not be clamped to it.
            if lo < hi:
                self._lo.append(float(lo))
                self._hi.append(float(hi))
            else:
                self._lo.append(-math.pi)
                self._hi.append(math.pi)
        if not self.joint_names:
            raise ValueError(f"none of the arm joints {list(arm_joints)} exist in {mjcf_path}")

        self.tcp = resolve_tcp(self.model, spec or TcpSpec(), self.joint_names[-1])
        self.q = np.zeros(len(self.joint_names))
        self.set_q(self.q)

    # ---------------------------------------------------------------- state
    @property
    def n(self) -> int:
        return len(self.joint_names)

    def set_q(self, q) -> None:
        self.q = np.asarray(q, dtype=float).copy()
        self.data.qpos[self._qadr] = self.q
        mujoco.mj_kinematics(self.model, self.data)
        mujoco.mj_comPos(self.model, self.data)  # the Jacobian reads cdof/subtree_com

    def joint_limits(self) -> tuple[np.ndarray, np.ndarray]:
        return np.array(self._lo), np.array(self._hi)

    # -- GUI degrees <-> MJCF radians, by the mapping the digital twin uses -----
    def fractions_to_q(self, fractions: dict[str, float]) -> np.ndarray:
        """0..1 position within each joint's own travel -> MJCF radians. This is
        DigitalTwin.set_joint_fraction's mapping, so a pose computed here is the
        pose the twin draws. Joints missing from `fractions` keep their current
        value."""
        q = self.q.copy()
        for i, name in enumerate(self.joint_names):
            if name in fractions:
                frac = max(0.0, min(1.0, fractions[name]))
                q[i] = self._lo[i] + frac * (self._hi[i] - self._lo[i])
        return q

    def q_to_fractions(self, q) -> dict[str, float]:
        return {
            name: (float(q[i]) - self._lo[i]) / (self._hi[i] - self._lo[i])
            for i, name in enumerate(self.joint_names)
        }

    # ---------------------------------------------------------------- forward kinematics
    def tcp_pose(self) -> tuple[np.ndarray, np.ndarray]:
        return tcp_pose(self.model, self.data, self.tcp)

    def jacobian(self) -> np.ndarray:
        """6 x n, world frame, at the TCP: rows 0-2 linear velocity of the TCP,
        rows 3-5 angular velocity of the tool body."""
        pos, _ = self.tcp_pose()
        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        if self.tcp.kind == "site":
            body = int(self.model.site_bodyid[self.tcp.obj_id])
        else:
            body = self.tcp.obj_id
        mujoco.mj_jac(self.model, self.data, jacp, jacr, pos, body)
        return np.vstack([jacp[:, self._dofadr], jacr[:, self._dofadr]])

    # ---------------------------------------------------------------- jog
    def _frame_matrix(self, frame: str) -> np.ndarray:
        """6x6 that maps a twist given in `frame` to the world frame."""
        if frame == "world":
            return np.eye(6)
        if frame != "tool":
            raise ValueError(f"unknown frame {frame!r}, expected one of {FRAMES}")
        _, rot = self.tcp_pose()
        out = np.zeros((6, 6))
        out[:3, :3] = rot
        out[3:, 3:] = rot
        return out

    def reachability(self, frame: str = "world") -> np.ndarray:
        """For each of AXES, 0..1: how much of a unit jog along that axis the
        arm can produce RIGHT NOW. 1 = fully, 0 = not at all.

        This is what makes a 5-DoF arm honest: a 5-joint SO-101 spans only a
        5-dimensional subspace of the 6-dimensional twist space, so at least one
        direction is always short of 1 (typically pure sideways translation
        without also yawing). Measured as the length of the projection of the
        unit axis onto the span of the Jacobian's columns."""
        jac = self.jacobian()
        u, s, _ = np.linalg.svd(jac, full_matrices=False)
        rank = int(np.sum(s > _RANK_TOL * s[0])) if s[0] > 0 else 0
        basis = u[:, :rank]
        axes_world = self._frame_matrix(frame)  # columns = the six unit axes, in world
        return np.array([float(np.linalg.norm(basis @ (basis.T @ axes_world[:, i]))) for i in range(6)])

    def _solve(self, jac: np.ndarray, twist_world: np.ndarray, blocked: np.ndarray) -> np.ndarray:
        dq = np.zeros(self.n)
        free = ~blocked
        if not np.any(free):
            return dq
        # Through the SVD rather than inverting J J^T: with fewer than six joints
        # J J^T is rank-deficient by construction, and the SVD form handles the
        # unreachable direction cleanly (it simply contributes nothing).
        u, sing, vt = np.linalg.svd(jac[:, free], full_matrices=False)
        smin = float(sing[-1])
        lam_sq = 0.0
        if smin < SINGULARITY_EPS:
            lam_sq = (self.damping ** 2) * (1.0 - (smin / SINGULARITY_EPS) ** 2)
        gain = sing / (sing ** 2 + lam_sq + 1e-12)
        dq[free] = vt.T @ (gain * (u.T @ twist_world))
        return dq

    def step(self, q, twist, frame: str, dt: float) -> StepResult:
        """Advance `q` by one tick of the commanded twist [vx, vy, vz, wx, wy, wz]
        (m/s and rad/s, expressed in `frame`) and return the new joint vector.

        Joints already at a limit and being pushed further are locked and the
        rest re-solved, so the arm slides along a workspace boundary instead of
        the whole motion stalling because one joint ran out of travel."""
        self.set_q(q)
        twist_world = self._frame_matrix(frame) @ np.asarray(twist, dtype=float)
        jac = self.jacobian()
        lo, hi = self.joint_limits()
        qv = np.asarray(q, dtype=float)

        blocked = np.zeros(self.n, dtype=bool)
        dq = self._solve(jac, twist_world, blocked)
        # One active-set pass is enough for jogging: the second solve only has
        # to be sane for a single 33 ms tick, and the next tick re-evaluates.
        pushing_out = ((qv <= lo + _LIMIT_EPS) & (dq < 0)) | ((qv >= hi - _LIMIT_EPS) & (dq > 0))
        if np.any(pushing_out):
            blocked = pushing_out
            dq = self._solve(jac, twist_world, blocked)

        saturated = False
        peak = float(np.max(np.abs(dq))) if dq.size else 0.0
        if peak > self.max_joint_rate:
            dq *= self.max_joint_rate / peak
            saturated = True

        q_new = np.clip(qv + dq * dt, lo, hi)
        self.set_q(q_new)
        return StepResult(q_new, [n for n, b in zip(self.joint_names, blocked, strict=True) if b], saturated)
