"""Gripper gauge: the contact / reading state machine against a simulated MIT gripper."""
from __future__ import annotations

import math

import pytest

from core.gripper_gauge import (
    DONE,
    FAILED,
    GaugeCalibration,
    GaugeParams,
    GripperGauge,
    describe_measurement,
)
from core.gripper_range import GripperRangeFinder


class SimGripper:
    """A jaw driven the way MIT mode drives it: torque = kp * (setpoint - q) - kd * speed, with
    friction (stiction included), inertia, a hard end stop and optionally an object at `contact_deg`."""

    def __init__(self, q_deg, sign=1, contact_deg=None, kp=8.0, kd=0.5, coulomb=0.15, end_deg=100.0, obstacle_k=60.0,
                 other_end_deg=None):
        self.q = math.radians(q_deg)
        self.v = 0.0
        self.sign, self.kp, self.kd, self.coulomb = sign, kp, kd, coulomb
        self.contact = None if contact_deg is None else math.radians(contact_deg)
        self.end = math.radians(end_deg)
        self.other_end = None if other_end_deg is None else math.radians(other_end_deg)   # a stop on the far side too
        self.k = obstacle_k
        self.max_torque = 0.0

    @property
    def deg(self):
        return math.degrees(self.q)

    def advance(self, setpoint_deg, seconds, dt=0.001):
        sp = math.radians(setpoint_deg)
        for _ in range(int(round(seconds / dt))):
            tau = self.kp * (sp - self.q) - self.kd * self.v
            self.max_torque = max(self.max_torque, abs(self.kp * (sp - self.q)))
            if self.contact is not None and self.sign * (self.q - self.contact) > 0:
                tau -= self.k * (self.q - self.contact)
            if self.sign * (self.q - self.end) > 0:
                tau -= 500.0 * (self.q - self.end)
            if self.other_end is not None and self.sign * (self.q - self.other_end) < 0:
                tau -= 500.0 * (self.q - self.other_end)
            if abs(self.v) < 1e-4 and abs(tau) <= self.coulomb:
                self.v = 0.0
                continue
            tau -= self.coulomb * math.copysign(1.0, self.v if self.v else tau) + 0.02 * self.v
            self.v += tau / 0.002 * dt
            self.q += self.v * dt


def run(sim, params, start=None, ticks=4000, tick=0.05, feedback=lambda i, q: q):
    gauge = GripperGauge(params)
    now = 0.0
    goal = gauge.start(now, sim.deg if start is None else start)
    goals = [goal]
    for i in range(ticks):
        sim.advance(goal, tick)
        now += tick
        new = gauge.update(now, feedback(i, sim.deg))
        if new is not None:
            goal = new
            goals.append(goal)
        if gauge.finished:
            break
    return gauge, goals


@pytest.mark.parametrize("sign", [1, -1])
def test_measures_the_contact_angle_and_returns(sign):
    start, contact = (10.0, 52.0) if sign == 1 else (90.0, 48.0)
    sim = SimGripper(start, sign, contact_deg=contact, end_deg=100.0 if sign == 1 else 0.0)
    params = GaugeParams(close_sign=sign, limit_deg=100.0 if sign == 1 else 0.0)
    gauge, goals = run(sim, params)
    assert gauge.state == DONE and not gauge.hit_limit
    result = gauge.result
    assert len(result.angles) == params.repeats
    # the jaw gives a little under the preload torque, always on the closing side of the object
    assert 0.0 <= sign * (result.mean - contact) < 1.5
    assert result.std < 0.3
    assert abs(sim.deg - start) < 2.5          # opened back to where it started


def test_force_is_capped_by_the_preload():
    sim = SimGripper(10.0, 1, contact_deg=50.0)
    params = GaugeParams(limit_deg=100.0, preload_deg=3.0)
    gauge, _ = run(sim, params)
    assert gauge.state == DONE
    # torque = kp * (setpoint - position): never beyond kp * (preload + a little tracking noise)
    assert sim.max_torque <= 8.0 * math.radians(3.0 + 0.6)


def test_goal_never_passes_the_end_of_travel():
    sim = SimGripper(10.0, 1, contact_deg=None, end_deg=100.0)
    gauge, goals = run(sim, GaugeParams(limit_deg=60.0))
    assert max(goals) <= 60.0 + 1e-9
    assert gauge.state == DONE and gauge.hit_limit      # nothing between the jaws


def test_already_touching_is_refused_not_pressed():
    sim = SimGripper(50.0, 1, contact_deg=50.0)
    gauge, _ = run(sim, GaugeParams(limit_deg=100.0))
    assert gauge.state == FAILED and "did not move" in gauge.message


def test_lost_feedback_stops_the_run():
    sim = SimGripper(10.0, 1, contact_deg=None)
    gauge, _ = run(sim, GaugeParams(limit_deg=100.0), feedback=lambda i, q: None if i > 5 else q)
    assert gauge.state == FAILED and "feedback" in gauge.message
    assert gauge.update(99.0, 10.0) is None


