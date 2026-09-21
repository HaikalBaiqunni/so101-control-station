"""Which physical robot this GUI is currently pointed at.

Deliberately dependency-light: no Feetech or Damiao imports here, no bus
logic. This module answers exactly one question - "given the selected robot,
what joints does its digital twin have, and where's its default MJCF?" -
so it can be imported by ui/main_window.py without dragging in either
hardware backend.

reBot B601-DM support here is simulation/twin-only for now (see
`hardware_available`). Its real motors are Damiao CAN devices over a custom
pyserial-framed protocol - a completely different transport from Feetech's
serial register bus that `core/servo_bus.py` implements - and no per-joint
CAN id mapping is documented anywhere in this project's resources yet (that
has to come from Damiao's own PC tool first). Wiring real hardware control
for it is deliberately a separate, later phase of work.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .servo_bus import DEFAULT_JOINT_IDS

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODELS_DIR = os.path.join(os.path.dirname(_HERE), "models")


@dataclass(frozen=True)
class RobotProfile:
    key: str                  # stable id, used as the gui_settings.json key
    label: str                 # shown in the selector combo box
    joint_order: tuple[str, ...]  # drives the digital twin (and, once it
                                    # exists, any hardware backend for this robot)
    default_mjcf_path: str    # absolute path, or "" if nothing bundled
    hardware_available: bool  # False = twin/preview only, no RobotWorker for it
    # Only consulted when hardware_available is False - with a real bus
    # connected, joint limits always come from ITS calibration instead (see
    # MainWindow._load_joint_limits), so this is exactly the "no bus to ask"
    # fallback that seeds the Joint Control sliders for a twin-only preview.
    # Taken from this MJCF's own compiled joint ranges (verified directly:
    # joint1 +-2.8 rad, joint2/3 -3.14..0, joint4 -1.87..1.57, joint5 +-1.57,
    # joint6 +-3.14, converted to degrees here since JointRow's slider/spin
    # are degree-scaled). finger_left is the one exception: it's a slide
    # joint measured in metres (0..0.05 m) on the real MJCF, not degrees -
    # kept as its true numeric range anyway rather than inventing a fake
    # degree-like number, since the slider only needs *a* sensible numeric
    # range to move smoothly and feed DigitalTwin.set_joint_fraction (which
    # only ever wants a 0..1 fraction, not a specific unit) - the cost is
    # that row's " deg" suffix is cosmetically wrong for this one joint,
    # a known, deliberately-not-solved-here Phase 1 simplification.
    preview_ranges: dict[str, tuple[float, float]] = None  # type: ignore[assignment]
    # -- Cartesian jogging (core/kinematics.py) ---------------------------------
    # Which of joint_order carry the TCP through space. The gripper is
    # deliberately absent: it doesn't move the tool centre point, and a jog
    # solver that could "use" it to hit a target would just open the jaws.
    # Empty means "every joint but the last" (the gripper, by convention).
    arm_joints: tuple[str, ...] = ()
    # Where the tool centre point is in that robot's MJCF. Plain strings
    # rather than kinematics.TcpSpec so this module stays free of MuJoCo
    # imports (see the module docstring) - MainWindow builds the TcpSpec.
    # Empty tcp_site + empty tcp_body = let the kinematics auto-detect one.
    tcp_site: str = ""
    tcp_body: str = ""
    tcp_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self):
        if self.preview_ranges is None:
            object.__setattr__(self, "preview_ranges", {})


PROFILES: dict[str, RobotProfile] = {
    "so101": RobotProfile(
        key="so101",
        label="SO-101 (Feetech)",
        # Reuses servo_bus's own joint map rather than a second hardcoded
        # copy - core/digital_twin.py used to keep its own independent
        # JOINT_NAMES list with the same 6 names, which was a latent
        # drift risk (nothing enforced the two ever agreeing). This is the
        # one place that list is spelled out now.
        joint_order=tuple(DEFAULT_JOINT_IDS.keys()),
        default_mjcf_path="",
        hardware_available=True,
        # Five joints move the tool; the sixth servo is the gripper. So Cartesian
        # jogging on an SO-101 is inherently 5-DoF - see KinematicChain.reachability.
        arm_joints=("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"),
        # No bundled MJCF to name a site in - the kinematics tries the usual
        # site names and otherwise falls back to the wrist_roll body's origin.
    ),
    "rebot_b601_dm": RobotProfile(
        key="rebot_b601_dm",
        label="reBot B601-DM (simulation only)",
        # Matches models/rebot_b601_dm/rebot_b601_dm.xml exactly: joint1..6
        # (arm) + finger_left (the gripper's one actual actuator - its
        # "finger_right" is a mimic follower driven by an <equality> in that
        # MJCF, not something this app ever needs to command directly).
        joint_order=("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "finger_left"),
        default_mjcf_path=os.path.join(_MODELS_DIR, "rebot_b601_dm", "rebot_b601_dm.xml"),
        hardware_available=False,
        arm_joints=("joint1", "joint2", "joint3", "joint4", "joint5", "joint6"),
        # This MJCF has no <site>. Its gripper fingers hang off link6 at
        # z = 0.15539 (finger_left_link's pos in the model), which is the tool
        # centre for a two-finger gripper.
        tcp_body="link6",
        tcp_offset=(0.0, 0.0, 0.15539),
        preview_ranges={
            "joint1": (-160.4, 160.4),
            "joint2": (-179.9, 0.0),
            "joint3": (-179.9, 0.0),
            "joint4": (-107.1, 90.0),
            "joint5": (-90.0, 90.0),
            "joint6": (-179.9, 179.9),
            # 0..100 "percent open" pseudo-range, NOT the real MJCF joint's
            # true 0..0.05 metre travel - JointRow's slider ticks are
            # SLIDER_SCALE(=10)-scaled ints, and int(0.05 * 10) == 0 collapses
            # a literal-metres range to a slider whose min and max are both
            # 0 (confirmed: this is why the gripper slider couldn't be
            # dragged at all). Only the 0..1 FRACTION within this range ever
            # reaches DigitalTwin.set_joint_fraction (see its own docstring),
            # so the range's actual unit is arbitrary - just needs to survive
            # that x10 scaling with room to drag.
            "finger_left": (0.0, 100.0),
        },
    ),
}

DEFAULT_PROFILE_KEY = "so101"


def profile_arm_joints(profile: RobotProfile) -> tuple[str, ...]:
    """The joints that place the TCP - explicit if the profile says so,
    otherwise everything except the trailing gripper joint."""
    return profile.arm_joints or profile.joint_order[:-1]


def get_profile(key: str) -> RobotProfile:
    return PROFILES.get(key, PROFILES[DEFAULT_PROFILE_KEY])
