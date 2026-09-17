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
    def __init__(
        self,
        mjcf_path: str,
        width: int = 480,
        height: int = 360,
        joint_names: list[str] | None = None,
    ):
        # Defaults to the SO-101 list so every existing caller (nothing
        # passed this before) behaves exactly as it did - only a caller that
        # knows about a different robot (see core/robot_profiles.py) needs
        # to pass its own joint_names.
        self.joint_names = list(joint_names) if joint_names is not None else list(JOINT_NAMES)
        self.model = mujoco.MjModel.from_xml_path(mjcf_path)
        self.data = mujoco.MjData(self.model)
        self.width = width
        self.height = height
        self.renderer = mujoco.Renderer(self.model, height=height, width=width)
        # A free camera MuJoCo itself owns nothing of - unlike qpos, nothing
        # about camera pose is simulation state, so it's fine to mutate this
        # directly from mouse input with no physics implications either way.
        # mjv_defaultFreeCamera fits it to THIS model's own bounding box, so
        # a small gripper-only scene and a full arm scene each start at a
        # sane distance instead of one arbitrary default that's wrong for
        # most models.
        self.camera = mujoco.MjvCamera()
        mujoco.mjv_defaultFreeCamera(self.model, self.camera)
        # Group 3 is this project's own convention (see
        # ReBot_B601_MuJoCo/scripts/convert_urdf_to_mjcf.py) for the
        # collision-derived geoms MuJoCo's URDF importer keeps alongside the
        # material-split visual ones - a plain single-color duplicate of the
        # same geometry that, left visible, sits on the SAME default group as
        # everything else and washes out the real material colors (confirmed
        # directly: this is why the B601-DM's Seeed-yellow accents rendered
        # far duller than the real arm). Hiding only group 3 - not a whole
        # default group like 0, which ordinary content overwhelmingly uses -
        # keeps this safe for an arbitrary MJCF a user loads that never heard
        # of this convention: group 3 free of any content just means nothing
        # changes.
        self.scene_option = mujoco.MjvOption()
        mujoco.mjv_defaultOption(self.scene_option)
        self.scene_option.geomgroup[3] = 0
        self._joint_qpos_adr: dict[str, int] = {}
        self._joint_range: dict[str, tuple[float, float]] = {}
        for name in self.joint_names:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid >= 0:
                self._joint_qpos_adr[name] = self.model.jnt_qposadr[jid]
                lo, hi = self.model.jnt_range[jid]
                if lo < hi:  # a joint with no declared limit reports (0, 0) - don't clamp that
                    self._joint_range[name] = (lo, hi)
        # Mimic joints (e.g. the B601-DM gripper's finger_right, which has no
        # actuator of its own - see convert_urdf_to_mjcf.py's MIMIC_PAIRS)
        # are only kept in sync by MuJoCo's constraint SOLVER, which runs
        # during mj_step - confirmed directly that a single mj_forward call
        # (all render() ever does; this twin is a kinematic pose preview, not
        # a running simulation) never moves them, since it only recomputes
        # derived quantities from the CURRENT qpos rather than integrating
        # time to let constraint forces act. Without this, dragging the
        # driven joint's slider visibly moves only one gripper finger.
        # Reading the model's own <equality> constraints here (rather than
        # hardcoding "finger_left/finger_right" by name) means any future
        # mimic pair in any profile's MJCF is handled the same way for free.
        self._mimic_followers: list[tuple[int, int, float, float]] = []  # (follower_adr, driven_adr, coef0, coef1)
        for i in range(self.model.neq):
            if self.model.eq_type[i] != mujoco.mjtEq.mjEQ_JOINT:
                continue
            obj1 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_JOINT, self.model.eq_obj1id[i])
            obj2 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_JOINT, self.model.eq_obj2id[i])
            coef0, coef1 = self.model.eq_data[i][0], self.model.eq_data[i][1]
            # MuJoCo's constraint equation is qpos[obj1] = coef0 + coef1*qpos[obj2].
            # Whichever side ISN'T one of this profile's directly-driven
            # joint_names is the follower - the other side is what
            # set_joint_deg/set_joint_fraction actually write to.
            obj1_driven = obj1 in self._joint_qpos_adr
            obj2_driven = obj2 in self._joint_qpos_adr
            if obj1_driven and not obj2_driven:
                self._mimic_followers.append((
                    self.model.jnt_qposadr[self.model.eq_obj2id[i]],
                    self._joint_qpos_adr[obj1],
                    -coef0 / coef1, 1.0 / coef1,
                ))
            elif obj2_driven and not obj1_driven:
                self._mimic_followers.append((
                    self.model.jnt_qposadr[self.model.eq_obj1id[i]],
                    self._joint_qpos_adr[obj2],
                    coef0, coef1,
                ))
            # both or neither driven: ambiguous for a simple preview - skip.
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

    # ---------------------------------------------------------------- camera (mouse-driven)
    # dx/dy are fractions of the viewport (e.g. pixels-dragged / height), the
    # same convention MuJoCo's own mjv_moveCamera expects - it scales the
    # actual rotate/pan/zoom speed by the CURRENT camera distance and the
    # scene's own bounding box internally, which is what makes one universal
    # "feel" work for both a close-up gripper-only model and a full-arm one
    # without a speed constant tuned per scene.
    def orbit(self, dx: float, dy: float) -> None:
        mujoco.mjv_moveCamera(self.model, mujoco.mjtMouse.mjMOUSE_ROTATE_H, dx, dy, self.renderer.scene, self.camera)

    def pan(self, dx: float, dy: float) -> None:
        mujoco.mjv_moveCamera(self.model, mujoco.mjtMouse.mjMOUSE_MOVE_H, dx, dy, self.renderer.scene, self.camera)

    def zoom(self, dy: float) -> None:
        mujoco.mjv_moveCamera(self.model, mujoco.mjtMouse.mjMOUSE_ZOOM, 0.0, dy, self.renderer.scene, self.camera)

    def reset_camera(self) -> None:
        mujoco.mjv_defaultFreeCamera(self.model, self.camera)

    def render(self) -> np.ndarray:
        for follower_adr, driven_adr, coef0, coef1 in self._mimic_followers:
            self.data.qpos[follower_adr] = coef0 + coef1 * self.data.qpos[driven_adr]
        mujoco.mj_forward(self.model, self.data)
        self.renderer.update_scene(self.data, camera=self.camera, scene_option=self.scene_option)
        return self.renderer.render()  # HxWx3 uint8 RGB
