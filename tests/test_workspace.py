"""The Workspace layout: task rail, tiles and the contextual right panel (ui/workspace_view.py)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from ui import main_window as mw

pytestmark = pytest.mark.workspace_layout   # these build the app's default layout, not the Classic one


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # keep gui_settings.json and logs/ away from the checkout
    w = mw.MainWindow()
    w.resize(1560, 880)
    w.show()
    yield w
    w.robot_worker = None
    w.close()


def _pick(win, key):
    win.robot_combo.setCurrentIndex(win.robot_combo.findData(key))


class FakeWorker:
    port = "COMX"

    def __init__(self):
        self.torque_calls: list[bool] = []
        self.last_goals: dict = {}
        self.bus = None

    def request_torque(self, enabled: bool) -> None:
        self.torque_calls.append(enabled)


def test_the_app_starts_in_the_workspace_layout(win):
    assert win.ui_layout == "workspace" and win.stage is None
    assert win.workspace is not None and win.rail is not None
    assert win.top_bar.nav_box.isHidden()                       # the rail navigates, not the top bar
    assert win.rail.current() == "operate"
    assert win.drawer.currentWidget() is win.workspace.operate_page
    # Operate holds Connection, Control source and Jog, all on screen and never over the twin
    for panel in (win.connection_panel, win.control_source_panel, win.jog_panel):
        assert win.workspace.operate_page.isAncestorOf(panel)
    assert not win.workspace.twin_tile.isAncestorOf(win.jog_panel)
    assert win.workspace.twin_tile.isAncestorOf(win.twin_panel)


def test_every_tool_of_the_classic_layout_is_reachable_from_the_rail(win):
    _pick(win, "rebot_b601_dm")
    tools = {"gauge": win.gripper_gauge_panel, "waypoints": win.teaching_panel, "record": win.recorder_panel,
             "tune": win.dm_tune_panel, "telemetry": win.telemetry_panel}
    for key, panel in tools.items():
        win.rail.buttons[key].click()
        assert win.rail.current() == key
        assert win.drawer.currentWidget() is panel and panel.isVisible()
        assert win.pages.currentIndex() == win.PAGE_STAGE
    win.rail.buttons["setup"].click()
    assert win.pages.currentIndex() == win.PAGE_SETUP and win.rail.current() == "setup"
    win.setup_hub.back_requested.emit()                          # Setup's own back button
    assert win.pages.currentIndex() == win.PAGE_STAGE and win.rail.current() == "operate"
    win.rail.buttons["telemetry"].click()
    win.rail.buttons["operate"].click()
    assert win.drawer.currentWidget() is win.workspace.operate_page
    assert not any(button.isChecked() for button in win.top_bar.nav_buttons.values())


def test_robot_specific_tools_follow_the_robot(win):
    _pick(win, "so101")
    assert win.rail.buttons["tune"].isHidden() and win.rail.buttons["gauge"].isHidden()
    _pick(win, "rebot_b601_dm")
    assert not win.rail.buttons["tune"].isHidden() and not win.rail.buttons["gauge"].isHidden()
    win.rail.buttons["tune"].click()
    _pick(win, "so101")                                          # a Tune panel that no longer applies closes
    assert win.rail.current() == "operate"
    assert win.drawer.currentWidget() is win.workspace.operate_page


def test_jog_folds_out_under_every_other_tool(win):
    ws = win.workspace
    assert ws.jog_toggle.isHidden()                              # Operate shows Jog itself
    win.rail.buttons["telemetry"].click()
    assert not ws.jog_toggle.isHidden() and ws.jog_fold.isHidden()
    ws.jog_toggle.click()
    assert ws.jog_fold.isVisible() and ws.jog_fold.isAncestorOf(win.jog_panel) and win.jog_panel.isVisible()
    win.rail.buttons["record"].click()                           # stays open across tools
    assert ws.jog_fold.isVisible() and ws.jog_fold.isAncestorOf(win.jog_panel)
    win.rail.buttons["operate"].click()
    assert ws.jog_fold.isHidden() and ws.operate_page.isAncestorOf(win.jog_panel) and win.jog_panel.isVisible()


def test_tiles_expand_and_restore(win):
    ws = win.workspace
    assert ws.camera_tile.isVisible() and ws.twin_tile.isVisible()   # the camera tile is on by default
    ws.twin_max_btn.click()
    assert ws.camera_tile.isHidden() and ws.twin_max_btn.text() == "Restore"
    ws.restore_tiles()
    assert ws.camera_tile.isVisible() and not ws.twin_max_btn.isChecked()

    second = win.camera_panel.add_slot()
    first = win.camera_panel.slot(0)
    assert not first.max_btn.isHidden() and not second.max_btn.isHidden()
    second.max_btn.click()                                       # one camera takes the whole middle
    assert ws.twin_tile.isHidden() and first.isHidden() and second.isVisible()
    first.max_btn.setChecked(False)                              # a no-op: it was not the expanded one
    ws.restore_tiles()
    assert ws.twin_tile.isVisible() and first.isVisible() and not second.max_btn.isChecked()

    win.twin_panel.camera_check.setChecked(False)                # the View options switch hides the tile
    assert ws.camera_tile.isHidden() and ws.twin_tile.isVisible()
    assert win._load_settings()["workspace_camera_tile"] is False
    win.twin_panel.camera_check.setChecked(True)
    assert ws.camera_tile.isVisible()
    win.camera_panel.remove_last_slot()


def test_view_options_live_in_a_popup_not_over_the_render(win):
    ws = win.workspace
    card = win.twin_panel.view_card
    assert not win.twin_panel.view.isAncestorOf(card)
    assert ws.view_btn.menu() is ws.view_menu and ws.view_menu.actions()[0].defaultWidget() is card
    assert win.twin_panel.view_min_btn.isHidden() and win.twin_panel.reset_layout_btn.isHidden()
    for check in (win.twin_panel.ghost_check, win.twin_panel.axes_check, win.twin_panel.hud_check,
                  win.twin_panel.camera_check):
        assert card.isAncestorOf(check)


def test_stop_and_chips_work_the_same(win):
    win.robot_worker = FakeWorker()
    win._on_connection_changed(True)
    assert win.top_bar.chips["follower"].property("state") == "good"
    win._on_torque(True)
    win.top_bar.stop_btn.click()
    assert win.robot_worker.torque_calls == [True, False] and win.control_source == "manual"


def test_switching_robot_clears_the_hud_of_the_previous_robots_joints(win):
    _pick(win, "so101")
    win.twin_panel.update_telemetry({"shoulder_pan": {"position_deg": 10.0, "load": 5.0}})
    assert win.twin_panel._hud_order == ["shoulder_pan"]
    _pick(win, "rebot_b601_dm")
    assert win.twin_panel._hud_order == [] and not win.twin_panel._hud_raw


def test_the_palette_opens_tools_and_offers_the_other_layout(win):
    actions = {a.title: a for a in win._palette_actions()}
    assert "Reset card layout" not in actions                    # no cards here
    actions["Open Telemetry"].run()
    assert win.rail.current() == "telemetry" and win.drawer.currentWidget() is win.telemetry_panel
    actions["Back to stage"].run()
    assert win.rail.current() == "operate"
    assert "Layout: switch to Classic" in actions


def test_switching_layout_is_saved_for_the_next_start(win, qapp, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    win._set_ui_layout("classic")
    assert asked and win._load_settings()["ui_layout"] == "classic"
    assert win.ui_layout == "workspace"                          # this window keeps running as it was
    win._set_ui_layout("workspace")                              # already the running layout: nothing to do
    assert len(asked) == 1

    classic = mw.MainWindow()                                    # the next start reads the setting
    try:
        assert classic.ui_layout == "classic" and classic.stage is not None and classic.workspace is None
        assert not classic.top_bar.nav_box.isHidden()
        assert "Layout: switch to Workspace" in {a.title for a in classic._palette_actions()}
    finally:
        classic.robot_worker = None
        classic.close()
    fallback = mw.MainWindow(layout="bogus")                     # an unknown name falls back
    try:
        assert fallback.ui_layout == "workspace"
    finally:
        fallback.robot_worker = None
        fallback.close()
