"""Pure-logic tests for the servo driver - no hardware, no serial port.

Everything here is encoding/scaling maths that is easy to get subtly wrong and
impossible to notice from the GUI: a sign bit off by one index still produces
plausible-looking numbers, right up until a joint runs the wrong way.
"""
from __future__ import annotations

import pytest

from core.servo_bus import (
    _EEPROM_REGISTERS,
    BAUDRATE_INDEX,
    BAUDRATE_TABLE,
    MAX_RES,
    REGISTERS,
    MotorCalibration,
    ServoBus,
    decode_sign_magnitude,
    encode_sign_magnitude,
)


class TestSignMagnitude:
    @pytest.mark.parametrize("value", [0, 1, -1, 42, -42, 2047, -2047])
    def test_roundtrip_11_bit(self, value):
        assert decode_sign_magnitude(encode_sign_magnitude(value, 11), 11) == value

    def test_negative_sets_the_direction_bit(self):
        assert encode_sign_magnitude(-5, 11) == (1 << 11) | 5

    def test_zero_is_positive_zero(self):
        # -0 and +0 must not encode differently, or a joint parked exactly at
        # its reference would flicker between two raw values.
        assert encode_sign_magnitude(0, 11) == 0

    def test_overflow_is_rejected_not_truncated(self):
        with pytest.raises(ValueError):
            encode_sign_magnitude(1 << 11, 11)


class TestRegisterTable:
    def test_eeprom_boundary_is_torque_enable(self):
        # Addresses below Torque_Enable (40) are EEPROM and need a settle
        # delay; at or above it is RAM. Lock lives at 55, so it is RAM.
        assert "Lock" not in _EEPROM_REGISTERS
        assert {"ID", "Baud_Rate", "Homing_Offset"} <= _EEPROM_REGISTERS
        for name in _EEPROM_REGISTERS:
            assert REGISTERS[name][0] < 40

    def test_telemetry_block_is_contiguous(self):
        # read_telemetry() fetches 56..63 in one transaction; that only works
        # while these five stay adjacent.
        for name, expected in [("Present_Position", 56), ("Present_Velocity", 58),
                               ("Present_Load", 60), ("Present_Voltage", 62),
                               ("Present_Temperature", 63)]:
            assert REGISTERS[name][0] == expected

    def test_baudrate_table_is_a_bijection(self):
        assert len(BAUDRATE_INDEX) == len(BAUDRATE_TABLE)
        assert all(BAUDRATE_INDEX[rate] == index for index, rate in BAUDRATE_TABLE.items())

    def test_index_zero_is_one_megabaud(self):
        # What SO-101 kits ship at and what the whole app assumes.
        assert BAUDRATE_TABLE[0] == 1_000_000


class TestDegreeConversion:
    """deg_limits() is what the sliders, the jog clamp and the twin's
    fraction mapping are all derived from, so its zero-reference matters."""

    @staticmethod
    def _bus_with(range_min: int, range_max: int) -> ServoBus:
        bus = ServoBus.__new__(ServoBus)  # no port, no SDK handles
        bus.calibration = {
            "gripper": MotorCalibration(
                id=6, drive_mode=0, homing_offset=0, range_min=range_min, range_max=range_max
            )
        }
        return bus

    def test_midpoint_is_zero_degrees(self):
        lo, hi = self._bus_with(1000, 3000).deg_limits("gripper")
        assert lo == pytest.approx(-hi)

    def test_full_span_is_360_degrees(self):
        lo, hi = self._bus_with(0, MAX_RES).deg_limits("gripper")
        assert hi - lo == pytest.approx(360.0)

    def test_uncalibrated_joint_falls_back_to_symmetric_range(self):
        assert self._bus_with(0, MAX_RES).deg_limits("elbow_flex") == (-180.0, 180.0)

    def test_asymmetric_range_keeps_its_own_midpoint(self):
        # A joint a human explored unevenly during calibration still reports
        # zero at the middle of what was actually explored, not at tick 2047.
        cal = MotorCalibration(id=1, drive_mode=0, homing_offset=0, range_min=500, range_max=1500)
        assert cal.mid == 1000