def test_runs_are_independent_of_the_object_position():
    for contact in (20.0, 45.0, 80.0):
        sim = SimGripper(5.0, 1, contact_deg=contact)
        gauge, _ = run(sim, GaugeParams(limit_deg=100.0, repeats=2))
        assert gauge.state == DONE
        assert 0.0 <= gauge.result.mean - contact < 1.5


def test_a_sticky_jaw_is_caught_by_the_passes_disagreeing():
    # preload torque barely above the breakaway torque: later passes stop short of the object
    sim = SimGripper(10.0, 1, contact_deg=50.0, kp=3.0, coulomb=0.05)
    gauge, _ = run(sim, GaugeParams(limit_deg=100.0, speed_deg_s=25.0))
    assert gauge.state == FAILED and "disagree" in gauge.message and gauge.result is None


def test_params_are_validated():
    for bad in ({"close_sign": 0}, {"speed_deg_s": 100}, {"preload_deg": 50}, {"repeats": 0},
                {"detect_lag_deg": 9.0, "preload_deg": 3.0}):
        with pytest.raises(ValueError):
            GaugeParams(**bad)


# ---------------------------------------------------------------- calibration
def test_calibration_fits_a_line():
    cal = GaugeCalibration(1, [(100.0, 0.0), (60.0, 20.0), (20.0, 40.0)])   # width = 50 - 0.5 * angle
    slope, intercept = cal.fit()
    assert slope == pytest.approx(-0.5) and intercept == pytest.approx(50.0)
    assert cal.width_mm(40.0) == pytest.approx(30.0)
    assert cal.max_residual_mm() == pytest.approx(0.0, abs=1e-9)
    assert cal.direction_ok()
    assert not GaugeCalibration(-1, cal.points).direction_ok()


def test_calibration_needs_two_distinct_references_and_replaces_same_size():
    cal = GaugeCalibration(1)
    assert not cal.usable and cal.fit() is None and cal.width_mm(10.0) is None
    cal.set_point(100.0, 0.0)
    assert not cal.usable
    cal.set_point(60.0, 20.0)
    cal.set_point(61.0, 20.0)                                # same size measured again: replaced
    assert len(cal.points) == 2 and cal.usable


def test_calibration_roundtrips_and_survives_garbage():
    cal = GaugeCalibration(-1, [(1.0, 0.0), (2.0, 5.0)])
    again = GaugeCalibration.from_dict(cal.to_dict())
    assert again.close_sign == -1 and again.points == cal.points
    assert GaugeCalibration.from_dict({"points": "nope"}).points == []
    assert GaugeCalibration.from_dict(None).points == []


def test_measurement_warnings():
    cal = GaugeCalibration(1, [(100.0, 0.0), (60.0, 20.0), (20.0, 40.0)])
    from core.gripper_gauge import GaugeResult
    width, spread, warnings = describe_measurement(cal, GaugeResult([40.0, 40.2]))
    assert width == pytest.approx(30.0, abs=0.1) and spread == pytest.approx(0.5 * 0.1414, abs=0.01) and not warnings
    _, _, warnings = describe_measurement(cal, GaugeResult([200.0]))
    assert any("extrapolation" in w for w in warnings) and any("negative" in w for w in warnings)
    noisy = GaugeCalibration(1, [(100.0, 0.0), (60.0, 25.0), (20.0, 40.0)])
    _, _, warnings = describe_measurement(noisy, GaugeResult([60.0]))
    assert any("straight line" in w for w in warnings)
    with pytest.raises(ValueError):
        describe_measurement(GaugeCalibration(), GaugeResult([1.0]))


# ---------------------------------------------------------------- main window wiring
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.dm_can import Control_Type  # noqa: E402
from ui import main_window as mw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    w = mw.MainWindow()
    yield w
    w.robot_worker = None
    w.close()


class FakeDmFollower:
    """Stands in for DmRobotWorker: records the goals the gauge sends."""
    port = "COMX"
    bus = None

    def __init__(self, mode=Control_Type.MIT):
        self.control_mode = mode
        self.last_goals, self.goals, self.torque_calls = {}, [], []

    def request_goal(self, name, degrees):
        self.last_goals[name] = degrees
        if name == "finger_left":
            self.goals.append(degrees)

    def request_torque(self, enabled, name=None):
        self.torque_calls.append(enabled)

    def request_range(self, name, lo, hi):
        self.ranges = getattr(self, "ranges", []) + [(name, lo, hi)]


