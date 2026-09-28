"""Read-only leader-arm driver for Seeed's Star Arm 102 (the reBot Arm B601-DM's
official leader), driven by FashionStar UART smart servos over
`motorbridge_smart_servo` - a completely different bus/protocol from the
follower's Damiao CAN (core/damiao_bus.py), confirmed directly this session by
reading LeRobot's own `lerobot.teleoperators.rebot_102_leader` source and by
getting `lerobot-calibrate --teleop.type=rebot_102_leader` working against the
real hardware first.

Deliberately read-only: this leader is always free-spinning by hand (moved by
the operator, never driven), matching `RebotArm102Leader.send_feedback()`
raising NotImplementedError upstream - there is no enable_torque/
write_goals_deg here at all, unlike core/damiao_bus.py's DamiaoBus.

The servo's own zero point lives in ITS flash after a one-time
`set_origin_point()` call (what `lerobot-calibrate` does) - not a software
offset file this class needs to load. This class only reads, using the same
joint_ids/joint_directions/joint_ranges as lerobot's own
config_rebot_102_leader.py defaults (copied here verbatim rather than
re-derived, including the gripper's -6 widening scale, not just a sign - see
that file's own comment: these ranges are already pre-scaled to match the
B601-DM follower's joint limits "so leader actions can drive the follower
key-for-key"). That leaves only a plain joint NAME translation for this class
to do.
"""
from __future__ import annotations

from motorbridge_smart_servo import FashionStarServo

BAUD = 1_000_000  # matches lerobot's config_rebot_102_leader.py default

# Copied verbatim from lerobot's config_rebot_102_leader.py - FashionStar's own
# per-joint servo ids on the UART bus.
JOINT_IDS: dict[str, int] = {
    "shoulder_pan": 0,
    "shoulder_lift": 1,
    "elbow_flex": 2,
    "wrist_flex": 3,
    "wrist_yaw": 4,
    "wrist_roll": 5,
    "gripper": 6,
}

# Copied verbatim - sign (and for the gripper, a widening SCALE, not just a
# sign) applied to the raw servo angle. See module docstring.
JOINT_DIRECTIONS: dict[str, float] = {
    "shoulder_pan": -1,
    "shoulder_lift": -1,
    "elbow_flex": 1,
    "wrist_flex": 1,
    "wrist_yaw": 1,
    "wrist_roll": -1,
    "gripper": -6,
}

# Copied verbatim - already pre-scaled to the B601-DM follower's own degree
# ranges (lerobot's own comment, see module docstring), keyed by FashionStar's
# names.
JOINT_RANGES: dict[str, tuple[float, float]] = {
    "shoulder_pan": (-150.0, 150.0),
    "shoulder_lift": (-200.0, 1.0),
    "elbow_flex": (-200.0, 1.0),
    "wrist_flex": (-80.0, 90.0),
    "wrist_yaw": (-90.0, 90.0),
    "wrist_roll": (-90.0, 90.0),
    "gripper": (-270.0, 0.0),
}

# Positional 1:1 mapping, base to gripper - confirmed against both the Star
# Arm 102 datasheet's Joint 0..5 + Gripper numbering and the B601-DM's own
# joint1..joint6 + finger_left ordering (core/robot_profiles.py).
FOLLOWER_NAME_BY_FASHIONSTAR_NAME: dict[str, str] = {
    "shoulder_pan": "joint1",
    "shoulder_lift": "joint2",
    "elbow_flex": "joint3",
    "wrist_flex": "joint4",
    "wrist_yaw": "joint5",
    "wrist_roll": "joint6",
    "gripper": "finger_left",
}

# Reverse of the above - used by ui/fashionstar_calibration_dialog.py to save
# a sweep-measured span (read in follower-name terms, like everything else in
# this class's public API) back into native_ranges_deg's expected FS-name keys.
FASHIONSTAR_NAME_BY_FOLLOWER_NAME: dict[str, str] = {
    follower: fs for fs, follower in FOLLOWER_NAME_BY_FASHIONSTAR_NAME.items()
}


class FashionStarBusError(RuntimeError):
    pass


