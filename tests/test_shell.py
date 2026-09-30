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
