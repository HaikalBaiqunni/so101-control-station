"""Screenshots for docs/MANUAL.html, taken from the real GUI, offscreen, no hardware.

    python tools/manual_shots.py            # writes tools/_shots/*.png (for a look)

`capture()` is what tools/make_manual.py calls. Everything runs against a scratch
working directory, so the developer's gui_settings.json / logs are never touched.
Some shots pretend both arms are online (chips, ghost, alignment) to show the layout;
the manual says so.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")   # without it offscreen shots have no text
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

W, H = 1440, 900
FULL_WIDTH = 1200   # full-window shots are scaled to this


def _to_pil(widget) -> Image.Image:
    image = widget.grab().toImage()
    rgb = image.convertToFormat(image.Format.Format_RGB888)
    return Image.frombuffer("RGB", (rgb.width(), rgb.height()), bytes(rgb.constBits()), "raw", "RGB", rgb.bytesPerLine())


def capture() -> dict[str, Image.Image]:
    os.chdir(tempfile.mkdtemp())   # scratch cwd: settings and logs land here, not in the repo
    from ui.about_dialog import AboutDialog
    from ui.command_palette import CommandPalette
    from ui.main_window import MainWindow
    from ui.style import STYLE_SHEET

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE_SHEET)
    win = MainWindow()
    win.resize(W, H)
    win.show()
    shots: dict[str, Image.Image] = {}

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    def wait_for_twin() -> None:
        deadline = time.time() + 25
        win.twin_panel._last_pixmap = None
        while win.twin_panel._last_pixmap is None and time.time() < deadline:
            pump(0.2)
        pump(1.0)

    def full(name: str) -> None:
        pump(0.4)
        img = _to_pil(win)
        shots[name] = img.resize((FULL_WIDTH, round(img.height * FULL_WIDTH / img.width)), Image.LANCZOS)

    def part(name: str, widget) -> None:
        pump(0.3)
        shots[name] = _to_pil(widget)

    def pose(degs: dict[str, float], ranges: dict) -> None:
        for joint in win.robot_profile.joint_order:
            lo, hi = ranges.get(joint, (-90.0, 90.0))
            win.current_positions[joint] = max(lo, min(hi, degs.get(joint, 0.0)))
        win._refresh_ui()
        pump(0.5)

    def drawers_off() -> None:
        for button in win.top_bar.nav_buttons.values():
            if button.isChecked():
                button.click()
        pump(0.2)

    def pretend_online(on: bool) -> None:
        win.connection_panel.set_connected(on)
        win.control_source_panel.set_leader_connected(on)
        win._follower_connected = win._leader_connected = on
        win.follower_torque_enabled = on
        if not on:
            win._leader_converted.clear()
        win._update_status_chips()

    # =============================================================== SO-101
    wait_for_twin()   # the SO-101 twin is bundled and loads on first launch
    joints = list(win.robot_profile.joint_order)
    ranges = dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
    stage_pose = {"shoulder_pan": 25, "shoulder_lift": 20, "elbow_flex": -30, "wrist_flex": 25, "wrist_roll": 20}
    pose(stage_pose, ranges)
    full("stage_so101")
    shots["topbar"] = _to_pil(win).crop((0, 0, W, 76))
    part("card_view", win.twin_panel.view_card)
    part("card_jog", win.stage.jog_card)
    part("card_dock", win.stage.dock_card)

    # leader on standby: ghost + alignment gate
    pretend_online(True)
    win.control_source_panel.leader_radio.click()
    pump(0.2)
    win._leader_converted.update({j: win.current_positions[j] + 32.0 for j in joints})
    win._update_teleop_ui()
    win._update_status_chips()
    full("gate_far")
    win._leader_converted.update({j: win.current_positions[j] + 2.0 for j in joints})
    win._update_teleop_ui()
    full("gate_ok")
    part("dock_gate", win.stage.dock_card)
    win.control_source_panel.manual_radio.click()
    pretend_online(False)

    # drawers
    win.waypoints = [
        {"label": "Waypoint 1", "positions": {**dict.fromkeys(joints, 0.0), "shoulder_pan": 30.0}, "dwell_ms": 0},
        {"label": "Waypoint 2 (grip)", "positions": {**dict.fromkeys(joints, 0.0), "shoulder_lift": 35.0}, "dwell_ms": 800},
        {"label": "Waypoint 3", "positions": {**dict.fromkeys(joints, 0.0), "elbow_flex": -35.0}, "dwell_ms": 0},
    ]
    win._refresh_waypoint_list()
    win.top_bar.nav_buttons["waypoints"].click()
    win.teaching_panel.list_widget.setCurrentRow(1)
    full("drawer_waypoints")
    win.top_bar.nav_buttons["telemetry"].click()
    telemetry = {
        j: {"position": 2048 + 90 * i, "velocity": 40 * (i % 3), "load": 60 + 25 * i, "current": 45 + 10 * i,
            "voltage": 74, "temperature": 33 + i}
        for i, j in enumerate(joints)
    }
    win._on_telemetry_updated(telemetry)
    full("drawer_telemetry")
    drawers_off()

    # cards folded into tabs
    win.stage.set_collapsed("view", True)
    win.stage.set_collapsed("jog", True)
    full("cards_tabs")
    win.stage.reset_layout()

    # setup hub
    win.top_bar.nav_buttons["setup"].click()
    for section, name in (("Motors and ids", "setup_so_ids"), ("Calibration", "setup_so_cal"),
                          ("Inputs", "setup_inputs"), ("Data and logs", "setup_logs")):
        win.setup_hub.show_section(section)
        full(name)
    win.top_bar.nav_buttons["setup"].click()

    # palette and about (dialogs are grabbed on their own)
    palette = CommandPalette(win)
    palette.set_actions(win._palette_actions())
    palette.search.setText("source")
    palette.show()
    pump(0.3)
    shots["palette"] = _to_pil(palette)
    palette.close()
    about = AboutDialog(win)
    about.show()
    pump(0.3)
    shots["about"] = _to_pil(about)
    about.close()

    # =============================================================== reBot B601-DM
    win.robot_combo.setCurrentIndex(win.robot_combo.findData("rebot_b601_dm"))
    wait_for_twin()
    joints = list(win.robot_profile.joint_order)
    ranges = dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
    pose({"joint1": 25, "joint2": 20, "joint3": -25, "joint4": 20, "joint5": 20}, ranges)
    full("stage_dm")
    part("card_dock_dm", win.stage.dock_card)

    win.top_bar.nav_buttons["tune"].click()
    panel = win.dm_tune_panel
    t0 = time.monotonic() - 9
    for i in range(270):
        goal = 0.0 if i < 80 else (12.0 if i < 200 else -3.0)
        x = (i - 80) / 30 if i < 200 else (i - 200) / 30
        if 80 <= i < 200:
            meas = 12 * (1 - math.exp(-4 * x) * math.cos(6 * x))
        elif i >= 200:
            meas = 12 - 15 * (1 - math.exp(-4 * x) * math.cos(6 * x))
        else:
            meas = 0.0
        panel.tracking_chart.add_sample(t0 + i / 30, goal, meas)
    panel.mit_gain_spins["joint4"][0].setValue(24.0)
    panel.save_preset("hold v2")
    panel._refresh_stats()
    full("drawer_tune")
    drawers_off()

    win.top_bar.nav_buttons["setup"].click()
    for section, name in (("Motors and ids", "setup_dm_ids"), ("Calibration", "setup_dm_cal")):
        win.setup_hub.show_section(section)
        full(name)

    win.close()
    return shots


if __name__ == "__main__":
    out = ROOT / "tools" / "_shots"
    out.mkdir(exist_ok=True)
    for key, image in capture().items():
        image.save(out / f"{key}.png")
    print("saved", out)
    sys.stdout.flush()
    os._exit(0)
