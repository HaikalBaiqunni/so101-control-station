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


class SimGripper:
    """A jaw driven the way MIT mode drives it: torque = kp * (setpoint - q) - kd * speed, with
    friction (stiction included), inertia, a hard end stop and optionally an object at `contact_deg`."""

    def __init__(self, q_deg, sign=1, contact_deg=None, kp=8.0, kd=0.5, coulomb=0.15, end_deg=100.0, obstacle_k=60.0):
        self.q = math.radians(q_deg)
        self.v = 0.0
        self.sign, self.kp, self.kd, self.coulomb = sign, kp, kd, coulomb
        self.contact = None if contact_deg is None else math.radians(contact_deg)
        self.end = math.radians(end_deg)
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
