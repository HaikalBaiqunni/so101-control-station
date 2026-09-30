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
