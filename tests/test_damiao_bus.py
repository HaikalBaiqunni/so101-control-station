"""Unit tests for core/damiao_bus.py's POS_VEL/MIT control-mode branching
(Phase 2.5) - pure logic, no real serial port or Qt needed. Mocks
dm_can.MotorControl so real command-packing/CAN framing is never exercised,
only that DamiaoBus calls the right MotorControl method with the right
arguments for the mode it was configured with."""
from __future__ import annotations

import math

import pytest

from core import damiao_bus as db
from core.dm_can import Control_Type, DM_Motor_Type, Motor


class FakeMotorControl:
    """Records every call instead of talking to a real bus."""

    def __init__(self, _ser):
        self.calls: list[tuple] = []

    def addMotor(self, _motor):
        pass

    def switchControlMode(self, _motor, _mode):
        return True

    def refresh_motor_status(self, motor):
        motor.recv_data(1.23, 0.0, 0.0)  # arbitrary "current position" for seed tests

    def control_Pos_Vel(self, motor, p_desired, v_desired):
        self.calls.append(("pos_vel", motor.SlaveID, p_desired, v_desired))

    def controlMIT(self, motor, kp, kd, q, dq, tau):
        self.calls.append(("mit", motor.SlaveID, kp, kd, q, dq, tau))

    def enable(self, motor):
        self.calls.append(("enable", motor.SlaveID))

    def disable(self, motor):
        self.calls.append(("disable", motor.SlaveID))


@pytest.fixture(autouse=True)
def fake_serial_and_motorcontrol(monkeypatch):
    monkeypatch.setattr(db, "MotorControl", FakeMotorControl)
    monkeypatch.setattr(db.serial, "Serial", lambda *a, **k: object())


RANGES = {"joint1": (-160.0, 160.0), "joint4": (-90.0, 90.0)}
IDS = {"joint1": (1, 0x11), "joint4": (4, 0x14)}


def test_pos_vel_is_the_default_and_uses_control_pos_vel():
    bus = db.DamiaoBus("COM_FAKE", IDS, RANGES)
    bus.connect()
    bus.write_goals_deg({"joint1": 10.0})
    fake_mc = bus._mc
    assert fake_mc.calls[-1][0] == "pos_vel"


def test_mit_mode_uses_controlmit_with_the_right_gains():
    bus = db.DamiaoBus(
        "COM_FAKE", IDS, RANGES,
        control_mode=Control_Type.MIT,
        mit_gains={"joint1": (12.0, 0.7)},
    )
    bus.connect()
    bus.write_goals_deg({"joint1": 10.0})
    kind, slave_id, kp, kd, _q, _dq, _tau = bus._mc.calls[-1]
    assert kind == "mit"
    assert slave_id == 1
    assert (kp, kd) == (12.0, 0.7)


def test_mit_mode_falls_back_to_conservative_default_gains_for_an_unlisted_joint():
    bus = db.DamiaoBus("COM_FAKE", IDS, RANGES, control_mode=Control_Type.MIT, mit_gains={})
    bus.connect()
    bus.write_goals_deg({"joint4": 5.0})
    kind, _slave_id, kp, kd, *_ = bus._mc.calls[-1]
    assert kind == "mit"
    assert (kp, kd) == (db.DEFAULT_MIT_KP, db.DEFAULT_MIT_KD)


def test_enable_torque_seeds_with_control_pos_vel_in_pos_vel_mode():
    bus = db.DamiaoBus("COM_FAKE", IDS, RANGES)
    bus.connect()
    bus.enable_torque("joint1")
    kinds = [c[0] for c in bus._mc.calls]
    assert kinds[-2:] == ["pos_vel", "enable"]


def test_enable_torque_seeds_with_controlmit_in_mit_mode():
    bus = db.DamiaoBus(
        "COM_FAKE", IDS, RANGES,
        control_mode=Control_Type.MIT,
        mit_gains={"joint1": (9.0, 0.4)},
    )
    bus.connect()
    bus.enable_torque("joint1")
    kinds = [c[0] for c in bus._mc.calls]
    assert kinds[-2:] == ["mit", "enable"]
    mit_call = bus._mc.calls[-2]
    assert (mit_call[2], mit_call[3]) == (9.0, 0.4)


def test_joints_1_to_3_use_dm4340_proxy_and_4_to_7_stay_dm4310():
    ids = {"joint1": (1, 0x11), "joint3": (3, 0x13), "joint4": (4, 0x14), "finger_left": (7, 0x17)}
    bus = db.DamiaoBus("COM_FAKE", ids, RANGES)
    bus.connect()
    assert bus._motors["joint1"].MotorType == DM_Motor_Type.DM4340
    assert bus._motors["joint3"].MotorType == DM_Motor_Type.DM4340
    assert bus._motors["joint4"].MotorType == DM_Motor_Type.DM4310
    assert bus._motors["finger_left"].MotorType == DM_Motor_Type.DM4310