def arm(win, mode=Control_Type.MIT, torque=True, kp=8.0):
    """A connected B601-DM follower with a calibrated gripper range and the MIT kp set."""
    win.robot_combo.setCurrentIndex(win.robot_combo.findData("rebot_b601_dm"))
    win._save_setting("dm_follower_calibrated_ranges", {"finger_left": [0.0, 100.0]})
    win.robot_worker = FakeDmFollower(mode)
    win._follower_connected = True
    win.follower_torque_enabled = torque
    win.joint_deg_ranges["finger_left"] = (0.0, 100.0)
    win.dm_tune_panel.mit_gain_spins["finger_left"][0].setValue(kp)
    win._refresh_gauge_ui()
    return win.robot_worker


def drive(win, sim, seconds, start=100.0, tick=0.05):
    """Run the gauge loop: the simulated jaw follows the last goal and its position arrives each tick."""
    now = start
    for _ in range(int(seconds / tick)):
        if win._gauge is None:
            break
        goal = win.robot_worker.last_goals.get("finger_left", sim.deg)
        sim.advance(goal, tick)
        now += tick
        win.current_positions["finger_left"] = sim.deg
        win._last_follower_update = now
        win._gauge_step(now)
    return now


def start(win, sim, kind="zero", size=0.0):
    win.current_positions["finger_left"] = sim.deg
    win._last_follower_update = mw.time.monotonic()
    win._on_gauge_measure(kind, size)


def status(win):
    return win.gripper_gauge_panel.status_label.text().lower()


def test_gauge_button_only_for_the_damiao_arm(win):
    win.show()
    win.robot_combo.setCurrentIndex(win.robot_combo.findData("so101"))
    assert not win.top_bar.nav_buttons["gauge"].isVisible()
    win.robot_combo.setCurrentIndex(win.robot_combo.findData("rebot_b601_dm"))
    assert win.top_bar.nav_buttons["gauge"].isVisible()
    win.top_bar.nav_buttons["gauge"].click()
    assert win.drawer.currentWidget() is win.gripper_gauge_panel
    assert any(a.title == "Open Gripper gauge" for a in win._palette_actions())


@pytest.mark.parametrize("kwargs, expect", [
    ({"mode": Control_Type.POS_VEL}, "mit"),
    ({"torque": False}, "torque"),
    ({"kp": 0.0}, "push limit"),
])
def test_it_refuses_to_start_unless_safe(win, kwargs, expect):
    sim = SimGripper(60.0, 1, contact_deg=20.0)
    arm(win, **kwargs)
    start(win, sim)
    assert win._gauge is None and expect in status(win)
    assert win.robot_worker.goals == []          # nothing was commanded


def test_it_refuses_without_a_calibrated_gripper_range(win):
    sim = SimGripper(60.0, 1)
    arm(win)
    win._save_setting("dm_follower_calibrated_ranges", {})
    start(win, sim)
    assert win._gauge is None and "calibrate the gripper range" in status(win)


def test_it_refuses_unless_the_control_source_is_manual(win):
    sim = SimGripper(60.0, 1)
    arm(win)
    win.control_source = "leader"
    start(win, sim)
    assert win._gauge is None and "manual" in status(win)


def test_push_limit_is_enforced(win):
    sim = SimGripper(60.0, 1)
    arm(win, kp=40.0)                            # 40 x 3 deg = 2.1 N*m, above the 1.5 N*m ceiling
    assert "above" in win.gripper_gauge_panel.force_label.text()
    start(win, sim)
    assert win._gauge is None and "push limit" in status(win)


def test_calibrate_then_measure_an_object(win):
    # jaws meet at 80 deg (0 mm); a 10 mm block stops them at 64 and a 30 mm block at 32
    arm(win)
    win.gripper_gauge_panel.size_spin.setValue(10.0)
    for kind, contact, size, begin in (("zero", 80.0, 0.0, 20.0), ("reference", 64.0, 10.0, 20.0),
                                       ("reference", 32.0, 30.0, 5.0)):
        sim = SimGripper(begin, 1, contact_deg=contact)
        start(win, sim, kind, size)
        assert win._gauge is not None and not win.jog_panel.isEnabled()      # manual controls are locked
        drive(win, sim, 120)
        assert win._gauge is None and win.jog_panel.isEnabled()
    assert len(win._gauge_cal.points) == 3 and win._gauge_cal.usable
    assert win._gauge_cal.max_residual_mm() < 1.0

    sim = SimGripper(10.0, 1, contact_deg=48.0)   # an object that stops the jaw near 48 deg
    start(win, sim, "object")
    drive(win, sim, 120)
    rows = win.gripper_gauge_panel.history_rows()
    assert len(rows) == 1 and 18.0 < rows[0]["width_mm"] < 22.0
    assert win.gripper_gauge_panel.result_label.text().endswith("mm")
    saved = win._load_settings()["gripper_gauge"]      # references and settings survive a restart
    assert len(saved["points"]) == 3 and saved["repeats"] == 3