class FashionStarBus:
    """One FashionStarServo connection reading all 7 leader joints - reports
    everything in the FOLLOWER's own joint names/ranges so
    ui/main_window.py's leader-handling code (deg_limits, the
    leader<->follower fraction remap) doesn't need to know this is a
    different bus type from the follower's."""

    def __init__(
        self,
        port: str,
        ranges_deg: dict[str, tuple[float, float]] | None = None,
        native_ranges_deg: dict[str, tuple[float, float]] | None = None,
    ):
        self.port = port
        # Defaults to JOINT_RANGES translated through the name map (already
        # follower-scaled, see module docstring) - callers may pass the
        # follower's own RobotProfile.preview_ranges instead, which is
        # numerically the same thing but keeps both sides sourced from one
        # place.
        self.ranges_deg = ranges_deg or {
            FOLLOWER_NAME_BY_FASHIONSTAR_NAME[name]: rng for name, rng in JOINT_RANGES.items()
        }
        # Per-joint NATIVE span (FashionStar-name keyed) used as the source
        # range for read_all_positions_deg()'s unwrap/clamp/fraction step -
        # confirmed via real-hardware measurement this session that
        # JOINT_RANGES' hardcoded defaults (copied from lerobot's own
        # unverified config) are simply wrong for at least three joints on
        # this physical unit. Falls back to the hardcoded default for any
        # joint that hasn't been swept yet via the leader calibration dialog
        # (ui/fashionstar_calibration_dialog.py).
        self.native_ranges: dict[str, tuple[float, float]] = dict(JOINT_RANGES)
        if native_ranges_deg:
            self.native_ranges.update(native_ranges_deg)
        self._bus: FashionStarServo | None = None
        # Empty on purpose - see core/damiao_bus.py's DamiaoBus.calibration
        # for the identical reasoning: degrades
        # MainWindow._open_directions(bus) to "nothing configured" instead of
        # an AttributeError for this bus type.
        self.calibration: dict = {}

    def connect(self) -> None:
        try:
            bus = FashionStarServo(self.port, baudrate=BAUD)
        except Exception as exc:
            raise FashionStarBusError(str(exc)) from exc
        for name, servo_id in JOINT_IDS.items():
            if not bus.ping(servo_id):
                bus.close()
                raise FashionStarBusError(
                    f"{name}: servo id {servo_id} did not respond - check power/wiring"
                )
        self._bus = bus

    def disconnect(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def deg_limits(self, name: str) -> tuple[float, float]:
        return self.ranges_deg.get(name, (-180.0, 180.0))

    @staticmethod
    def _unwrap(value: float, lo: float, hi: float) -> float:
        """Same multi-turn unwrap as RebotArm102Leader._round_to_valid_range:
        the servo may report value = true_angle + N*360 - subtract the
        nearest whole number of turns to bring it back into the ±180deg
        window centred on (lo+hi)/2."""
        center = (lo + hi) / 2.0
        turns = round((value - center) / 360.0)
        return value - turns * 360.0

    def _read_raw_angles(self) -> dict[str, float]:
        """One sync_monitor() round trip - RAW monitor.angle_deg per joint,
        follower-name keyed, no direction sign, no unwrap, no clamp. Shared
        by both read methods below, which each add whatever
        direction/unwrap/clamp behaviour is appropriate for their own
        purpose."""
        result = self._bus.sync_monitor(list(JOINT_IDS.values()))
        raw: dict[str, float] = {}
        for fs_name, servo_id in JOINT_IDS.items():
            monitor = result.get(servo_id)
            if monitor is None:
                raise FashionStarBusError(f"{fs_name}: servo id {servo_id} has never responded")
            raw[FOLLOWER_NAME_BY_FASHIONSTAR_NAME[fs_name]] = monitor.angle_deg
        return raw

    def read_all_native_positions_deg(self) -> dict[str, float]:
        """Native-scale reading, follower-name keyed, direction-adjusted but
        deliberately NO unwrap and NO clamp - used only by the leader
        calibration dialog (ui/fashionstar_calibration_dialog.py) to
        sweep-measure each joint's true range of motion.

        Confirmed directly this session (real-hardware measurement of raw
        angle_deg across full mechanical travel, no wrap correction applied
        at all) that none of these seven joints' raw readings actually wrap
        during normal single-turn use - applying a generic-window unwrap
        "defensively" here (an earlier version of this method did) was a
        real regression found on hardware: shoulder_lift/elbow_flex (which
        never wrap) got their real sweep readings corrupted into a
        near-exact +-180deg clamp artifact by a wrap-correction they never
        needed, and a calibration sweep has no known-correct window to
        unwrap against yet anyway - discovering it is the whole point."""
        raw = self._read_raw_angles()
        return {
            follower_name: raw[follower_name] * JOINT_DIRECTIONS[fs_name]
            for fs_name, follower_name in FOLLOWER_NAME_BY_FASHIONSTAR_NAME.items()
        }

    def read_all_positions_deg(self) -> dict[str, float]:
        raw = self._read_raw_angles()
        positions: dict[str, float] = {}
        for fs_name in JOINT_IDS:
            follower_name = FOLLOWER_NAME_BY_FASHIONSTAR_NAME[fs_name]
            direction = JOINT_DIRECTIONS[fs_name]
            sign = 1.0 if direction >= 0 else -1.0
            native_lo, native_hi = self.native_ranges[fs_name]
            # Unwrap against THIS joint's own (possibly still-default until
            # calibrated) native range - matches RebotArm102Leader's original
            # per-joint design. wrist_roll is the one joint whose real
            # measured span (~315deg) comes close enough to a full turn that
            # this can matter; every other joint's raw reading never needs
            # correcting, and using each joint's OWN range (rather than one
            # generic window) avoids corrupting a joint that legitimately
            # reads near +-180deg without ever actually wrapping.
            unwrapped = self._unwrap(raw[follower_name], native_lo * sign, native_hi * sign)
            native_position = unwrapped * direction
            native_position = max(native_lo, min(native_hi, native_position))

            # Confirmed via real-hardware bring-up this session: LeRobot's own
            # JOINT_RANGES claims to already match "the reBot B601 follower's
            # joint limits", but that's THEIR OWN follower driver's gripper
            # convention (negative, closed-to-open), not this app's own
            # RobotProfile.preview_ranges (0..100 for finger_left, derived
            # independently from our MJCF model) - the two disagree for the
            # gripper specifically (no numeric overlap at all: -270..0 vs
            # 0..100), so a value in JOINT_RANGES' terms was landing entirely
            # outside the Jog panel's finger_left spinbox range and getting
            # silently clamped to 0 by Qt every single read. Always
            # fraction-remap from the (ideally calibrated - see
            # self.native_ranges) native range onto whatever range THIS bus
            # was told to report via deg_limits() (self.ranges_deg) - keeps
            # read_all_positions_deg()'s output self-consistent with
            # deg_limits(), the same contract DamiaoBus/ServoBus already
            # follow by construction.
            target_lo, target_hi = self.ranges_deg.get(follower_name, (native_lo, native_hi))
            if native_hi > native_lo:
                fraction = (native_position - native_lo) / (native_hi - native_lo)
            else:
                fraction = 0.0
            mapped = target_lo + fraction * (target_hi - target_lo)
            positions[follower_name] = max(target_lo, min(target_hi, mapped))
        return positions
