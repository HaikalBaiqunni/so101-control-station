"""Gravity torques of the B601-DM from its own MuJoCo model, for MIT feed-forward.

With qvel = 0, MuJoCo's `qfrc_bias` is exactly the joint torque needed to hold the
arm still against gravity. Sent as the `tau` term of an MIT command it carries the
arm's weight, so `kp` only has to correct small errors instead of carrying the load.

Pure logic: a private MjModel (no GL, no Qt, no hardware). Positions are the same
degrees the GUI uses; they are fed to the MJCF joints unchanged, which is right
because the B601-DM profile's joint ranges ARE the MJCF's joint ranges (see
RobotProfile.preview_ranges). Whether the SIGN also matches on a real unit is what
the app's gravity check verifies against the motors' own torque feedback before it
lets the feed-forward be switched on.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

import mujoco

# Hard ceiling on the feed-forward torque sent to each joint (N*m), whatever the model
# says. Joints 1-3 are the big DM4340-class motors, 4-6 the DM4310 wrist motors; the
# gripper gets none. Deliberately well under the motors' peak so a wrong model can
# never ask for a violent torque.
FF_TAU_CAP_NM = {"joint1": 12.0, "joint2": 12.0, "joint3": 12.0, "joint4": 4.0, "joint5": 4.0, "joint6": 4.0}


def clamp_ff(joint: str, tau: float) -> float:
    cap = FF_TAU_CAP_NM.get(joint, 0.0)
    return max(-cap, min(cap, tau))


class GravityModel:
    def __init__(self, mjcf_path: str, joint_names: Iterable[str]):
        self.model = mujoco.MjModel.from_xml_path(mjcf_path)
        self._data = mujoco.MjData(self.model)
        self.total_mass_kg = float(self.model.body_mass.sum())
        self._qpos_adr: dict[str, int] = {}
        self._dof_adr: dict[str, int] = {}
        for name in joint_names:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            # revolute joints only: the gripper's slide joint is not gravity-compensated
            if jid >= 0 and self.model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_HINGE and name in FF_TAU_CAP_NM:
                self._qpos_adr[name] = int(self.model.jnt_qposadr[jid])
                self._dof_adr[name] = int(self.model.jnt_dofadr[jid])

    @property
    def joint_names(self) -> list[str]:
        return list(self._qpos_adr)

    def torques(self, positions_deg: dict[str, float]) -> dict[str, float]:
        """Torque (N*m, along each joint's positive direction) that holds the arm still
        at `positions_deg`. A joint missing from the input is left at the model's rest value."""
        data = self._data
        data.qpos[:] = self.model.qpos0
        data.qvel[:] = 0.0
        for name, adr in self._qpos_adr.items():
            if name in positions_deg:
                data.qpos[adr] = math.radians(positions_deg[name])
        mujoco.mj_forward(self.model, data)
        return {name: float(data.qfrc_bias[dof]) for name, dof in self._dof_adr.items()}