def test_object_measurement_needs_calibration_first(win):
    sim = SimGripper(10.0, 1, contact_deg=48.0)
    arm(win)
    start(win, sim, "object")
    assert win._gauge is None and "calibrate first" in status(win)


def test_nothing_between_the_jaws_is_not_recorded_as_a_measurement(win):
    sim = SimGripper(20.0, 1, contact_deg=None, end_deg=100.0)
    arm(win)
    win._gauge_cal.set_point(80.0, 0.0)
    win._gauge_cal.set_point(64.0, 10.0)
    start(win, sim, "object")
    drive(win, sim, 120)
    assert win._gauge is None and win.gripper_gauge_panel.history_rows() == []
    assert "nothing between" in status(win)


@pytest.mark.parametrize("how", ["stop", "torque", "disconnect", "cancel", "source"])
def test_every_way_out_stops_the_gauge(win, how):
    sim = SimGripper(10.0, 1, contact_deg=60.0)
    arm(win)
    start(win, sim)
    drive(win, sim, 1.0)
    assert win._gauge is not None
    before = len(win.robot_worker.goals)
    if how == "stop":
        win.top_bar.stop_btn.click()
    elif how == "torque":
        win._on_torque(False)
    elif how == "disconnect":
        win._on_connection_changed(False)
    elif how == "cancel":
        win.gripper_gauge_panel.cancel_btn.click()
    else:
        win._on_control_source_changed("gamepad")
    assert win._gauge is None and win.jog_panel.isEnabled() and not win.gauge_timer.isActive()
    assert not win.gripper_gauge_panel.is_running()
    after = win.robot_worker.goals[before:]
    if how == "cancel":
        assert after == [pytest.approx(10.0, abs=0.5)]   # the jaws open back to where they started
    else:
        assert after == []                               # Stop / torque off / disconnect never command the gripper


def test_stale_position_feedback_aborts(win):
    sim = SimGripper(10.0, 1, contact_deg=60.0)
    arm(win)
    start(win, sim)
    now = drive(win, sim, 0.5)
    win._gauge_step(now + 2.0)                   # no position update for 2 s
    assert win._gauge is None and "feedback" in status(win)


def test_other_goals_for_the_gripper_are_dropped_while_measuring(win):
    sim = SimGripper(10.0, 1, contact_deg=60.0)
    arm(win)
    start(win, sim)
    n = len(win.robot_worker.goals)
    win._request_goal("finger_left", 99.0)
    win._request_goal("joint1", 5.0)
    assert len(win.robot_worker.goals) == n and win.robot_worker.last_goals["joint1"] == 5.0


def test_changing_the_closing_direction_clears_the_references(win):
    arm(win)
    win._gauge_cal.set_point(80.0, 0.0)
    win._gauge_cal.set_point(64.0, 10.0)
    win.gripper_gauge_panel.direction_combo.setCurrentIndex(1)
    assert win._gauge_cal.points == [] and win._gauge_cal.close_sign == -1
    assert win._load_settings()["gripper_gauge"]["close_sign"] == -1


def test_motor_fault_is_shown_on_the_torque_chip_and_clears(win):
    arm(win)
    win._update_status_chips()
    assert win.top_bar.chips["torque"].text() == "Torque ON"
    win._on_motor_status({"joint1": 1, "finger_left": 0xE})
    assert win.top_bar.chips["torque"].text() == "! finger_left: overload"
    assert win.top_bar.chips["torque"].property("state") == "warn"
    assert "finger_left: overload" in win.statusBar().currentMessage()
    win._on_motor_status({"joint1": 1, "finger_left": 1})
    assert win.top_bar.chips["torque"].text() == "Torque ON"


def test_a_motor_that_stays_disabled_while_torque_is_on_is_reported(win):
    arm(win)
    win._on_motor_status({"finger_left": 0})
    assert win._motor_alert == ""                       # one reading could be the instant before the enable lands
    win._on_motor_status({"finger_left": 0})
    assert win._motor_alert == "finger_left: motor disabled"
    win._on_motor_status({"finger_left": 1})
    assert win._motor_alert == ""


def test_a_disabled_motor_is_normal_while_torque_is_off(win):
    arm(win, torque=False)
    for _ in range(3):
        win._on_motor_status({"finger_left": 0})
    assert win._motor_alert == ""


def test_disconnecting_forgets_the_alert(win):
    arm(win)
    win._on_motor_status({"finger_left": 0xE})
    assert win._motor_alert
    win._on_connection_changed(False)
    assert win._motor_alert == ""


# ---------------------------------------------------------------- automatic range finder
def run_finder(sim, finder=None, start=None, ticks=6000, tick=0.05):
    finder = finder or GripperRangeFinder()
    now = 0.0
    goal = finder.start(now, sim.deg if start is None else start)
    goals = [goal]
    for _ in range(ticks):
        sim.advance(goal, tick)
        now += tick
        new = finder.update(now, sim.deg)
        if new is not None:
            goal = new
            goals.append(goal)
        if finder.finished:
            break
    return finder, goals


