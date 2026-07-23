"""
MuJoCo-backed digital twin: renders the SO-101 (optionally + Aero Hand) model
offscreen and hands back plain RGB numpy frames for the GUI to display.

Runs on its own QThread (see twin_worker.py), driven by a plain loop, not the
Qt main thread - a single render() call costs 40-90ms on some machines, which
would stall the whole UI's event loop if it ran there.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

JOINT_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]


class DigitalTwin:
    def __init__(self, mjcf_path: str, width: int = 480, height: int = 360):
        self.model = mujoco.MjModel.from_xml_path(mjcf_path)
        self.data = mujoco.MjData(self.model)
        self.width = width
        self.height = height
        self.renderer = mujoco.Renderer(self.model, height=height, width=width)
        self._joint_qpos_adr: dict[str, int] = {}
        self._joint_range: dict[str, tuple[float, float]] = {}
        for name in JOINT_NAMES:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid >= 0:
                self._joint_qpos_adr[name] = self.model.jnt_qposadr[jid]
                lo, hi = self.model.jnt_range[jid]
                if lo < hi:  # a joint with no declared limit reports (0, 0) - don't clamp that
                    self._joint_range[name] = (lo, hi)
        mujoco.mj_forward(self.model, self.data)

    def set_joint_deg(self, name: str, degrees: float) -> None:
        """Direct degrees->radians, clamped to the MJCF's own joint range.

        Only meaningful when there's no real calibration to reconcile against
        (e.g. pure simulation preview with no hardware connected) - real
        hardware degrees should go through set_joint_fraction instead, see
        below for why."""
        adr = self._joint_qpos_adr.get(name)
        if adr is None:
            return
        radians = math.radians(degrees)
        lo, hi = self._joint_range.get(name, (-math.inf, math.inf))
        self.data.qpos[adr] = max(lo, min(hi, radians))

    def set_joint_fraction(self, name: str, fraction: float) -> None:
        """`fraction` is 0.0-1.0 = where the joint sits within the REAL
        robot's own calibrated range_min..range_max (0 = one physical
        extreme, 1 = the other) - NOT a degree value.

        This model's own joint zero-reference does not necessarily line up
        with a given calibration's (confirmed on the gripper specifically:
        the calibrated "0 deg" is the middle of whatever range a human
        happened to explore during calibration, while this MJCF's gripper
        joint range is -10..100 deg, i.e. its own zero is nowhere near its
        centre). Mapping proportionally - "50% open in real life" -> "50% of
        this model's own travel" - sidesteps needing the two zero-references
        to agree at all."""
        adr = self._joint_qpos_adr.get(name)
        if adr is None:
            return
        fraction = max(0.0, min(1.0, fraction))
        lo, hi = self._joint_range.get(name, (-math.pi, math.pi))
        self.data.qpos[adr] = lo + fraction * (hi - lo)

    def set_all_deg(self, positions: dict[str, float]) -> None:
        for name, deg in positions.items():
            self.set_joint_deg(name, deg)

    def set_all_fractions(self, fractions: dict[str, float]) -> None:
        for name, fraction in fractions.items():
            self.set_joint_fraction(name, fraction)

    def render(self) -> np.ndarray:
        mujoco.mj_forward(self.model, self.data)
        self.renderer.update_scene(self.data)
        return self.renderer.render()  # HxWx3 uint8 RGB
