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

from .kinematics import Tcp, TcpSpec, resolve_tcp, tcp_pose

JOINT_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]

# Translucent cyan - reads as "not really there" against both the light and
# dark parts of the model without being mistaken for a real link colour.
GHOST_RGBA = (0.30, 0.78, 1.0, 0.30)
# X/Y/Z = R/G/B, the convention every robot pendant and 3D tool uses.
AXIS_RGB = ((0.95, 0.25, 0.25), (0.25, 0.85, 0.35), (0.30, 0.50, 1.0))


class DigitalTwin:
    def __init__(
        self,
        mjcf_path: str,
        width: int = 480,
        height: int = 360,
        joint_names: list[str] | None = None,
        tcp_spec: TcpSpec | None = None,
        arm_joints: list[str] | None = None,
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

        # -- overlays: the ghost arm and the World/Tool axis gizmo ---------------
        # The ghost is a SECOND pose of the same model (its own MjData, so
        # posing it never disturbs the real one), drawn translucent on top of
        # the normal render. Fractions - not degrees - for the same reason as
        # the real pose: see set_joint_fraction.
        self.ghost_data = mujoco.MjData(self.model)
        self._ghost_fractions: dict[str, float] | None = None
        # None = no gizmo; "world"/"tool" = draw both triads, the named one
        # emphasised (that's the frame the jog buttons currently act in).
        self._frame_overlay: str | None = None
        self._tcp: Tcp | None = None
        present = [n for n in (arm_joints or self.joint_names[:-1]) if n in self._joint_qpos_adr]
        try:
            self._tcp = resolve_tcp(self.model, tcp_spec or TcpSpec(), present[-1] if present else None)
        except ValueError:
            self._tcp = None  # no usable TCP: the ghost still works, only the gizmo is unavailable

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
        self._write_fraction(self.data, name, fraction)

    def _write_fraction(self, data: mujoco.MjData, name: str, fraction: float) -> None:
        adr = self._joint_qpos_adr.get(name)
        if adr is None:
            return
        fraction = max(0.0, min(1.0, fraction))
        lo, hi = self._joint_range.get(name, (-math.pi, math.pi))
        data.qpos[adr] = lo + fraction * (hi - lo)

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
    def _move_camera(self, action, dx: float, dy: float) -> None:
        """mjv_moveCamera's Python signature changed between MuJoCo releases:
        older ones take (model, action, dx, dy, scene, camera), newer ones
        (3.11 confirmed) dropped `scene` and take (model, action, dx, dy,
        camera). Calling the wrong arity is a TypeError that kills the twin's
        render thread, so try the newer form and fall back to the older one."""
        try:
            mujoco.mjv_moveCamera(self.model, action, dx, dy, self.camera)
        except TypeError:
            mujoco.mjv_moveCamera(self.model, action, dx, dy, self.renderer.scene, self.camera)

    def orbit(self, dx: float, dy: float) -> None:
        self._move_camera(mujoco.mjtMouse.mjMOUSE_ROTATE_H, dx, dy)

    def pan(self, dx: float, dy: float) -> None:
        self._move_camera(mujoco.mjtMouse.mjMOUSE_MOVE_H, dx, dy)

    def zoom(self, dy: float) -> None:
        self._move_camera(mujoco.mjtMouse.mjMOUSE_ZOOM, 0.0, dy)

    def reset_camera(self) -> None:
        mujoco.mjv_defaultFreeCamera(self.model, self.camera)

    # ---------------------------------------------------------------- overlays
    def set_ghost_fractions(self, fractions: dict[str, float] | None) -> None:
        """Pose the translucent ghost arm, or hide it with None. Joints not
        named in `fractions` sit wherever the real arm's are, so a ghost of a
        waypoint that never recorded the gripper doesn't spuriously open it."""
        self._ghost_fractions = dict(fractions) if fractions else None

    def set_frame_overlay(self, frame: str | None) -> None:
        self._frame_overlay = frame if frame in ("world", "tool") else None

    def has_tcp(self) -> bool:
        return self._tcp is not None

    def _apply_mimics(self, data: mujoco.MjData) -> None:
        for follower_adr, driven_adr, coef0, coef1 in self._mimic_followers:
            data.qpos[follower_adr] = coef0 + coef1 * data.qpos[driven_adr]

    def _draw_ghost(self) -> None:
        scene = self.renderer.scene
        self.ghost_data.qpos[:] = self.data.qpos
        for name, fraction in self._ghost_fractions.items():
            self._write_fraction(self.ghost_data, name, fraction)
        self._apply_mimics(self.ghost_data)
        mujoco.mj_kinematics(self.model, self.ghost_data)

        first = scene.ngeom
        # DYNAMIC only: the fixed base is identical in both poses, and drawing
        # it twice would just z-fight a translucent copy over the solid one.
        mujoco.mjv_addGeoms(
            self.model, self.ghost_data, self.scene_option, mujoco.MjvPerturb(),
            int(mujoco.mjtCatBit.mjCAT_DYNAMIC), scene,
        )
        for i in range(first, scene.ngeom):
            geom = scene.geoms[i]
            geom.rgba[:] = GHOST_RGBA
            geom.matid = -1          # use rgba as-is, not the material's colour/texture
            geom.emission = 0.0
            geom.specular = 0.0
            geom.reflectance = 0.0

    def _add_arrow(self, start: np.ndarray, end: np.ndarray, width: float, rgba) -> None:
        scene = self.renderer.scene
        if scene.ngeom >= scene.maxgeom:
            return
        geom = scene.geoms[scene.ngeom]
        mujoco.mjv_initGeom(geom, mujoco.mjtGeom.mjGEOM_ARROW, np.zeros(3), np.zeros(3), np.zeros(9), np.array(rgba, dtype=np.float32))
        mujoco.mjv_connector(geom, mujoco.mjtGeom.mjGEOM_ARROW, width, start, end)
        scene.ngeom += 1

    def _draw_frames(self) -> None:
        """Two axis triads: the fixed WORLD frame at the base, and the TOOL
        frame riding on the TCP. The one the jog buttons are acting in is drawn
        solid and thick, the other faint - so "which +X is this button?" is
        answered by looking at the arrow it will move along."""
        if self._frame_overlay is None or self._tcp is None:
            return
        extent = float(self.model.stat.extent)
        tcp_pos, tcp_rot = tcp_pose(self.model, self.data, self._tcp)
        triads = (
            # Long enough that the tips clear the base link, which the origin
            # sits inside - a short triad here is simply hidden by the model.
            ("world", np.zeros(3), np.eye(3), 0.55 * extent),
            ("tool", tcp_pos, tcp_rot, 0.20 * extent),
        )
        for name, origin, rot, length in triads:
            active = name == self._frame_overlay
            width = (0.012 if active else 0.005) * extent
            alpha = 1.0 if active else 0.35
            for axis in range(3):
                rgb = AXIS_RGB[axis]
                self._add_arrow(origin, origin + length * rot[:, axis], width, (*rgb, alpha))

    def render(self, overlays: bool = True) -> np.ndarray:
        self._apply_mimics(self.data)
        mujoco.mj_forward(self.model, self.data)
        self.renderer.update_scene(self.data, camera=self.camera, scene_option=self.scene_option)
        if overlays:
            if self._ghost_fractions:
                self._draw_ghost()
            self._draw_frames()
        return self.renderer.render()  # HxWx3 uint8 RGB