def test_finder_finds_both_ends_and_returns():
    sim = SimGripper(30.0, 1, end_deg=80.0, other_end_deg=-20.0)
    finder, goals = run_finder(sim)
    assert finder.state == DONE
    low, high = finder.result
    assert 78.0 <= high <= 81.0 and -21.0 <= low <= -18.0       # a little inside each stop is the jaw's give
    assert abs(sim.deg - 30.0) < 3.0                              # back where it started
    assert sim.max_torque <= 8.0 * math.radians(3.0 + 0.8)        # the force cap held at both stops


def test_finder_works_whichever_end_it_starts_at():
    for start in (80.0, -20.0):
        sim = SimGripper(start, 1, end_deg=80.0, other_end_deg=-20.0)
        finder, _ = run_finder(sim)
        assert finder.state == DONE, finder.message
        low, high = finder.result
        assert 78.0 <= high <= 81.0 and -21.0 <= low <= -18.0


def test_finder_goals_stay_within_the_travel_cap():
    sim = SimGripper(30.0, 1, end_deg=1000.0, other_end_deg=-1000.0)
    finder, goals = run_finder(sim, GripperRangeFinder(max_travel_deg=60.0))
    assert finder.state == FAILED and "no upper end" in finder.message
    assert max(goals) <= 90.0 + 1e-9 and min(goals) >= -30.0 - 1e-9


def test_finder_refuses_a_jaw_that_barely_moves():
    sim = SimGripper(30.0, 1, end_deg=34.0, other_end_deg=26.0)   # blocked 8 deg apart
    finder, _ = run_finder(sim)
    assert finder.state == FAILED and "only moved" in finder.message


def test_finder_stops_on_lost_feedback():
    finder = GripperRangeFinder()
    finder.start(0.0, 10.0)
    assert finder.update(0.1, None) is None and finder.state == FAILED and "feedback" in finder.message
    assert finder.update(0.2, 10.0) is None


def drive_range(win, sim, seconds, tick=0.05):
    return drive(win, sim, seconds, tick=tick)


def test_auto_range_saves_and_applies_the_range(win):
    sim = SimGripper(30.0, 1, end_deg=80.0, other_end_deg=-20.0)
    worker = arm(win)
    win.current_positions["finger_left"] = sim.deg
    win._last_follower_update = mw.time.monotonic()
    win.dm_calibration_page.gripper_auto_btn.click()
    assert isinstance(win._gauge, GripperRangeFinder) and not win.jog_panel.isEnabled()
    assert worker.ranges[0][0] == "finger_left" and worker.ranges[0][1] < -20.0 and worker.ranges[0][2] > 80.0   # widened
    assert win.dm_calibration_page.gripper_auto_btn.text().startswith("Cancel")
    drive_range(win, sim, 200)
    assert win._gauge is None and win.jog_panel.isEnabled()
    low, high = win._load_settings()["dm_follower_calibrated_ranges"]["finger_left"]
    assert 78.0 <= high <= 81.0 and -21.0 <= low <= -18.0
    assert win.joint_deg_ranges["finger_left"] == (low, high)
    name, lo, hi = worker.ranges[-1]                              # the clamp now matches what was found
    assert lo <= low + 1e-6 and hi >= high - 1e-6 and hi - lo < 105.0
    assert "saved" in win.statusBar().currentMessage().lower()
    assert win.dm_calibration_page.gripper_auto_btn.text() == "Auto-measure gripper range"


def test_auto_range_refuses_unless_safe(win):
    sim = SimGripper(30.0, 1)
    arm(win, torque=False)
    win.current_positions["finger_left"] = sim.deg
    win._last_follower_update = mw.time.monotonic()
    win.dm_calibration_page.gripper_auto_btn.click()
    assert win._gauge is None and "torque" in win.statusBar().currentMessage().lower()
    arm(win, mode=Control_Type.POS_VEL)
    win.current_positions["finger_left"] = sim.deg
    win._last_follower_update = mw.time.monotonic()
    win.dm_calibration_page.gripper_auto_btn.click()
    assert win._gauge is None and "MIT" in win.statusBar().currentMessage()


def test_auto_range_needs_no_saved_range(win):
    sim = SimGripper(30.0, 1, end_deg=80.0, other_end_deg=-20.0)
    arm(win)
    win._save_setting("dm_follower_calibrated_ranges", {})
    win.current_positions["finger_left"] = sim.deg
    win._last_follower_update = mw.time.monotonic()
    win.dm_calibration_page.gripper_auto_btn.click()
    assert isinstance(win._gauge, GripperRangeFinder)
    win._gauge_abort("test", release=False)


