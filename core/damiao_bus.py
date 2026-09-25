"""
Real hardware driver for the reBot B601-DM's Damiao CAN motors - the DM
equivalent of core/servo_bus.py's ServoBus (Feetech), with as much of the same
external shape (method names, degree-based position API, deg_limits) as makes
sense so ui/main_window.py's existing follower-worker code barely needs to
know which bus type it's holding.

Built on core/dm_can.py (vendored from cmjang/DM_Control_Python - see
core/LICENSE_DM_CAN.txt), the SAME library core/dm_setup_worker.py already
uses for CAN-id assignment/enable-disable/PID read-write in the Setup tab -
this file adds the missing piece: actually commanding a position, not just
configuring the motor's own registers.

POS_VEL control mode, not MIT - confirmed directly this session that MIT
mode's kp/kd/q/dq/tau are scaled against a per-motor-TYPE table
(dm_can.MotorControl.Limit_Param) that this codebase currently gets wrong
(every motor hardcoded as DM4310 in dm_setup_worker.py, when the real B601-DM
motors are two different models - J4340P for joints 1-3, DM4310 for 4-7 per
assign_can_id.py's own comment). POS_VEL sends raw position/velocity floats
straight through and relies on the motor's OWN onboard position/velocity
loop (its KP_ASR/KI_ASR/KP_APR/KI_APR gains - already exposed for read/write
in ui/dm_setup_panel.py), sidestepping that per-type scaling table entirely
rather than needing it fixed first.
"""
from __future__ import annotations

import math
import threading

import serial

from .dm_can import Control_Type, DM_Motor_Type, Motor, MotorControl

BAUD = 921600  # matches dm_setup_worker.py and Damiao's own DM_Tools

# Deliberately slow for the first real-hardware pass of this driver - not
# user-adjustable yet (see JogPanel's speed slider, which today only scales
# Feetech degrees/second; wiring it through to this is future work once this
# conservative default has actually been run against the real arm). Worth
# revisiting together once basic position control is confirmed safe, not a
# number to guess bigger in isolation.
MAX_JOG_VEL_RAD_S = 0.3

# Very gentle starting point for MIT mode's per-command kp/kd (stiffness/
# damping the MIT frame itself carries - NOT the motor's own persisted
# KP_ASR/KP_APR registers, see ui/dm_setup_panel.py's Motion Control section).
# Only used as a fallback if a joint is somehow missing from the mit_gains
# dict passed in - the real defaults live in the UI, tuned live on hardware.
DEFAULT_MIT_KP = 8.0
DEFAULT_MIT_KD = 0.5

# Real joints 1-3 are Seeed's "J4340P" motors - dm_can.py's DM_Motor_Type only
# has DM4340/DM4340_48V (Damiao's own naming), no literal J4340P entry, so
# DM4340 is used here as the closest available proxy for this table lookup.
# UNVERIFIED against a real J4340P datasheet - confirm Q_MAX/DQ_MAX/TAU_MAX
# match before relying on MIT mode's torque limiting for these three joints.
# Joints 4-7 are DM4310, which was already correct in Phase 2.
_MOTOR_TYPE_BY_JOINT = {
    "joint1": DM_Motor_Type.DM4340,
    "joint2": DM_Motor_Type.DM4340,
    "joint3": DM_Motor_Type.DM4340,
}
_DEFAULT_MOTOR_TYPE = DM_Motor_Type.DM4310


class DamiaoBusError(RuntimeError):
    pass


