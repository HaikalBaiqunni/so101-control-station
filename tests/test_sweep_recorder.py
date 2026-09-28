"""Unit tests for core/sweep_recorder.py's SweepRecorder - the pure min/max
bookkeeping shared by both hardware calibration dialogs (leader and follower
gripper), kept separate from either dialog specifically so it's testable
without a Qt event loop."""
from __future__ import annotations

from core.sweep_recorder import SweepRecorder

JOINTS = ("joint1", "joint2")


def test_starts_with_no_spans_and_incomplete():
    recorder = SweepRecorder(JOINTS)
    assert recorder.spans() == {}
    assert not recorder.is_complete()


def test_update_tracks_running_min_and_max():
    recorder = SweepRecorder(JOINTS)
    recorder.update({"joint1": 10.0, "joint2": -5.0})
    recorder.update({"joint1": -20.0, "joint2": 30.0})
    recorder.update({"joint1": 5.0, "joint2": 0.0})
    assert recorder.spans() == {"joint1": (-20.0, 10.0), "joint2": (-5.0, 30.0)}


def test_last_reflects_the_most_recent_update_only():
    recorder = SweepRecorder(JOINTS)
    recorder.update({"joint1": 10.0, "joint2": -5.0})
    recorder.update({"joint1": 3.0, "joint2": 3.0})
    assert recorder.last == {"joint1": 3.0, "joint2": 3.0}


def test_is_complete_requires_every_joint_with_a_real_nonzero_span():
    recorder = SweepRecorder(JOINTS)
    recorder.update({"joint1": 0.0})
    assert not recorder.is_complete()  # joint2 never seen, joint1 has zero span
    recorder.update({"joint1": 10.0, "joint2": -5.0})
    recorder.update({"joint2": 5.0})
    assert recorder.is_complete()


def test_reset_clears_min_max_but_not_last():
    recorder = SweepRecorder(JOINTS)
    recorder.update({"joint1": 10.0, "joint2": -5.0})
    recorder.reset()
    assert recorder.spans() == {}
    assert not recorder.is_complete()
    assert recorder.last == {"joint1": 10.0, "joint2": -5.0}


def test_update_with_extra_unknown_joint_names_is_ignored_for_completeness():
    recorder = SweepRecorder(JOINTS)
    recorder.update({"joint1": 10.0, "joint2": -5.0, "some_other_joint": 99.0})
    recorder.update({"joint1": -10.0, "joint2": 5.0, "some_other_joint": -99.0})
    assert set(recorder.spans()) == {"joint1", "joint2"}
    assert recorder.is_complete()