@pytest.mark.parametrize("how", ["stop", "torque", "cancel", "disconnect"])
def test_every_way_out_stops_the_range_search_and_keeps_the_old_range(win, how):
    sim = SimGripper(30.0, 1, end_deg=80.0, other_end_deg=-20.0)
    worker = arm(win)
    win.current_positions["finger_left"] = sim.deg
    win._last_follower_update = mw.time.monotonic()
    win.dm_calibration_page.gripper_auto_btn.click()
    drive_range(win, sim, 1.0)
    assert win._gauge is not None
    before = len(worker.goals)
    if how == "stop":
        win.top_bar.stop_btn.click()
    elif how == "torque":
        win._on_torque(False)
    elif how == "cancel":
        win.dm_calibration_page.gripper_auto_btn.click()
    else:
        win._on_connection_changed(False)
    assert win._gauge is None and win.jog_panel.isEnabled() and not win.gauge_timer.isActive()
    assert win._load_settings()["dm_follower_calibrated_ranges"]["finger_left"] == [0.0, 100.0]   # untouched
    assert win.dm_calibration_page.gripper_auto_btn.text() == "Auto-measure gripper range"
    after = worker.goals[before:]
    if how == "cancel":
        assert after == [pytest.approx(sim.deg, abs=3.0)]     # relaxes where it is, never goes anywhere
    else:
        assert after == []
    if how != "disconnect":
        _, lo, hi = worker.ranges[-1]                         # the clamp is back to the old range (or just the jaw's spot)
        assert (lo, hi) == (0.0, 100.0)


def test_finder_does_not_mistake_friction_for_an_end_of_travel():
    # stiction 0.7 N*m beats the gentle 3 deg push (8 x 0.052 = 0.42): the jaw sits still far from any stop
    gentle, _ = run_finder(SimGripper(30.0, 1, end_deg=80.0, other_end_deg=-20.0, coulomb=0.7))
    assert gentle.state == FAILED and "only moved" in gentle.message
    sim = SimGripper(30.0, 1, end_deg=80.0, other_end_deg=-20.0, coulomb=0.7)
    finder, _ = run_finder(sim, GripperRangeFinder(max_preload_deg=8.6))   # may push up to 8 x 0.15 = 1.2 N*m
    assert finder.state == DONE, finder.message
    low, high = finder.result
    assert 76.0 <= high <= 81.0 and -21.0 <= low <= -16.0
    assert sim.max_torque <= 8.0 * math.radians(8.6 + 0.8)       # never more than the ceiling


def test_finder_still_stops_at_a_real_stop_with_the_escalation_on():
    sim = SimGripper(30.0, 1, end_deg=80.0, other_end_deg=-20.0)
    finder, _ = run_finder(sim, GripperRangeFinder(max_preload_deg=8.6))
    assert finder.state == DONE
    low, high = finder.result
    assert 78.0 <= high <= 81.0 and -21.0 <= low <= -18.0


def test_the_connection_card_has_the_auto_button(win):
    win.show()
    win.robot_combo.setCurrentIndex(win.robot_combo.findData("so101"))
    assert not win.connection_panel.calibrate_gripper_auto_btn.isVisible()
    win.robot_combo.setCurrentIndex(win.robot_combo.findData("rebot_b601_dm"))
    assert win.connection_panel.calibrate_gripper_auto_btn.isVisible()
    sim = SimGripper(30.0, 1, end_deg=80.0, other_end_deg=-20.0)
    arm(win)
    win.current_positions["finger_left"] = sim.deg
    win._last_follower_update = mw.time.monotonic()
    win.connection_panel.calibrate_gripper_auto_btn.click()
    assert isinstance(win._gauge, GripperRangeFinder)
    assert win.connection_panel.calibrate_gripper_auto_btn.text().startswith("Cancel")
    win.connection_panel.calibrate_gripper_auto_btn.click()
    assert win._gauge is None
    assert win.connection_panel.calibrate_gripper_auto_btn.text() == "Auto-calibrate gripper range"


def test_gauge_needs_the_breakaway_push_when_friction_beats_the_gentle_one():
    # the user's real failure: "the jaw did not move" at 1 deg/s, because 0.42 N*m cannot beat 0.7 N*m of friction
    gentle, _ = run(SimGripper(10.0, 1, contact_deg=60.0, coulomb=0.7), GaugeParams(limit_deg=100.0, speed_deg_s=1.0, repeats=1),
                    ticks=1500)
    assert gentle.state == FAILED and "did not move" in gentle.message
    sim = SimGripper(10.0, 1, contact_deg=60.0, coulomb=0.7)
    gauge, _ = run(sim, GaugeParams(limit_deg=100.0, speed_deg_s=4.0, repeats=2, max_preload_deg=8.6), ticks=6000)
    assert gauge.state == DONE, gauge.message
    assert 0.0 <= gauge.result.mean - 60.0 < 4.0
    assert sim.max_torque <= 8.0 * math.radians(8.6 + 1.0)


