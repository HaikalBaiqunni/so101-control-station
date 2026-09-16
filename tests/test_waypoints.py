"""Tests for waypoint-file validation.

A malformed sequence file used to load without complaint and only fail later,
mid-playback, with the arm already moving - the worst possible moment to find
out. These tests pin the failure back to load time.
"""
from __future__ import annotations

import pytest

from ui.main_window import _validate_waypoints


class TestRejects:
    @pytest.mark.parametrize("raw,fragment", [
        ({}, "list of waypoints"),
        ("not json at all", "list of waypoints"),
        ([], "no waypoints"),
        ([1, 2], "expected an object"),
        ([{"label": "a"}], "missing a 'positions'"),
        ([{"positions": []}], "missing a 'positions'"),
        ([{"positions": {}}], "no recognisable joints"),
        ([{"positions": {"bogus_joint": 1.0}}], "no recognisable joints"),
        ([{"positions": {"gripper": "twelve"}}], "expected a number"),
        ([{"positions": {"gripper": None}}], "expected a number"),
    ])
    def test_raises_with_a_useful_message(self, raw, fragment):
        with pytest.raises(ValueError, match=fragment):
            _validate_waypoints(raw)

    def test_booleans_are_not_accepted_as_degrees(self):
        # bool is a subclass of int in Python, so a naive isinstance check
        # would let `true` through as 1.0 degrees.
        with pytest.raises(ValueError, match="expected a number"):
            _validate_waypoints([{"positions": {"gripper": True}}])

    def test_the_offending_waypoint_is_named(self):
        raw = [{"positions": {"gripper": 1.0}}, {"positions": {"gripper": "x"}}]
        with pytest.raises(ValueError, match="waypoint 2"):
            _validate_waypoints(raw)


class TestAccepts:
    def test_normalises_ints_to_floats(self):
        result = _validate_waypoints([{"label": "a", "positions": {"gripper": 1}}])
        assert result == [{"label": "a", "positions": {"gripper": 1.0}}]

    def test_supplies_a_label_when_missing(self):
        result = _validate_waypoints([{"positions": {"gripper": 0.0}}])
        assert result[0]["label"] == "Waypoint 1"

    def test_drops_unknown_joints_instead_of_failing(self):
        # A sequence recorded on an arm with an extra joint should still be
        # usable on a standard SO-101, minus the joint that does not exist.
        result = _validate_waypoints([{"positions": {"gripper": 5.0, "seventh_axis": 90.0}}])
        assert result[0]["positions"] == {"gripper": 5.0}

    def test_preserves_order(self):
        raw = [{"label": str(i), "positions": {"gripper": float(i)}} for i in range(5)]
        assert [wp["label"] for wp in _validate_waypoints(raw)] == ["0", "1", "2", "3", "4"]
