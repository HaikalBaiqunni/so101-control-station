"""Unit tests for core/damiao_bus.py's POS_VEL/MIT control-mode branching
(Phase 2.5) - pure logic, no real serial port or Qt needed. Mocks
dm_can.MotorControl so real command-packing/CAN framing is never exercised,
only that DamiaoBus calls the right MotorControl method with the right
arguments for the mode it was configured with."""
from __future__ import annotations

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