def test_finder_keeps_the_push_that_worked_so_the_motion_is_continuous():
    sim = SimGripper(30.0, 1, end_deg=330.0, other_end_deg=-20.0, coulomb=0.7)
    finder = GripperRangeFinder(speed_deg_s=25.0, max_travel_deg=400.0, max_preload_deg=8.6)
    now, goal, moving, n = 0.0, finder.start(0.0, sim.deg), 0, 0
    while not finder.finished and now < 120.0:
        before = sim.deg
        sim.advance(goal, 0.05)
        now += 0.05
        moving += abs(sim.deg - before) > 0.02
        n += 1
        new = finder.update(now, sim.deg)
        goal = goal if new is None else new
    assert finder.state == DONE, finder.message
    assert now < 90.0              # ~350 deg out and back at 25 deg/s plus the settling, not minutes of stop-and-go
    assert moving / n > 0.5        # it spends most of the time actually moving


def test_passes_agree_under_friction_because_the_reading_is_held_at_the_confirming_push():
    sim = SimGripper(10.0, 1, contact_deg=60.0, coulomb=0.7, obstacle_k=400.0)
    gauge, _ = run(sim, GaugeParams(limit_deg=100.0, speed_deg_s=4.0, repeats=3, max_preload_deg=8.6), ticks=9000)
    assert gauge.state == DONE, gauge.message
    assert gauge.result.std < 0.5


def test_a_range_that_does_not_contain_the_jaw_is_reported_once(win):
    arm(win)
    win.joint_deg_ranges["finger_left"] = (-336.0, 12.0)
    win._range_checked = False
    win._on_positions_updated({"finger_left": 24.0})          # inside by 12 deg margin? no: 24 > 12 + 10
    assert "360" in win.statusBar().currentMessage()
    win.statusBar().clearMessage()
    win._on_positions_updated({"finger_left": 24.0})
    assert win.statusBar().currentMessage() == ""             # only once per connection
    win._range_checked = False
    win._on_positions_updated({"finger_left": 5.0})           # inside: silent
    assert win.statusBar().currentMessage() == ""


# ---------------------------------------------------------------- leader relay: grip limit and live width
def fresh(win, angle):
    win.current_positions["finger_left"] = angle
    win._last_follower_update = mw.time.monotonic()


def test_the_leader_cannot_squeeze_harder_than_the_push_limit(win):
    arm(win)                       # kp 8, Break-away push 8 deg -> the setpoint stays within 8 deg of the jaw
    fresh(win, 50.0)
    assert win._limit_grip("finger_left", 100.0) == pytest.approx(58.0)
    assert win._limit_grip("finger_left", -100.0) == pytest.approx(50.0 - math.degrees(mw.MainWindow.GRIP_FREE_MAX_NM / 8.0))   # opening: free
    assert win._limit_grip("finger_left", 53.0) == 53.0            # a goal inside the lead is untouched
    assert win._limit_grip("joint1", 100.0) == 100.0               # only the gripper


def test_grip_limit_uses_the_ceiling_for_a_stiff_kp(win):
    arm(win, kp=40.0)              # 1.5 N*m / 40 = 2.1 deg
    fresh(win, 50.0)
    assert win._limit_grip("finger_left", 100.0) == pytest.approx(50.0 + math.degrees(1.5 / 40.0), abs=1e-6)


def test_grip_limit_waits_for_a_fresh_position_and_skips_other_modes(win):
    arm(win)
    win.current_positions["finger_left"] = 50.0
    win._last_follower_update = mw.time.monotonic() - 5.0
    assert win._limit_grip("finger_left", 100.0) is None            # no fresh feedback: send nothing
    arm(win, mode=Control_Type.POS_VEL)
    assert win._limit_grip("finger_left", 100.0) == 100.0           # POS_VEL has its own velocity-limited loop


def test_the_relay_sends_the_limited_gripper_goal(win, monkeypatch):
    worker = arm(win)
    monkeypatch.setattr(win, "_leader_deg_to_follower_deg", lambda name, deg: deg)
    win.teleop_engaged, win.control_source = True, "leader"
    fresh(win, 50.0)
    win._on_leader_positions({"finger_left": 100.0})
    assert worker.last_goals["finger_left"] == pytest.approx(58.0)


def test_live_width_follows_the_jaw_once_calibrated(win):
    arm(win)
    win._gauge_cal.set_point(80.0, 0.0)
    win._gauge_cal.set_point(32.0, 30.0)       # 30 mm per 48 deg, closing = angle up
    win._on_positions_updated({"finger_left": 56.0})
    assert win.gripper_gauge_panel.live_label.text() == "15.0 mm"
    win._on_positions_updated({"finger_left": 10.0})
    assert "outside your references" in win.gripper_gauge_panel.live_label.text()
    win._gauge_cal.clear()
    win._on_positions_updated({"finger_left": 56.0})
    assert win.gripper_gauge_panel.live_label.text() == "-"


