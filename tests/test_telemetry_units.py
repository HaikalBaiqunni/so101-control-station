"""Tests for the telemetry register -> real-unit conversions.

These scale factors are the app's least certain numbers - two of them are
cross-checked against a community driver rather than a vendor datasheet, and
one (velocity) is explicitly a derived estimate. Pinning them in tests means a
future "cleanup" cannot silently change what a logged CSV means.
"""
from __future__ import annotations

import pytest

from ui.telemetry_panel import DEG_PER_TICK, _ticks_delta, convert_telemetry


class TestConversions:
    def test_voltage_is_tenths_of_a_volt(self):
        value, unit = convert_telemetry("voltage", 74)
        assert value == pytest.approx(7.4)
        assert unit == "V"

    def test_temperature_passes_through_as_celsius(self):
        assert convert_telemetry("temperature", 41) == (41.0, "°C")

    def test_current_uses_the_65_milliamp_step(self):
        assert convert_telemetry("current", 100)[0] == pytest.approx(650.0)

    def test_load_is_signed_at_bit_10_and_scaled_to_percent(self):
        assert convert_telemetry("load", 500)[0] == pytest.approx(50.0)
        assert convert_telemetry("load", (1 << 10) | 500)[0] == pytest.approx(-50.0)

    def test_velocity_is_signed_at_bit_15(self):
        forward = convert_telemetry("velocity", 100)[0]
        reverse = convert_telemetry("velocity", (1 << 15) | 100)[0]
        assert forward == pytest.approx(-reverse)

    def test_velocity_unit_is_flagged_as_unconfirmed(self):
        # The trailing "*" is the only thing telling a reader this figure is
        # derived rather than documented - losing it would be a quiet
        # downgrade in honesty, not a cosmetic change.
        assert convert_telemetry("velocity", 0)[1].endswith("*")

    def test_unknown_field_is_passed_through_unitless(self):
        assert convert_telemetry("position", 2047) == (2047.0, "")


class TestEncoderWraparound:
    """wrist_roll is a full continuous turn, so it crosses the 0/4095 boundary
    routinely - without shortest-path differencing that reads as a 4096-tick
    spike, which is precisely the artefact the velocity cross-check exists to
    rule out."""

    def test_small_forward_step(self):
        assert _ticks_delta(110, 100) == 10

    def test_small_reverse_step(self):
        assert _ticks_delta(100, 110) == -10

    def test_wrap_upward_over_zero(self):
        assert _ticks_delta(5, 4090) == 11

    def test_wrap_downward_over_zero(self):
        assert _ticks_delta(4090, 5) == -11

    def test_exactly_half_a_turn_stays_bounded(self):
        assert abs(_ticks_delta(2048, 0)) <= 2048

    def test_resolution_matches_a_12_bit_encoder(self):
        assert DEG_PER_TICK * 4096 == pytest.approx(360.0)
