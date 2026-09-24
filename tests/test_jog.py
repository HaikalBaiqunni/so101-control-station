"""Tests for the JAKA-style Jog panel and the engine behind it in MainWindow.

Headless (Qt's offscreen platform, no GL): the panel tests only need widgets;
the engine tests build a real MainWindow on the SO-101 profile - which loads no
twin - and give it a synthetic kinematic chain, then drive it with a fake clock
so the arithmetic (deg/s, mm/s, step sizes) can be asserted exactly rather than
"roughly, depending on how busy the CI machine was".
"""
from __future__ import annotations

import time as real_time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from synthetic_arm import ARM5, SO101_LIKE

from core.kinematics import KinematicChain
from ui import main_window as mw
from ui.jog_panel import JogPanel
from ui.joint_panel import JointPanel

TICK = 1 / 30

# MJCF joint ranges of the synthetic arm, in degrees - used as the "calibrated"
# ranges so the GUI-degrees <-> model-radians mapping is exactly 1:1 and the
# Cartesian numbers can be checked against plain geometry.
RANGES_DEG = {
    "shoulder_pan": (-110.0, 110.0), "shoulder_lift": (-100.0, 100.0), "elbow_flex": (-97.0, 97.0),
    "wrist_flex": (-95.0, 95.0), "wrist_roll": (-160.0, 160.0), "gripper": (0.0, 90.0),
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeClock:
    """Stands in for the `time` module inside ui.main_window."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(real_time, name)

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    # Settings (gui_settings.json) and logs/ are written relative to the cwd;
    # keep both away from the real checkout, and away from whichever robot
    # profile a developer last left persisted there.
    monkeypatch.chdir(tmp_path)
    clock = FakeClock()
    monkeypatch.setattr(mw, "time", clock)
    win = mw.MainWindow()
    win.clock = clock
    win.joint_deg_ranges = dict(RANGES_DEG)
    win.current_positions = dict.fromkeys(RANGES_DEG, 0.0)
    yield win
    win.robot_worker = None  # a test may have installed a FakeRobot; close() would try to retire it as a QThread
    win.close()


@pytest.fixture
def cartesian_window(window, tmp_path):
    path = tmp_path / "arm.xml"
    path.write_text(SO101_LIKE)
    window.kinematics = KinematicChain(str(path), ARM5)
    window.jog_panel.set_cartesian_available(True)
    return window


def tick(win, seconds):
    """Advance the fake clock and run the jog engine at its real tick rate."""
    for _ in range(round(seconds / TICK)):
        win.clock.advance(TICK)
        win._jog_tick()


class FakeRobot:
    def __init__(self):
        self.goals = []

    def request_goal(self, name, degrees):
        self.goals.append((name, degrees))


# --------------------------------------------------------------------------- panel
class TestJogPanel:
    def test_world_and_tool_start_disabled_until_a_model_is_available(self, qapp):
        panel = JogPanel()
        assert panel.mode() == "joint"
        assert not panel.mode_buttons["world"].isEnabled()
        assert not panel.mode_buttons["tool"].isEnabled()
        assert "Load a digital twin" in panel.hint_label.text()

    def test_enabling_cartesian_lets_the_modes_be_picked(self, qapp):
        panel = JogPanel()
        panel.set_cartesian_available(True)
        modes = []
        panel.mode_changed.connect(modes.append)
        panel.mode_buttons["tool"].click()
        assert panel.mode() == "tool"
        assert modes == ["tool"]
        assert panel.stack.currentIndex() == 1

    def test_losing_the_model_while_in_a_cartesian_mode_falls_back_to_joint(self, qapp):
        panel = JogPanel()
        panel.set_cartesian_available(True)
        panel.mode_buttons["world"].click()
        modes = []
        panel.mode_changed.connect(modes.append)
        panel.set_cartesian_available(False, "no twin")
        assert panel.mode() == "joint"
        assert modes == ["joint"]
        assert panel.stack.currentIndex() == 0

    def test_a_disabled_mode_cannot_be_selected(self, qapp):
        panel = JogPanel()
        panel.mode_buttons["world"].click()
        assert panel.mode() == "joint"

    def test_joint_buttons_emit_mode_name_and_direction(self, qapp):
        panel = JogPanel()
        events = []
        panel.jog_pressed.connect(lambda *a: events.append(("down", *a)))
        panel.jog_released.connect(lambda *a: events.append(("up", *a)))
        row = panel.joint_page.rows["elbow_flex"]
        row.plus_btn.pressed.emit()
        row.plus_btn.released.emit()
        row.minus_btn.pressed.emit()
        assert events == [
            ("down", "joint", "elbow_flex", 1),
            ("up", "joint", "elbow_flex", 1),
            ("down", "joint", "elbow_flex", -1),
        ]

    def test_cartesian_buttons_carry_the_current_frame(self, qapp):
        panel = JogPanel()
        panel.set_cartesian_available(True)
        panel.mode_buttons["tool"].click()
        events = []
        panel.jog_pressed.connect(lambda *a: events.append(a))
        panel.cartesian_page.rows["rz"].minus_btn.pressed.emit()
        assert events == [("tool", "rz", -1)]

    def test_speed_defaults_slow_and_step_reads_back(self, qapp):
        panel = JogPanel()
        assert panel.speed_fraction() == pytest.approx(0.30)
        assert panel.step_size() == 0.0
        panel.step_combo.setCurrentIndex(3)
        assert panel.step_size() == 5.0

    def test_reachability_marks_limited_axes(self, qapp):
        panel = JogPanel()
        panel.set_reach([1.0, 1.0, 1.0, 0.2, 1.0, 0.9])
        rows = panel.cartesian_page.rows
        assert rows["rx"].plus_btn.property("limited") is True
        assert rows["x"].plus_btn.property("limited") is False
        assert "20%" in rows["rx"].plus_btn.toolTip()

    def test_input_can_be_disabled_as_a_whole(self, qapp):
        panel = JogPanel()
        panel.set_input_enabled(False)
        assert not panel.joint_page.rows["gripper"].plus_btn.isEnabled()
        assert not panel.cartesian_page.rows["z"].minus_btn.isEnabled()
        panel.set_input_enabled(True)
        assert panel.joint_page.rows["gripper"].plus_btn.isEnabled()

    def test_pose_readout_is_in_millimetres_and_degrees(self, qapp):
        panel = JogPanel()
        panel.set_pose([0.35, 0.0, 0.08], (10.0, 20.0, 30.0))
        assert "350.0 mm" in panel.cartesian_page.rows["x"].readout.text()
        assert "80.0 mm" in panel.cartesian_page.rows["z"].readout.text()
        assert "30.0 deg" in panel.cartesian_page.rows["rz"].readout.text()


class TestJointRow:
    def test_typed_entry_only_commits_on_enter_not_per_keystroke(self, qapp):
        # Otherwise typing "120" would command 1, then 12, then 120.
        panel = JointPanel()  # held in a name: an unreferenced panel is destroyed with its spinboxes
        assert panel.rows["gripper"].spin.keyboardTracking() is False

    def test_live_feedback_never_becomes_a_goal(self, qapp):
        panel = JointPanel()
        goals = []
        panel.goal_changed.connect(lambda *a: goals.append(a))
        panel.update_feedback({"gripper": 12.5})
        assert panel.rows["gripper"].spin.value() == pytest.approx(12.5)
        assert goals == []

    def test_a_user_edit_does_become_a_goal(self, qapp):
        panel = JointPanel()
        goals = []
        panel.goal_changed.connect(lambda *a: goals.append(a))
        panel.rows["gripper"].spin.setValue(33.0)
        assert goals == [("gripper", 33.0)]

    def test_rebuild_replaces_the_rows_and_keeps_input_state(self, qapp):
        panel = JointPanel()
        panel.set_input_enabled(False)
        panel.rebuild(["joint1", "joint2"])
        assert list(panel.rows) == ["joint1", "joint2"]
        assert not panel.rows["joint1"].plus_btn.isEnabled()


# --------------------------------------------------------------------------- engine: joint jog
class TestJointJog:
    def test_a_held_button_moves_at_the_speed_setting(self, window):
        window.robot_worker = FakeRobot()
        window.jog_panel.speed_slider.setValue(40)  # 40% of 45 deg/s = 18 deg/s
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        tick(window, 1.0)
        assert window.current_positions["shoulder_pan"] == pytest.approx(18.0, abs=0.7)
        assert window.robot_worker.goals, "goals must reach the robot worker"

    def test_negative_direction_moves_the_other_way(self, window):
        window._on_jog_pressed("joint", "wrist_flex", -1)
        tick(window, 0.5)
        assert window.current_positions["wrist_flex"] < -5

    def test_release_stops_the_motion(self, window):
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        tick(window, 0.3)
        window._on_jog_released("joint", "shoulder_pan", 1)
        stopped_at = window.current_positions["shoulder_pan"]
        tick(window, 0.5)
        assert window.current_positions["shoulder_pan"] == stopped_at
        assert not window._jog_active

    def test_it_never_leaves_the_calibrated_range(self, window):
        window.jog_panel.speed_slider.setValue(100)
        window._on_jog_pressed("joint", "gripper", 1)
        tick(window, 5.0)
        assert window.current_positions["gripper"] == pytest.approx(90.0)

    def test_two_joints_can_be_held_at_once(self, window):
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        window._on_jog_pressed("joint", "elbow_flex", -1)
        tick(window, 0.5)
        assert window.current_positions["shoulder_pan"] > 3
        assert window.current_positions["elbow_flex"] < -3

    def test_a_step_moves_exactly_that_far_even_if_released_at_once(self, window):
        window.jog_panel.step_combo.setCurrentIndex(3)  # 5 deg
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        window._on_jog_released("joint", "shoulder_pan", 1)
        tick(window, 1.0)
        assert window.current_positions["shoulder_pan"] == pytest.approx(5.0, abs=0.05)
        assert not window._jog_active

    def test_jogging_starts_from_the_measured_pose(self, window):
        window.current_positions["shoulder_pan"] = 40.0
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        tick(window, 0.2)
        assert window.current_positions["shoulder_pan"] > 40.0


class TestJogLockouts:
    def test_only_manual_control_source_may_jog(self, window):
        window.control_source = "keyboard"
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        assert not window._jog_active

    def test_playback_blocks_jogging(self, window):
        window._playback_index = 0
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        assert not window._jog_active

    def test_leaving_manual_drops_a_jog_in_progress_and_disables_the_buttons(self, window):
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        window.control_source = "gamepad"
        window._apply_control_source_lock()
        assert not window._jog_active
        assert not window.joint_panel.rows["shoulder_pan"].plus_btn.isEnabled()
        window.control_source = "manual"
        window._apply_control_source_lock()
        assert window.joint_panel.rows["shoulder_pan"].plus_btn.isEnabled()

    def test_starting_playback_drops_a_jog(self, window):
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        window._jog_release_all()
        assert not window._jog_active

    def test_switching_mode_stops_the_motion(self, cartesian_window):
        win = cartesian_window
        win._on_jog_pressed("joint", "shoulder_pan", 1)
        win.jog_panel.mode_buttons["world"].click()
        assert not win._jog_active

    def test_cartesian_jog_is_ignored_without_a_kinematic_model(self, window):
        window.kinematics = None
        window._on_jog_pressed("world", "z", 1)
        assert not window._jog_active

    def test_a_stalled_gui_does_not_turn_into_one_huge_lunge(self, window):
        window._on_jog_pressed("joint", "shoulder_pan", 1)
        window.clock.advance(30.0)  # the event loop was blocked for 30 s
        window._jog_tick()
        assert window.current_positions["shoulder_pan"] < 3.0


# --------------------------------------------------------------------------- engine: Cartesian jog
def _tcp(win):
    fractions = win._positions_to_fractions()
    chain = win.kinematics
    chain.set_q(chain.fractions_to_q({n: fractions[n] for n in chain.joint_names if n in fractions}))
    return chain.tcp_pose()


BENT = {"shoulder_pan": 0.0, "shoulder_lift": 34.0, "elbow_flex": -57.0, "wrist_flex": 46.0, "wrist_roll": 0.0}


class TestCartesianJog:
    def test_world_z_raises_the_tool_at_the_configured_speed(self, cartesian_window):
        win = cartesian_window
        win.current_positions.update(BENT)
        win.jog_panel.mode_buttons["world"].click()
        p0, _ = _tcp(win)
        win._on_jog_pressed("world", "z", 1)
        tick(win, 1.0)
        p1, _ = _tcp(win)
        rise_mm = (p1[2] - p0[2]) * 1000
        assert rise_mm == pytest.approx(24.0, abs=2.0)  # 30% of 80 mm/s
        assert abs(p1[0] - p0[0]) * 1000 < 4 and abs(p1[1] - p0[1]) * 1000 < 4

    def test_tool_frame_moves_along_the_tools_own_axis(self, cartesian_window):
        win = cartesian_window
        win.current_positions.update(BENT)
        win.jog_panel.mode_buttons["tool"].click()
        p0, rot0 = _tcp(win)
        win._on_jog_pressed("tool", "x", 1)
        tick(win, 1.0)
        p1, _ = _tcp(win)
        move = p1 - p0
        cosine = float(move @ rot0[:, 0] / np.linalg.norm(move))
        assert cosine > 0.98
        assert np.linalg.norm(move) * 1000 == pytest.approx(24.0, abs=2.5)

    def test_world_and_tool_disagree_when_the_wrist_is_pitched(self, cartesian_window):
        win = cartesian_window
        results = {}
        for frame in ("world", "tool"):
            win._jog_release_all()
            win.current_positions.update(BENT)
            win.jog_panel.mode_buttons[frame].click()
            p0, _ = _tcp(win)
            win._on_jog_pressed(frame, "x", 1)
            tick(win, 0.6)
            p1, _ = _tcp(win)
            results[frame] = p1 - p0
        assert np.linalg.norm(results["world"] - results["tool"]) * 1000 > 3

    def test_a_cartesian_step_travels_the_requested_distance(self, cartesian_window):
        win = cartesian_window
        win.current_positions.update(BENT)
        win.jog_panel.mode_buttons["world"].click()
        win.jog_panel.step_combo.setCurrentIndex(3)  # 5 mm
        p0, _ = _tcp(win)
        win._on_jog_pressed("world", "z", 1)
        win._on_jog_released("world", "z", 1)
        tick(win, 1.0)
        p1, _ = _tcp(win)
        assert (p1[2] - p0[2]) * 1000 == pytest.approx(5.0, abs=0.4)

    def test_the_cartesian_readout_and_reach_are_published(self, cartesian_window):
        win = cartesian_window
        win.current_positions.update(BENT)
        win.jog_panel.mode_buttons["world"].click()
        win._update_cartesian_readout()
        assert "mm" in win.jog_panel.cartesian_page.rows["x"].readout.text()
        limited = [
            axis for axis, row in win.jog_panel.cartesian_page.rows.items()
            if row.plus_btn.property("limited")
        ]
        assert limited, "a 5-joint arm must show at least one limited axis"

    def test_jogging_never_moves_the_gripper(self, cartesian_window):
        win = cartesian_window
        win.current_positions.update(BENT)
        win.current_positions["gripper"] = 30.0
        win.jog_panel.mode_buttons["world"].click()
        win._on_jog_pressed("world", "z", 1)
        tick(win, 1.0)
        assert win.current_positions["gripper"] == 30.0


# --------------------------------------------------------------------------- ghost + axes overlay
class TestGhost:
    def test_nothing_to_show_means_no_ghost(self, window):
        assert window._compute_ghost() is None

    def test_playback_target_away_from_the_arm_is_shown_as_fractions(self, window):
        window._playback_index = 0
        window._playback_target_positions = {"shoulder_pan": 55.0}
        ghost = window._compute_ghost()
        assert ghost == {"shoulder_pan": pytest.approx((55.0 + 110.0) / 220.0)}

    def test_a_target_the_arm_is_already_at_is_not_drawn(self, window):
        window._playback_index = 0
        window._playback_target_positions = {"shoulder_pan": 1.0}  # within 2 degrees
        assert window._compute_ghost() is None

    def test_the_toggle_turns_it_off(self, window):
        window._playback_index = 0
        window._playback_target_positions = {"shoulder_pan": 55.0}
        window.twin_panel.ghost_check.setChecked(False)
        assert window._compute_ghost() is None

    def test_a_jog_target_is_shown_while_the_real_arm_catches_up(self, window):
        window.robot_worker = FakeRobot()
        window._jog_target = {"shoulder_pan": 30.0}
        window._jog_ghost_until = window.clock.now + 2.0
        assert window._compute_ghost() is not None
        window.clock.advance(3.0)
        assert window._compute_ghost() is None, "the jog ghost must not linger forever"

    def test_a_jog_ghost_needs_real_hardware_to_be_meaningful(self, window):
        window._jog_target = {"shoulder_pan": 30.0}
        window._jog_ghost_until = window.clock.now + 2.0
        assert window.robot_worker is None
        assert window._compute_ghost() is None  # without a follower, live == target

    def test_playback_outranks_a_jog_target_outranks_a_selected_waypoint(self, window):
        window.robot_worker = FakeRobot()
        window.waypoints = [{"label": "w", "positions": {"shoulder_pan": -50.0}, "dwell_ms": 0}]
        window.teaching_panel.set_waypoints(["w"])
        window.teaching_panel.select_row(0)
        assert window._compute_ghost()["shoulder_pan"] == pytest.approx((-50.0 + 110.0) / 220.0)

        window._jog_target = {"shoulder_pan": 30.0}
        window._jog_ghost_until = window.clock.now + 2.0
        assert window._compute_ghost()["shoulder_pan"] == pytest.approx(140.0 / 220.0)

        window._playback_index = 0
        window._playback_target_positions = {"shoulder_pan": 80.0}
        assert window._compute_ghost()["shoulder_pan"] == pytest.approx(190.0 / 220.0)


class TestFrameOverlay:
    def test_no_overlay_without_a_kinematic_model(self, window):
        assert window._frame_overlay() is None

    def test_overlay_follows_the_jog_frame(self, cartesian_window):
        win = cartesian_window
        assert win._frame_overlay() == "world"  # joint mode still shows the fixed base frame
        win.jog_panel.mode_buttons["tool"].click()
        assert win._frame_overlay() == "tool"

    def test_the_axes_toggle_hides_it(self, cartesian_window):
        cartesian_window.twin_panel.axes_check.setChecked(False)
        assert cartesian_window._frame_overlay() is None


def test_telemetry_lives_in_its_own_tab(window):
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["1 - Setup", "2 - Calibration", "3 - Control", "4 - Telemetry"]
    assert window.tabs.widget(3) is window.telemetry_panel