def moving(win, angles, dt=0.05):
    """Feed the jaw's positions as follower updates `dt` apart."""
    t = mw.time.monotonic()
    for i, angle in enumerate(angles):
        win.current_positions["finger_left"] = angle
        win._last_follower_update = t
        win._grip_hist.append((t - dt * (len(angles) - 1 - i), angle))


def test_a_moving_jaw_is_led_further_than_a_stopped_one(win):
    arm(win)
    moving(win, [50.0, 50.0, 50.0])
    stopped = win._limit_grip("finger_left", 200.0) - 50.0
    assert stopped == pytest.approx(8.0)                              # the Break-away push
    win._grip_hist.clear()
    moving(win, [44.0, 47.0, 50.0])                                    # 60 deg/s
    free = win._limit_grip("finger_left", 200.0) - 50.0
    assert free > stopped + 5.0
    assert free <= math.degrees(mw.MainWindow.GRIP_FREE_MAX_NM / 8.0) + 1e-9                      # never past the free ceiling


def test_a_jaw_that_just_hit_something_is_limited_at_once(win):
    arm(win)
    moving(win, [44.0, 47.0, 50.0, 50.0])                              # fast, then stopped in the newest interval
    assert win._limit_grip("finger_left", 200.0) - 50.0 == pytest.approx(8.0)


def test_contact_latches_the_width_and_releases(win):
    arm(win)
    win._gauge_cal.set_point(80.0, 0.0)
    win._gauge_cal.set_point(32.0, 30.0)
    win.teleop_engaged, win.control_source = True, "leader"
    win._grip_wanted = 100.0                                          # the leader keeps squeezing
    t = mw.time.monotonic()
    win.current_positions["finger_left"] = 56.0
    for k in range(6):                                                # the jaw sits at 56 for 0.3 s
        win._grip_hist.append((t + 0.05 * k, 56.0))
        win._update_grip_contact(t + 0.05 * k, 56.0)
    assert win._grip_contact == 56.0
    win._on_positions_updated({"finger_left": 56.4})                  # still holding: the reading stays at the latch
    assert win.top_bar.width_value.text() == "15.0 mm" and win.top_bar.width_value.property("state") == "grip"
    win._grip_wanted = 40.0                                           # the hand opens
    win._on_positions_updated({"finger_left": 56.0})
    assert win._grip_contact is None
    assert win.top_bar.width_value.property("state") == "free"


def test_no_contact_while_the_jaw_is_moving_or_the_leader_is_not_squeezing(win):
    arm(win)
    win.teleop_engaged, win.control_source = True, "leader"
    win._grip_wanted = 100.0
    t = mw.time.monotonic()
    for k in range(8):                                                # moving 30 deg/s
        angle = 40.0 + 1.5 * k
        win._grip_hist.append((t + 0.05 * k, angle))
        win._update_grip_contact(t + 0.05 * k, angle)
    assert win._grip_contact is None
    win._grip_wanted = 53.0                                           # stopped, but nobody is asking for more
    for k in range(8):
        win._grip_hist.append((t + 1.0 + 0.05 * k, 52.0))
        win._update_grip_contact(t + 1.0 + 0.05 * k, 52.0)
    assert win._grip_contact is None


def test_the_top_bar_shows_the_width_only_when_calibrated(win):
    arm(win)
    win._on_positions_updated({"finger_left": 56.0})
    assert win.top_bar.width_pill.isHidden()
    win._gauge_cal.set_point(80.0, 0.0)
    win._gauge_cal.set_point(32.0, 30.0)
    win._on_positions_updated({"finger_left": 56.0})
    assert not win.top_bar.width_pill.isHidden() and win.top_bar.width_value.text() == "15.0 mm"



def test_contact_beeps_once_when_enabled(win, monkeypatch):
    beeps = []
    monkeypatch.setattr(mw.QApplication, "beep", staticmethod(lambda: beeps.append(1)))
    arm(win)
    win.teleop_engaged, win.control_source = True, "leader"
    win._grip_wanted = 100.0
    t = mw.time.monotonic()
    for k in range(10):
        win._grip_hist.append((t + 0.05 * k, 56.0))
        win._update_grip_contact(t + 0.05 * k, 56.0)
    assert beeps == [1]                                   # latched once, not on every cycle
    win.gripper_gauge_panel.beep_check.setChecked(False)
    win._grip_contact = None
    win._grip_still_since = None
    for k in range(10):
        win._grip_hist.append((t + 1 + 0.05 * k, 56.0))
        win._update_grip_contact(t + 1 + 0.05 * k, 56.0)
    assert win._grip_contact == 56.0 and beeps == [1]     # latched again, silently
