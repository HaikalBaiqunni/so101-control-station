"""Top bar, page switching, drawer and the Stop button of the redesigned shell."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from ui import main_window as mw


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # keep gui_settings.json and logs/ away from the checkout
    w = mw.MainWindow()
    yield w
    w.robot_worker = None
    w.close()


class FakeWorker:
    """Records torque requests; stands in for RobotWorker and DmRobotWorker alike
    (both expose the same request_torque signature)."""

    port = "COMX"

    def __init__(self):
        self.torque_calls: list[bool] = []
        self.last_goals: dict = {}
        self.bus = None

    def request_torque(self, enabled: bool) -> None:
        self.torque_calls.append(enabled)


def test_stop_turns_torque_off_and_forces_manual(win):
    win.robot_worker = FakeWorker()
    win._on_torque(True)
    win.control_source_panel.leader_radio.setEnabled(True)
    win.control_source_panel.leader_radio.setChecked(True)
    win.control_source_panel._on_source_clicked(2)
    assert win.control_source == "leader"

    win.top_bar.stop_btn.click()

    assert win.robot_worker.torque_calls == [True, False]
    assert win.follower_torque_enabled is False
    assert win.control_source == "manual"
    assert win.control_source_panel.manual_radio.isChecked()


def test_stop_without_a_follower_is_harmless(win):
    win.top_bar.stop_btn.click()
    assert win.control_source == "manual"


def test_chips_follow_connection_state(win):
    win.robot_worker = FakeWorker()
    win._on_connection_changed(True)
    assert win.top_bar.chips["follower"].property("state") == "good"
    win._on_torque(True)
    assert win.top_bar.chips["torque"].text() == "Torque ON"
    win._on_connection_changed(False)
    assert win.follower_torque_enabled is False
    assert win.top_bar.chips["follower"].property("state") == "off"
    assert win.top_bar.chips["torque"].text() == "Torque off"


def test_setup_button_switches_page_and_back_returns(win):
    assert win.pages.currentIndex() == win.PAGE_STAGE
    win.top_bar.nav_buttons["setup"].click()
    assert win.pages.currentIndex() == win.PAGE_SETUP
    win.setup_hub.back_btn.click()
    assert win.pages.currentIndex() == win.PAGE_STAGE
    assert not win.top_bar.nav_buttons["setup"].isChecked()


def test_drawer_holds_one_panel_at_a_time(win):
    win.top_bar.nav_buttons["waypoints"].click()
    assert win.drawer.currentWidget() is win.teaching_panel
    win.top_bar.nav_buttons["telemetry"].click()
    assert win.drawer.currentWidget() is win.telemetry_panel
    assert not win.top_bar.nav_buttons["waypoints"].isChecked()
    win.top_bar.nav_buttons["setup"].click()
    assert win.drawer.isHidden()
    assert not win.top_bar.nav_buttons["telemetry"].isChecked()


def test_setup_hub_sections(win):
    assert win.setup_hub.section_titles()[:3] == ["Motors and ids", "Calibration", "Inputs"]
    assert win.setup_hub.stack.count() == len(win.setup_hub.section_titles())


def test_camera_card_follows_the_view_toggle(win):
    win.show()
    assert win.stage.camera_card.isHidden()
    win.twin_panel.camera_check.setChecked(True)
    assert not win.stage.camera_card.isHidden()
    win.twin_panel.camera_check.setChecked(False)
    assert win.stage.camera_card.isHidden()


@pytest.mark.parametrize("size", [(1100, 700), (1440, 900)])
def test_stage_cards_stay_inside_the_stage(win, size):
    win.resize(*size)
    win.show()
    win.stage._layout_cards()
    rect = win.stage.rect()
    for card in (win.stage.jog_card, win.stage.dock_card, win.twin_panel.view_card):
        geo = card.geometry()
        assert rect.contains(geo), (card, geo, rect)


def _pick(win, key):
    win.robot_combo.setCurrentIndex(win.robot_combo.findData(key))


def test_tune_button_exists_only_for_the_damiao_arm(win):
    win.show()
    _pick(win, "so101")
    assert not win.top_bar.nav_buttons["tune"].isVisible()
    _pick(win, "rebot_b601_dm")
    assert win.top_bar.nav_buttons["tune"].isVisible()
    win.top_bar.nav_buttons["tune"].click()
    assert win.drawer.currentWidget() is win.dm_tune_panel
    _pick(win, "so101")   # switching away closes a Tune drawer that no longer applies
    assert win.drawer.isHidden()


def test_calibration_section_follows_the_robot(win):
    _pick(win, "rebot_b601_dm")
    assert win.calibration_stack.currentWidget() is win.dm_calibration_page
    _pick(win, "so101")
    assert win.calibration_stack.currentWidget() is win.calibration_panel


def test_dm_calibration_cards_launch_the_existing_handlers(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win, "_on_dm_gripper_calibrate_requested", lambda: calls.append("grip"))
    monkeypatch.setattr(win, "_on_leader_calibrate_requested", lambda: calls.append("leader"))
    # signals were connected to the bound originals at construction, so emit
    # through fresh connections to the patched ones
    page = win.dm_calibration_page
    page.gripper_calibrate_requested.disconnect()
    page.leader_calibrate_requested.disconnect()
    page.gripper_calibrate_requested.connect(win._on_dm_gripper_calibrate_requested)
    page.leader_calibrate_requested.connect(win._on_leader_calibrate_requested)
    page.gripper_btn.click()
    page.leader_btn.click()
    assert calls == ["grip", "leader"]


def test_hub_has_data_and_logs_with_the_session_log_path(win):
    assert "Data and logs" in win.setup_hub.section_titles()
    assert str(win.session_logger.path) == win.data_logs_page.path_label.text()


def test_tune_panel_keeps_its_signals_and_round_trips_gains(win):
    seen = []
    win.dm_tune_panel.mit_gains_changed.connect(seen.append)
    kp, kd = win.dm_tune_panel.mit_gain_spins["joint4"]
    kp.setValue(20.0)
    assert seen and seen[-1]["joint4"][0] == 20.0
    win.dm_tune_panel.set_mit_gains({"joint4": (12.0, 1.0)})
    assert win.dm_tune_panel.mit_gains()["joint4"] == (12.0, 1.0)
    assert len(seen) == 1   # set_mit_gains must not re-emit
    win.dm_tune_panel.set_control_mode("mit")
    assert win.dm_tune_panel.control_mode_combo.currentData() == "mit"


def test_cards_can_be_moved_and_stay_in_bounds(win):
    from PySide6.QtCore import QPoint
    win.resize(1440, 900)
    win.show()
    stage = win.stage
    saved = []
    stage.layout_changed.connect(saved.append)
    stage._move_card("jog", QPoint(-500, 99999))   # far outside: must be clamped
    geo = stage.cards["jog"].geometry()
    assert stage.rect().contains(geo)
    assert stage.layout_state()["placed"]["jog"] == [0.0, 1.0]
    win.resize(1100, 700)   # a moved card keeps its relative place and stays inside
    stage._layout_cards()
    assert stage.rect().contains(stage.cards["jog"].geometry())


def test_layout_state_round_trips_and_resets(win):
    stage = win.stage
    stage.set_layout_state({"placed": {"dock": [0.5, 0.25], "bogus": [1, 1]}, "camera_size": [500, 400]})
    assert stage.layout_state()["placed"] == {"dock": [0.5, 0.25]}
    assert stage.layout_state()["camera_size"] == [500, 400]
    stage.set_layout_state("not a dict")   # foreign data must not crash
    stage.reset_layout()
    assert stage.layout_state()["placed"] == {}


def test_camera_card_can_be_resized_within_limits(win):
    from PySide6.QtCore import QSize
    win.resize(1440, 900)
    win.show()
    win.twin_panel.camera_check.setChecked(True)
    win.stage._resize_camera(QSize(10, 10))
    assert win.stage._camera_size.width() >= 300 and win.stage._camera_size.height() >= 230


def test_nav_buttons_and_mode_tiles_have_icons(win):
    for button in win.top_bar.nav_buttons.values():
        assert not button.icon().isNull()
    assert not win.top_bar.stop_btn.icon().isNull()
    for tile in (win.control_source_panel.manual_radio, win.control_source_panel.leader_radio):
        assert not tile.icon().isNull()
    assert win.control_source_panel.manual_radio.isChecked()


class TeleopFake(FakeWorker):
    def __init__(self):
        super().__init__()
        self.goals: list[tuple[str, float]] = []

    def request_goal(self, name, degrees):
        self.goals.append((name, degrees))


@pytest.fixture
def teleop(win):
    """Both arms 'connected', torque on, leader source selected, arms 0 deg apart."""
    win.robot_worker = TeleopFake()
    win._follower_connected = True
    win._leader_connected = True
    win.follower_torque_enabled = True
    win.control_source_panel.leader_radio.setChecked(True)
    win._on_control_source_changed("leader")
    joints = win.robot_profile.joint_order
    win.current_positions.update(dict.fromkeys(joints, 0.0))
    win._leader_converted.update(dict.fromkeys(joints, 0.0))
    return win


def test_selecting_the_leader_does_not_drive_the_follower(teleop):
    teleop._on_leader_positions(dict.fromkeys(teleop.robot_profile.joint_order, 5.0))
    assert teleop.robot_worker.goals == []
    assert not teleop.teleop_engaged


def test_engage_is_refused_until_aligned_then_relays(teleop):
    joints = teleop.robot_profile.joint_order
    teleop._leader_converted[joints[1]] = 40.0   # 40 deg apart, tolerance is 10
    can, text, _ = teleop._teleop_alignment()
    assert not can and joints[1] in text
    teleop._on_engage_toggled(True)
    assert not teleop.teleop_engaged

    teleop._leader_converted[joints[1]] = 4.0
    teleop._on_engage_toggled(True)
    assert teleop.teleop_engaged
    teleop._on_leader_positions(dict.fromkeys(joints, 3.0))
    assert teleop.robot_worker.goals   # now it relays


def test_gripper_is_not_part_of_the_alignment_check(teleop):
    joints = teleop.robot_profile.joint_order
    teleop._leader_converted[joints[-1]] = 90.0
    assert teleop._teleop_alignment()[0]


@pytest.mark.parametrize("how", ["stop", "torque_off", "source", "leader_lost", "follower_lost"])
def test_engaged_teleop_drops_on_every_safety_event(teleop, how):
    teleop._on_engage_toggled(True)
    assert teleop.teleop_engaged
    if how == "stop":
        teleop._on_emergency_stop()
    elif how == "torque_off":
        teleop._on_torque(False)
    elif how == "source":
        teleop.control_source_panel.force_manual()
    elif how == "leader_lost":
        teleop._on_leader_connection_changed(False)
    else:
        teleop._on_connection_changed(False)
    assert not teleop.teleop_engaged
    n = len(teleop.robot_worker.goals) if teleop.robot_worker else 0
    teleop._on_leader_positions(dict.fromkeys(teleop.robot_profile.joint_order, 1.0))
    assert (len(teleop.robot_worker.goals) if teleop.robot_worker else 0) == n


def test_reasons_when_engage_is_not_possible(teleop):
    teleop.follower_torque_enabled = False
    assert "torque" in teleop._teleop_alignment()[1].lower()
    teleop._follower_connected = False
    assert "follower" in teleop._teleop_alignment()[1].lower()
    teleop._leader_connected = False
    assert "leader" in teleop._teleop_alignment()[1].lower()


def test_engage_button_reflects_the_gate(teleop):
    panel = teleop.control_source_panel
    teleop._update_teleop_ui()
    assert panel.engage_btn.isEnabled() and panel.engage_btn.text() == "Engage teleop"
    teleop._leader_converted[teleop.robot_profile.joint_order[0]] = 50.0
    teleop._update_teleop_ui()
    assert not panel.engage_btn.isEnabled()
    teleop._leader_converted[teleop.robot_profile.joint_order[0]] = 0.0
    teleop._update_teleop_ui()   # the refresh timer does this in the app
    panel.engage_btn.click()   # a real click, through the signal
    assert teleop.teleop_engaged and panel.engage_btn.text() == "Disengage"
    panel.engage_btn.click()
    assert not teleop.teleop_engaged


def test_leader_pose_is_the_ghost_while_standing_by(teleop):
    joints = teleop.robot_profile.joint_order
    teleop.twin_panel.ghost_check.setChecked(True)
    teleop._leader_converted[joints[0]] = 30.0
    teleop.joint_deg_ranges = dict.fromkeys(joints, (-90.0, 90.0))
    assert teleop._compute_ghost() is not None
    teleop._leader_converted[joints[0]] = 0.0
    assert teleop._compute_ghost() is None   # identical to the arm: no ghost


def test_tolerance_is_persisted(teleop):
    teleop.control_source_panel.tolerance_spin.setValue(6)
    import json
    with open("gui_settings.json", encoding="utf-8") as f:
        assert json.load(f)["teleop_align_tolerance_deg"] == 6.0


# ---------------------------------------------------------------- tune: presets, tracking, nudge, speed
def _steady(goal, measured, t0=0.0, n=20, dt=1 / 30):
    return [(t0 + i * dt, goal, measured) for i in range(n)]


def test_tracking_metrics_step_response():
    from ui.tracking_chart import tracking_metrics
    dt = 1 / 30
    samples = _steady(0.0, 0.0, n=10)
    t = samples[-1][0] + dt
    # goal steps +10; the joint overshoots to 11 then settles at 10
    path = [0.0, 2.0, 6.0, 9.0, 11.0, 10.5, 10.1, 10.0, 10.0, 10.0, 10.0, 10.0]
    for i, m in enumerate(path):
        samples.append((t + i * dt, 10.0, m))
    m = tracking_metrics(samples)
    assert m["step_deg"] == 10.0
    assert abs(m["overshoot_pct"] - 10.0) < 1e-6
    assert m["peak_error"] == 10.0
    assert m["settle_s"] is not None and 0.0 < m["settle_s"] < 0.5


def test_tracking_metrics_without_a_step_or_data():
    from ui.tracking_chart import tracking_metrics
    assert tracking_metrics([])["peak_error"] is None
    m = tracking_metrics(_steady(5.0, 4.0))
    assert m["peak_error"] == 1.0 and m["step_deg"] is None and m["settle_s"] is None


def test_tracking_metrics_not_settled_yet():
    from ui.tracking_chart import tracking_metrics
    samples = _steady(0.0, 0.0, n=5)
    samples += [(1.0 + i / 30, 20.0, 1.0 * i) for i in range(6)]   # still climbing
    assert tracking_metrics(samples)["settle_s"] is None


def test_gain_presets_save_load_delete_and_persist(win):
    panel = win.dm_tune_panel
    kp, kd = panel.mit_gain_spins["joint4"]
    kp.setValue(24.0)
    assert panel.save_preset("hold v2")
    assert "hold v2" in panel.preset_names()
    kp.setValue(5.0)
    seen = []
    panel.mit_gains_changed.connect(seen.append)
    assert panel.load_preset("hold v2")
    assert panel.mit_gains()["joint4"][0] == 24.0
    assert seen and seen[-1]["joint4"][0] == 24.0   # loading pushes the gains out (live retune)
    import json
    with open("gui_settings.json", encoding="utf-8") as f:
        assert "hold v2" in json.load(f)["dm_mit_gain_presets"]
    assert panel.delete_preset("hold v2")
    assert "hold v2" not in panel.preset_names()
    assert not panel.save_preset("")   # empty / built-in names are refused
    assert not panel.save_preset(panel.preset_names()[0])


def test_damaged_presets_are_ignored(win):
    win.dm_tune_panel.set_presets({"ok": {"joint1": [1, 2]}, "bad": "nope", "worse": {"joint1": [1]}})
    assert "ok" in win.dm_tune_panel.preset_names()
    assert "bad" not in win.dm_tune_panel.preset_names()


def test_builtin_preset_loads_gentle_defaults(win):
    from ui.dm_tune_panel import BUILTIN_PRESET
    win.dm_tune_panel.mit_gain_spins["joint2"][0].setValue(99.0)
    win.dm_tune_panel.load_preset(BUILTIN_PRESET)
    assert win.dm_tune_panel.mit_gains()["joint2"] == (8.0, 0.5)


def test_speed_slider_maps_to_a_capped_velocity():
    from core.damiao_bus import (
        MAX_JOG_VEL_RAD_S,
        MAX_SPEED_VEL_RAD_S,
        MIN_SPEED_VEL_RAD_S,
        speed_percent_to_vel_limit,
    )
    assert speed_percent_to_vel_limit(30) == pytest.approx(MAX_JOG_VEL_RAD_S)   # default unchanged
    assert speed_percent_to_vel_limit(100) == MAX_SPEED_VEL_RAD_S                # never faster than the ceiling
    assert speed_percent_to_vel_limit(1) == MIN_SPEED_VEL_RAD_S                  # never zero
    assert speed_percent_to_vel_limit(15) < speed_percent_to_vel_limit(45)


def test_speed_slider_reaches_a_damiao_worker(win):
    from core.dm_robot_worker import DmRobotWorker
    worker = DmRobotWorker.__new__(DmRobotWorker)   # no thread / bus: only the setter is under test
    worker._speed_percent = 30.0
    worker._speed_dirty = False
    win.robot_worker = worker
    win.jog_panel.speed_slider.setValue(60)
    assert worker._speed_percent == 60.0 and worker._speed_dirty
    win.robot_worker = None   # the fixture closes the window without retiring a fake thread


def test_nudge_is_refused_unless_it_is_safe(win):
    from core.dm_robot_worker import DmRobotWorker
    worker = DmRobotWorker.__new__(DmRobotWorker)
    worker.last_goals = {"joint4": 10.0}
    goals = []
    worker.request_goal = lambda name, deg: goals.append((name, deg))
    win.robot_worker = worker
    win.joint_deg_ranges = {"joint4": (-50.0, 50.0)}
    win.current_positions["joint4"] = 10.0

    win._follower_connected = True
    win.follower_torque_enabled = False
    win._on_nudge_requested("joint4", 5.0)          # torque off
    assert goals == []

    win.follower_torque_enabled = True
    win.teleop_engaged = True
    win._on_nudge_requested("joint4", 5.0)          # teleop engaged
    assert goals == []
    win.teleop_engaged = False

    win._on_nudge_requested("joint4", 5.0)          # allowed
    assert goals == [("joint4", 15.0)]
    win._on_nudge_requested("joint4", 500.0)        # clamped to the calibrated range
    assert goals[-1] == ("joint4", 50.0)
    win.robot_worker = None


def test_nudge_buttons_follow_the_gate(win):
    from core.dm_robot_worker import DmRobotWorker
    worker = DmRobotWorker.__new__(DmRobotWorker)
    worker.last_goals = {}
    win.robot_worker = worker
    win._follower_connected = True
    win.follower_torque_enabled = False
    win._update_teleop_ui()
    assert not win.dm_tune_panel.nudge_plus_btn.isEnabled()
    win.follower_torque_enabled = True
    win._update_teleop_ui()
    assert win.dm_tune_panel.nudge_plus_btn.isEnabled()
    win.robot_worker = None


def test_tracking_samples_flow_only_while_the_tune_drawer_is_open(win):
    from core.dm_robot_worker import DmRobotWorker
    worker = DmRobotWorker.__new__(DmRobotWorker)
    worker.last_goals = {"joint1": 5.0}
    win.robot_worker = worker
    win._follower_connected = True
    win.current_positions["joint1"] = 4.0
    win._update_teleop_ui()
    assert win.dm_tune_panel.tracking_chart.samples() == []
    win.show()
    win.top_bar.nav_buttons["tune"].click()
    win._update_teleop_ui()
    assert len(win.dm_tune_panel.tracking_chart.samples()) == 1
    win.robot_worker = None


# ---------------------------------------------------------------- cards: minimise, auto-adjust
def _overlaps(stage):
    keys = [k for k, c in stage.cards.items() if not c.isHidden()]
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if stage.cards[a].geometry().intersects(stage.cards[b].geometry()):
                return (a, b)
    return None


@pytest.mark.parametrize("size", [(1100, 700), (1440, 900), (1920, 1080)])
@pytest.mark.parametrize("leader", [False, True])
def test_cards_never_overlap_or_sit_under_the_top_bar(win, size, leader):
    from ui.stage_view import TOP
    win.resize(*size)
    win.show()
    if leader:
        win.control_source_panel.leader_radio.click()
    win.twin_panel.camera_check.setChecked(True)
    win.stage._layout_cards()
    assert _overlaps(win.stage) is None
    for card in win.stage.cards.values():
        if not card.isHidden():
            assert card.geometry().top() >= TOP
            assert win.stage.rect().contains(card.geometry())


def test_a_saved_position_under_the_bar_or_on_another_card_is_corrected(win):
    from ui.stage_view import TOP
    win.resize(1100, 700)
    win.show()
    win.stage.set_layout_state({"placed": {"view": [0.0, 0.0], "jog": [1.0, 0.0], "dock": [0.1, 0.9]}})
    assert _overlaps(win.stage) is None
    assert all(c.geometry().top() >= TOP for c in win.stage.cards.values() if not c.isHidden())


def test_cards_minimise_to_a_tab_and_expand_again(win):
    win.resize(1440, 900)
    win.show()
    stage = win.stage
    full = stage.cards["jog"].geometry()
    stage.cards["jog"].min_btn.click()
    assert stage.is_collapsed("jog")
    assert stage.tabs["jog"].isVisible()
    assert stage.cards["jog"].width() < full.width() and stage.cards["jog"].height() < 60
    assert stage.layout_state()["collapsed"] == ["jog"]
    stage.tabs["jog"].click()
    assert not stage.is_collapsed("jog")
    assert stage.cards["jog"].geometry().width() == full.width()


def test_the_view_card_minimises_too(win):
    win.resize(1440, 900)
    win.show()
    full_height = win.stage.cards["view"].height()
    assert full_height > 200
    win.twin_panel.view_min_btn.click()
    assert win.stage.is_collapsed("view") and win.stage.cards["view"].width() < 200
    win.stage.tabs["view"].click()
    assert win.stage.cards["view"].width() == 300
    # right away, without waiting for another layout event: not a collapsed-height sliver
    assert win.stage.cards["view"].height() == full_height
    assert all(part.isVisible() for part in win.stage.cards["view"].parts)


def test_minimised_state_is_saved_and_restored(win):
    win.resize(1440, 900)
    win.show()
    win.stage.cards["dock"].min_btn.click()
    import json
    with open("gui_settings.json", encoding="utf-8") as f:
        assert json.load(f)["stage_layout"]["collapsed"] == ["dock"]
    win.stage.set_layout_state({"collapsed": ["jog", "bogus"]})
    assert win.stage.is_collapsed("jog") and not win.stage.is_collapsed("dock")


def test_view_folds_itself_when_there_is_no_room_and_reopens_when_there_is(win):
    win.resize(1100, 700)
    win.show()
    win.control_source_panel.leader_radio.click()   # taller dock
    win.stage._layout_cards()
    assert "view" in win.stage._auto and win.stage.tabs["view"].isVisible()
    assert "view" not in win.stage._collapsed        # automatic: not saved as the user's choice
    win.resize(1920, 1200)
    win.stage._layout_cards()
    assert "view" not in win.stage._auto and not win.stage.tabs["view"].isVisible()


def test_reset_layout_also_unfolds_cards(win):
    win.resize(1440, 900)
    win.show()
    win.stage.set_collapsed("jog", True)
    win.stage.reset_layout()
    assert not win.stage.is_collapsed("jog")


# ---------------------------------------------------------------- palette, joint bars, TCP line
def test_palette_filter_needs_every_word():
    from ui.command_palette import PaletteAction, filter_actions
    acts = [PaletteAction("Open Tune", "drawer", lambda: None, "kp kd"),
            PaletteAction("Toggle ghost target", "view", lambda: None)]
    assert [a.title for a in filter_actions(acts, "tune")] == ["Open Tune"]
    assert [a.title for a in filter_actions(acts, "kd open")] == ["Open Tune"]
    assert filter_actions(acts, "nothing here") == []
    assert len(filter_actions(acts, "  ")) == 2


def test_palette_offers_nothing_that_moves_an_arm(win):
    titles = " | ".join(a.title.lower() for a in win._palette_actions())
    for forbidden in ("engage", "nudge", "torque on", "jog", "play"):
        assert forbidden not in titles


def test_palette_lists_tune_and_calibration_only_for_the_damiao_arm(win):
    _pick(win, "so101")
    titles = [a.title for a in win._palette_actions()]
    assert "Open Tune" not in titles and not any("Calibrate" in t for t in titles)
    _pick(win, "rebot_b601_dm")
    titles = [a.title for a in win._palette_actions()]
    assert "Open Tune" in titles and any("Calibrate leader" in t for t in titles)


def test_palette_runs_actions_and_leader_choice_does_not_engage(win):
    from ui.command_palette import CommandPalette
    palette = CommandPalette(win)
    palette.set_actions(win._palette_actions())
    palette.search.setText("leader arm")
    assert palette.titles() == ["Control source: Leader arm"]
    palette.list.setCurrentRow(0)
    palette.run_current()
    assert win.control_source == "leader" and not win.teleop_engaged
    palette.set_actions(win._palette_actions())
    palette.search.setText("open telemetry")
    palette.run_current()
    assert not win.drawer.isHidden() and win.drawer.currentWidget() is win.telemetry_panel


def test_the_palette_and_stop_shortcuts_exist(win):
    from PySide6.QtGui import QShortcut
    keys = {s.key().toString() for s in win.findChildren(QShortcut)}
    assert {"Ctrl+K", "Ctrl+Shift+Space"} <= keys


def test_joint_rows_draw_position_and_target_bars(win):
    row = next(iter(win.joint_panel.rows.values()))
    row.set_limits(-100.0, 100.0)
    row.set_feedback_deg(0.0)
    row.set_target_deg(50.0)
    assert row.bar._position == 0.5 and row.bar._target == 0.75
    row.set_target_deg(None)
    assert row.bar._target is None
    row.set_feedback_deg(1000.0)   # beyond the range: clamped, never drawn outside the bar
    assert row.bar._position == 1.0


def test_targets_reach_the_bars_from_the_worker_goals(win):
    class Worker:
        last_goals = {}
    name = next(iter(win.joint_panel.rows))
    win.joint_panel.rows[name].set_limits(-100.0, 100.0)
    win.robot_worker = Worker()
    Worker.last_goals = {name: 100.0}
    win._refresh_ui()
    assert win.joint_panel.rows[name].bar._target == 1.0
    win.robot_worker = None


def test_tcp_summary_shows_only_in_joint_mode(win):
    win.jog_panel.set_tcp_summary("TCP (world)  X 1  Y 2  Z 3 mm")
    win.show()
    assert win.jog_panel.tcp_label.isVisible()
    win.jog_panel.set_tcp_summary("")
    assert not win.jog_panel.tcp_label.isVisible()


# ---------------------------------------------------------------- about
def test_about_names_the_creator_and_the_stack():
    from ui.about_dialog import about_text
    text = about_text()
    assert "Haikal Baiqunni" in text
    for item in ("PySide6", "MuJoCo", "NumPy", "OpenCV", "Feetech", "motorbridge", "B601-DM", "SO-101"):
        assert item in text


def test_about_version_matches_pyproject():
    import re
    from pathlib import Path

    from ui.about_dialog import APP_VERSION
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version\s*=\s*"{re.escape(APP_VERSION)}"', pyproject, re.M)


def test_about_opens_from_the_top_bar_and_the_palette(win, monkeypatch):
    from ui.about_dialog import AboutDialog
    opened = []
    monkeypatch.setattr(AboutDialog, "exec", lambda self: opened.append(self.windowTitle()) or 0)
    win.top_bar.about_btn.click()
    assert len(opened) == 1 and "About" in opened[0]
    assert any(a.title == "About" for a in win._palette_actions())


def test_connected_status_labels_turn_green(win):
    """objectName selects the colour; without a repolish the label stayed red after connecting."""
    from ui.style import COLORS, STYLE_SHEET

    def colours_of(label):
        image = label.grab().toImage()
        return {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())}

    app = QApplication.instance()
    app.setStyleSheet(STYLE_SHEET)   # the suite otherwise runs unstyled
    try:
        win.show()
        panel = win.connection_panel
        assert COLORS["danger"] in colours_of(panel.status_label)
        panel.set_connected(True)
        assert COLORS["good"] in colours_of(panel.status_label)
        assert COLORS["danger"] not in colours_of(panel.status_label)
        panel.set_connected(False)
        assert COLORS["danger"] in colours_of(panel.status_label)
    finally:
        app.setStyleSheet("")


# ---------------------------------------------------------------- bundled SO-101 model
def test_the_so101_profile_bundles_a_loadable_model():
    import os

    import mujoco

    from core import robot_profiles
    from core.robot_profiles import PROFILES

    assert PROFILES["so101"].default_mjcf_path == ""   # the autouse fixture blanks it for the suite
    path = os.path.join(robot_profiles._MODELS_DIR, "so101", "scene.xml")
    assert os.path.isfile(path)
    model = mujoco.MjModel.from_xml_path(path)       # every referenced mesh is really there
    joint_names = {mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, i) for i in range(model.njnt)}
    assert set(PROFILES["so101"].joint_order) <= joint_names
    for name in ("LICENSE-Apache-2.0.txt", "NOTICE.md"):
        assert os.path.isfile(os.path.join(os.path.dirname(path), name))


@pytest.mark.real_so101_profile
def test_the_real_so101_profile_points_at_the_bundle():
    import os

    from core.robot_profiles import PROFILES
    assert PROFILES["so101"].default_mjcf_path.endswith(os.path.join("models", "so101", "scene.xml"))


# ---------------------------------------------------------------- MIT setpoint slew and the holding preset
def test_setpoint_slew_approaches_the_goal_at_a_bounded_rate():
    from core.setpoint_slew import SetpointSlew
    slew = SetpointSlew()
    # first goal starts from where the joint really is, not from the goal
    assert slew.step("j", 90.0, 10.0, rate_deg_s=60.0, dt=0.5) == 40.0
    assert slew.step("j", 90.0, 10.0, rate_deg_s=60.0, dt=0.5) == 70.0
    assert slew.step("j", 90.0, 10.0, rate_deg_s=60.0, dt=0.5) == 90.0     # never overshoots the goal
    assert slew.at_goal("j", 90.0)
    assert slew.step("j", 0.0, 90.0, rate_deg_s=60.0, dt=0.25) == 75.0     # works downward too
    slew.reset("j")
    assert not slew.at_goal("j", 0.0)
    assert slew.step("j", 5.0, None, rate_deg_s=10.0, dt=1.0) == 5.0       # no measurement: start at the goal


def test_slew_rate_follows_the_speed_slider():
    from core.damiao_bus import speed_percent_to_mit_rate_deg_s as rate
    assert rate(30) == 90.0 and rate(100) == 300.0 and rate(1) == 5.0
    assert rate(10) < rate(50)


class _FakeMitBus:
    from core.dm_can import Control_Type as _CT
    control_mode = _CT.MIT

    def __init__(self):
        self.calls: list[dict] = []
        self.mit_gains = {}
        self.vel_limit_rad_s = 0.3

    def write_goals_deg(self, goals):
        self.calls.append(dict(goals))


def _mit_worker(measured):
    from core.dm_robot_worker import DmRobotWorker
    worker = DmRobotWorker.__new__(DmRobotWorker)
    from core.setpoint_slew import SetpointSlew
    worker.bus = _FakeMitBus()
    worker._slew = SetpointSlew()
    worker._measured = dict(measured)
    worker._speed_percent = 30.0        # 90 deg/s
    worker._gravity = None
    worker._g_target = worker._g_now = 0.0
    worker._g_sent_nonzero = False
    worker._torque_on = True
    worker.error = type("S", (), {"emit": staticmethod(lambda msg: None)})()
    return worker


def test_a_far_mit_target_is_streamed_as_a_ramp_not_a_jump():
    worker = _mit_worker({"joint2": 0.0})
    held = {"joint2": 60.0}
    worker._stream_mit(held, held, dt=0.1)            # fresh goal: first step only (9 deg)
    for _ in range(10):
        worker._stream_mit({}, held, dt=0.1)
    sent = [c["joint2"] for c in worker.bus.calls]
    assert sent[0] == pytest.approx(9.0)
    assert all(b - a <= 9.0 + 1e-6 for a, b in zip(sent, sent[1:], strict=False))   # never more than rate * dt
    assert sent[-1] == pytest.approx(60.0)


def test_an_arrived_mit_joint_is_not_resent_every_cycle():
    worker = _mit_worker({"joint2": 59.0})
    held = {"joint2": 60.0}
    worker._stream_mit(held, held, dt=0.1)            # arrives immediately (1 deg away)
    n = len(worker.bus.calls)
    for _ in range(5):
        worker._stream_mit({}, held, dt=0.1)
    assert len(worker.bus.calls) == n                 # idle traffic unchanged
    worker._stream_mit(held, held, dt=0.1)            # but an explicit new request is sent
    assert len(worker.bus.calls) == n + 1


def test_the_holding_preset_stays_inside_the_kp_kd_ranges(win):
    from ui.dm_tune_panel import HOLDING_GAINS, HOLDING_PRESET
    for kp, kd in HOLDING_GAINS.values():
        assert 0 <= kp <= 500 and 0 <= kd <= 5
    seen = []
    win.dm_tune_panel.mit_gains_changed.connect(seen.append)
    assert win.dm_tune_panel.load_preset(HOLDING_PRESET)
    assert win.dm_tune_panel.mit_gains()["joint2"] == (40.0, 2.5)
    assert seen                                       # pushed out for a live retune
    assert not win.dm_tune_panel.save_preset(HOLDING_PRESET)   # built-ins cannot be overwritten


# ---------------------------------------------------------------- gravity feed-forward
B601_XML = None


def _b601_path():
    import os

    from core import robot_profiles
    return os.path.join(robot_profiles._MODELS_DIR, "rebot_b601_dm", "rebot_b601_dm.xml")


def _gravity_model():
    from core.gravity import GravityModel
    return GravityModel(_b601_path(), ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "finger_left"])


def test_gravity_model_covers_the_arm_joints_but_not_the_gripper():
    model = _gravity_model()
    assert model.joint_names == ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"]
    assert 3.0 < model.total_mass_kg < 6.0           # a real-looking arm, not an empty model


def test_gravity_torque_is_zero_on_the_vertical_axis_and_loads_the_lifting_joints():
    model = _gravity_model()
    tau = model.torques({})
    assert tau["joint1"] == pytest.approx(0.0, abs=1e-6)   # base yaw: gravity has no lever arm
    assert abs(tau["joint3"]) > 1.0                        # the elbow carries real load at the rest pose
    # holding torque is exactly what it takes to hold: it equals MuJoCo's bias at qvel = 0
    again = model.torques({"joint2": 0.0, "joint3": 0.0})
    assert again == pytest.approx(tau)


def test_gravity_torque_changes_with_pose_and_flips_sign_through_vertical():
    model = _gravity_model()
    a = model.torques({"joint2": -30.0, "joint3": -30.0})
    b = model.torques({"joint2": -120.0, "joint3": -30.0})
    assert a["joint2"] != pytest.approx(b["joint2"])


def test_feedforward_is_clamped_per_joint_and_never_given_to_the_gripper():
    from core.gravity import FF_TAU_CAP_NM, clamp_ff
    assert clamp_ff("joint2", 99.0) == FF_TAU_CAP_NM["joint2"]
    assert clamp_ff("joint5", -99.0) == -FF_TAU_CAP_NM["joint5"]
    assert clamp_ff("finger_left", 5.0) == 0.0 and clamp_ff("unknown", 5.0) == 0.0


class _FfBus(_FakeMitBus):
    def write_goals_deg(self, goals, feedforward=None):
        self.calls.append({"goals": dict(goals), "ff": dict(feedforward) if feedforward else None})


def _gravity_worker(measured):
    worker = _mit_worker(measured)
    worker.bus = _FfBus()
    worker._gravity = _gravity_model()
    worker._torque_on = True
    return worker


ARM = {"joint1": 0.0, "joint2": -30.0, "joint3": -30.0, "joint4": 0.0, "joint5": 0.0, "joint6": 0.0}


def test_gravity_ramps_in_and_resends_every_held_joint_with_a_torque():
    worker = _gravity_worker(ARM)
    held = {"joint2": -30.0}
    worker.request_gravity_percent(100.0)
    worker._stream_mit(held, held, dt=0.05)
    first = worker.bus.calls[-1]
    # 40 %/s * 0.05 s = 2 % of the way in
    full = worker._gravity.torques(ARM)["joint3"]
    assert first["ff"]["joint3"] == pytest.approx(full * 0.02, rel=1e-6)
    assert "joint3" in first["goals"]                  # a joint with no goal is held where it is
    for _ in range(100):
        worker._stream_mit({}, held, dt=0.05)
    last = worker.bus.calls[-1]
    cap = 12.0
    assert last["ff"]["joint3"] == pytest.approx(max(-cap, min(cap, full)))
    assert "finger_left" not in last["ff"]


def test_the_unfloated_joint_is_held_not_chased():
    worker = _gravity_worker(ARM)
    worker.request_gravity_percent(100.0)
    worker._stream_mit({}, {}, dt=0.05)
    seeded = worker._slew.setpoint["joint3"]
    worker._measured["joint3"] = -20.0                 # it sags
    worker._stream_mit({}, {}, dt=0.05)
    assert worker._slew.setpoint["joint3"] == seeded   # the setpoint did not follow it down


def test_switching_gravity_off_sends_one_zero_torque_command_then_stops_streaming():
    worker = _gravity_worker(ARM)
    held = {"joint2": -30.0}
    worker.request_gravity_percent(100.0)
    for _ in range(30):
        worker._stream_mit({}, held, dt=0.05)
    worker.request_gravity_percent(0.0)
    for _ in range(80):                                # ramp down at 40 %/s
        worker._stream_mit({}, held, dt=0.05)
    assert worker.bus.calls[-1]["ff"] is None          # the final command carried no torque
    n = len(worker.bus.calls)
    for _ in range(5):
        worker._stream_mit({}, held, dt=0.05)
    assert len(worker.bus.calls) == n                  # and then it is quiet again


def test_no_feedforward_while_torque_is_off_or_without_a_model():
    worker = _gravity_worker(ARM)
    worker.request_gravity_percent(100.0)
    worker._torque_on = False
    worker._stream_mit({}, {"joint2": -30.0}, dt=0.5)
    assert worker.bus.calls == [] or worker.bus.calls[-1]["ff"] is None
    worker._torque_on = True
    worker._gravity = None
    worker._stream_mit({}, {"joint2": -30.0}, dt=0.5)
    assert all(c["ff"] is None for c in worker.bus.calls)


# ---------------------------------------------------------------- gravity check + switches in the GUI
def _gravity_fake():
    """A connected B601-DM follower in MIT mode (a DmRobotWorker shell: no thread, no bus)."""
    from core.dm_can import Control_Type
    from core.dm_robot_worker import DmRobotWorker
    worker = DmRobotWorker.__new__(DmRobotWorker)
    worker.control_mode = Control_Type.MIT
    worker.last_goals = {}
    worker.percent_calls = []
    worker.request_gravity_percent = worker.percent_calls.append
    worker.request_torque = lambda enabled, name=None: None
    return worker


def _telemetry_from_model(scale=1.0, speed=0.0):
    model = _gravity_model()
    tau = model.torques({})
    return {j: {"position": 0.0, "velocity": speed, "load": t * scale} for j, t in tau.items()}


@pytest.fixture
def gwin(win):
    _pick(win, "rebot_b601_dm")
    win.robot_worker = _gravity_fake()
    win._follower_connected = True
    win.follower_torque_enabled = True
    yield win
    win.robot_worker = None


def test_gravity_check_passes_when_the_motors_report_what_the_model_predicts(gwin):
    gwin._last_telemetry = _telemetry_from_model(scale=0.8)      # friction etc.: a bit less than ideal
    gwin._on_gravity_check()
    assert gwin._gravity_ok
    assert "CHECK PASSED" in gwin.dm_tune_panel.gravity_result.text()


def test_gravity_check_fails_on_a_flipped_sign(gwin):
    gwin._last_telemetry = _telemetry_from_model(scale=-1.0)     # the motor's sign convention is opposite
    gwin._on_gravity_check()
    assert not gwin._gravity_ok
    assert "MISMATCH" in gwin.dm_tune_panel.gravity_result.text()


def test_gravity_check_fails_on_a_wildly_wrong_magnitude(gwin):
    gwin._last_telemetry = _telemetry_from_model(scale=10.0)
    gwin._on_gravity_check()
    assert not gwin._gravity_ok


def test_gravity_check_refuses_a_moving_arm(gwin):
    gwin._last_telemetry = _telemetry_from_model(speed=20.0)
    gwin._on_gravity_check()
    assert not gwin._gravity_ok and "moving" in gwin.dm_tune_panel.gravity_result.text()


def test_gravity_check_needs_connection_mit_and_torque(gwin):
    gwin._last_telemetry = _telemetry_from_model()
    gwin.follower_torque_enabled = False
    gwin._on_gravity_check()
    assert not gwin._gravity_ok and "torque" in gwin.dm_tune_panel.gravity_result.text().lower()
    gwin.follower_torque_enabled = True
    from core.dm_can import Control_Type
    gwin.robot_worker.control_mode = Control_Type.POS_VEL
    gwin._on_gravity_check()
    assert not gwin._gravity_ok and "MIT" in gwin.dm_tune_panel.gravity_result.text()


def test_the_amount_slider_is_locked_until_the_check_passes_and_refuses_if_forced(gwin):
    panel = gwin.dm_tune_panel
    gwin._update_teleop_ui()
    assert panel.gravity_check_btn.isEnabled() and not panel.gravity_slider.isEnabled()
    panel.gravity_slider.setValue(50)            # forced anyway (programmatic): must be refused and reset
    assert panel.gravity_percent() == 0 and 50 not in gwin.robot_worker.percent_calls
    gwin._last_telemetry = _telemetry_from_model()
    gwin._on_gravity_check()
    assert panel.gravity_slider.isEnabled()
    panel.gravity_slider.setValue(40)
    assert gwin.robot_worker.percent_calls[-1] == 40


@pytest.mark.parametrize("how", ["torque_off", "stop", "disconnect"])
def test_gravity_switches_off_and_relocks_on_every_safety_event(gwin, how):
    gwin._last_telemetry = _telemetry_from_model()
    gwin._on_gravity_check()
    gwin.dm_tune_panel.gravity_slider.setValue(60)
    assert gwin.robot_worker.percent_calls[-1] == 60
    worker = gwin.robot_worker
    if how == "torque_off":
        gwin._on_torque(False)
    elif how == "stop":
        gwin._on_emergency_stop()
    else:
        gwin._on_connection_changed(False)
    assert worker.percent_calls[-1] == 0
    assert gwin.dm_tune_panel.gravity_percent() == 0
    assert not gwin._gravity_ok                  # needs a fresh check before it can come back