def test_connect_uses_the_configured_control_mode_for_switchcontrolmode(monkeypatch):
    seen_modes = []

    class RecordingMC(FakeMotorControl):
        def switchControlMode(self, _motor, mode):
            seen_modes.append(mode)
            return True

    monkeypatch.setattr(db, "MotorControl", RecordingMC)
    bus = db.DamiaoBus("COM_FAKE", IDS, RANGES, control_mode=Control_Type.MIT)
    bus.connect()
    assert all(m == Control_Type.MIT for m in seen_modes)


def test_connect_raises_when_switchcontrolmode_never_confirms(monkeypatch):
    class RefusingMC(FakeMotorControl):
        def switchControlMode(self, _motor, _mode):
            return False

    monkeypatch.setattr(db, "MotorControl", RefusingMC)
    bus = db.DamiaoBus("COM_FAKE", IDS, RANGES)
    with pytest.raises(db.DamiaoBusError):
        bus.connect()


def test_motor_object_is_real_dm_can_motor_not_a_stub():
    # Sanity check the fixture setup itself uses the real Motor class (only
    # MotorControl/serial are faked) - a regression here would mean these
    # tests are accidentally exercising a different code path than production.
    bus = db.DamiaoBus("COM_FAKE", IDS, RANGES)
    bus.connect()
    assert isinstance(bus._motors["joint1"], Motor)


def test_write_goals_deg_clamps_to_a_calibrated_range_narrower_than_the_default():
    # Regression test for a real incident this session: preview_ranges'
    # ASSUMED gripper range let write_goals_deg clamp toward a position past
    # this unit's true mechanical stop, stalling the motor until a fuse
    # blew. ui/dm_gripper_calibration_dialog.py exists to replace that
    # assumed range with a measured one - confirm the override actually
    # narrows what gets clamped to, not just what deg_limits() reports.
    wide_ranges = {"joint1": (-160.0, 160.0), "joint4": (-90.0, 90.0)}
    narrow_ranges = {"joint1": (-100.0, 100.0), "joint4": (-90.0, 90.0)}
    bus_wide = db.DamiaoBus("COM_FAKE", IDS, wide_ranges)
    bus_narrow = db.DamiaoBus("COM_FAKE", IDS, narrow_ranges)
    bus_wide.connect()
    bus_narrow.connect()

    bus_wide.write_goals_deg({"joint1": 150.0})
    bus_narrow.write_goals_deg({"joint1": 150.0})

    wide_p_desired = bus_wide._mc.calls[-1][2]
    narrow_p_desired = bus_narrow._mc.calls[-1][2]
    assert wide_p_desired == pytest.approx(math.radians(150.0))
    # Would have been clamped to the wrong (too-wide) default before a real
    # measured range existed - now clamps to the calibrated stop instead.
    assert narrow_p_desired == pytest.approx(math.radians(100.0))


# ---------------------------------------------------------------- motor status nibble / faults
def test_status_nibble_is_decoded_from_a_feedback_frame():
    from core.dm_can import MotorControl
    mc = MotorControl.__new__(MotorControl)          # no serial port: only the frame decoder is exercised
    motor = Motor(DM_Motor_Type.DM4310, 7, 0x17)
    mc.motors_map = {0x7: motor}                       # keyed by the master id in the low nibble of byte 0
    process = mc._MotorControl__process_packet
    # data[0] = status nibble (0xE = overload) | master id 0x7; q / dq / tau all centred
    process(bytes([0xE7, 0x80, 0x00, 0x80, 0x08, 0x00, 0, 0]), 0x00, 0x11)
    assert motor.err_state == 0xE
    process(bytes([0x17, 0x80, 0x00, 0x80, 0x08, 0x00, 0, 0]), 0x00, 0x11)
    assert motor.err_state == 0x1


def test_state_names_and_fault_test():
    assert db.describe_motor_state(0xE) == "overload"
    assert db.describe_motor_state(0xA) == "overcurrent"
    assert db.describe_motor_state(None) == "unknown"
    assert db.describe_motor_state(0x5) == "state 0x5"
    assert db.is_fault(0xE) and db.is_fault(0x8) and not db.is_fault(0x1) and not db.is_fault(0x0) and not db.is_fault(None)


class FaultyMotorControl(FakeMotorControl):
    """Reports an overload until clear_error() is called."""

    def __init__(self, _ser):
        super().__init__(_ser)
        self.faulted = True

    def refresh_motor_status(self, motor):
        motor.recv_data(1.23, 0.0, 0.0, 0xE if self.faulted else 0x0)

    def clear_error(self, motor):
        self.calls.append(("clear_error", motor.SlaveID))
        self.faulted = False


def test_enabling_clears_a_latched_fault_first(monkeypatch):
    monkeypatch.setattr(db, "MotorControl", FaultyMotorControl)
    bus = db.DamiaoBus("COM_FAKE", IDS, RANGES)
    bus.connect()
    assert bus.motor_states() == {"joint1": None, "joint4": None}   # nothing heard yet
    bus.enable_torque("joint1")
    kinds = [c[0] for c in bus._mc.calls]
    assert kinds.index("clear_error") < kinds.index("enable")


def test_enabling_a_healthy_motor_sends_no_clear_error():
    bus = db.DamiaoBus("COM_FAKE", IDS, RANGES)
    bus.connect()
    bus.enable_torque()
    assert "clear_error" not in [c[0] for c in bus._mc.calls]