class DamiaoBus:
    """One shared serial connection + dm_can.MotorControl driving every
    joint of the arm - confirmed this session that MotorControl already
    supports multiple motors multiplexed by CAN id over one connection
    (its motors_map dict, exercised already by dm_setup_worker.py's
    "verify all" feature), so this mirrors that established pattern rather
    than opening one connection per joint."""

    def __init__(
        self,
        port: str,
        id_master_by_joint: dict[str, tuple[int, int]],
        ranges_deg: dict[str, tuple[float, float]],
        control_mode: Control_Type = Control_Type.POS_VEL,
        mit_gains: dict[str, tuple[float, float]] | None = None,
    ):
        self.port = port
        self.id_master_by_joint = dict(id_master_by_joint)
        self.ranges_deg = dict(ranges_deg)
        self.control_mode = control_mode
        self.mit_gains = dict(mit_gains) if mit_gains else {}
        self._ser: serial.Serial | None = None
        self._mc: MotorControl | None = None
        self._motors: dict[str, Motor] = {}
        self._lock = threading.Lock()
        # Empty on purpose - core/robot_profiles.py's preview_ranges is what
        # this phase uses for safe operating range (see deg_limits below),
        # not a per-joint MotorCalibration the way ServoBus has. Present so
        # ui/main_window.py's _open_directions(bus) (a Feetech-specific
        # gripper-direction helper that iterates bus.calibration.items())
        # degrades to "nothing configured" instead of an AttributeError for
        # this bus type.
        self.calibration: dict = {}

    # ---------------------------------------------------------------- connection
    def connect(self) -> None:
        try:
            self._ser = serial.Serial(self.port, BAUD, timeout=0.5)
            self._mc = MotorControl(self._ser)
        except (serial.SerialException, OSError) as exc:
            raise DamiaoBusError(str(exc)) from exc

        for name, (can_id, master_id) in self.id_master_by_joint.items():
            # Motor type matters for MIT commands and for decoding ANY
            # telemetry read (refresh_motor_status scales against
            # Limit_Param[MotorType] regardless of control mode) - see
            # _MOTOR_TYPE_BY_JOINT's own comment for the DM4340-as-proxy-for-
            # J4340P caveat. POS_VEL motion commands themselves still ignore
            # this entirely.
            motor_type = _MOTOR_TYPE_BY_JOINT.get(name, _DEFAULT_MOTOR_TYPE)
            motor = Motor(motor_type, can_id, master_id)
            self._mc.addMotor(motor)
            self._motors[name] = motor
            # CTRL_MODE (RID 10) is a persisted register separate from
            # anything id-assignment touches - confirmed directly this
            # session (see core/dm_setup_worker.py's enable handler) that a
            # motor whose mode was never explicitly set doesn't reliably
            # respond to position commands or arm from a bare enable().
            ok = self._mc.switchControlMode(motor, self.control_mode)
            if not ok:
                raise DamiaoBusError(
                    f"{name}: could not confirm {self.control_mode.name} control mode - check power/wiring"
                )

    def disconnect(self) -> None:
        if self._mc:
            for motor in self._motors.values():
                try:
                    self._mc.disable(motor)
                except (serial.SerialException, OSError):
                    pass  # best-effort on the way out, matches ServoBus.disconnect
        if self._ser:
            self._ser.close()

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    # ---------------------------------------------------------------- torque
    def enable_torque(self, name: str | None = None) -> None:
        """Before arming, seed each motor's own target at its CURRENT
        measured position - mirrors RobotWorker's identical precaution for
        Feetech torque-on (core/workers.py): enabling with no seed lets the
        motor lurch toward whatever P/V it last had in its own registers
        from a previous session, which is exactly the kind of surprise
        motion "no mistake" means avoiding."""
        for target_name, motor in self._iter_targets(name):
            self._mc.refresh_motor_status(motor)
            pos = motor.getPosition()
            if self.control_mode == Control_Type.MIT:
                kp, kd = self.mit_gains.get(target_name, (DEFAULT_MIT_KP, DEFAULT_MIT_KD))
                self._mc.controlMIT(motor, kp, kd, pos, 0.0, 0.0)
            else:
                self._mc.control_Pos_Vel(motor, pos, 0.0)
            self._mc.enable(motor)

    def disable_torque(self, name: str | None = None) -> None:
        for _name, motor in self._iter_targets(name):
            self._mc.disable(motor)

    def _iter_targets(self, name: str | None):
        if name:
            motor = self._motors.get(name)
            if motor is None:
                raise DamiaoBusError(f"unknown joint {name!r}")
            return [(name, motor)]
        return list(self._motors.items())

    # ---------------------------------------------------------------- position (degrees)
    def deg_limits(self, name: str) -> tuple[float, float]:
        return self.ranges_deg.get(name, (-180.0, 180.0))

    def write_goal_deg(self, name: str, degrees: float) -> None:
        self.write_goals_deg({name: degrees})

    def write_goals_deg(self, goals: dict[str, float]) -> None:
        """Clamps every value to ranges_deg first - defence in depth,
        confirmed directly this session that dm_can.py's control_Pos_Vel and
        controlMIT pack their arguments with NO clamping of their own (MIT's
        internal LIMIT_MIN_MAX helper is a no-op due to a local-variable
        mutation bug), unlike ServoBus.write_goals_deg's tick-range clamp for
        Feetech."""
        for name, degrees in goals.items():
            motor = self._motors.get(name)
            if motor is None:
                continue  # matches ServoBus.write_goals_deg: skip, don't abort the batch
            lo, hi = self.deg_limits(name)
            clamped = max(lo, min(hi, degrees))
            q_rad = math.radians(clamped)
            if self.control_mode == Control_Type.MIT:
                kp, kd = self.mit_gains.get(name, (DEFAULT_MIT_KP, DEFAULT_MIT_KD))
                self._mc.controlMIT(motor, kp, kd, q_rad, 0.0, 0.0)
            else:
                self._mc.control_Pos_Vel(motor, q_rad, MAX_JOG_VEL_RAD_S)

    def read_all_positions_deg(self) -> dict[str, float]:
        positions = {}
        for name, motor in self._motors.items():
            self._mc.refresh_motor_status(motor)
            positions[name] = math.degrees(motor.getPosition())
        return positions

    def read_telemetry(self) -> dict[str, dict[str, float]]:
        """Position (deg), velocity (deg/s) and load (N*m, straight off the
        motor's own torque estimate) only - confirmed directly this session
        that dm_can.py has no temperature or voltage reading at all (no RID,
        no request frame for either), unlike Feetech's register set. Reporting
        those would mean inventing a number this app has no actual source
        for, which this project's own telemetry code (see
        ui/telemetry_panel.py's convert_telemetry docstring) deliberately
        avoids doing."""
        telemetry = {}
        for name, motor in self._motors.items():
            self._mc.refresh_motor_status(motor)
            telemetry[name] = {
                "position": math.degrees(motor.getPosition()),
                "velocity": math.degrees(motor.getVelocity()),
                "load": motor.getTorque(),
            }
        return telemetry
