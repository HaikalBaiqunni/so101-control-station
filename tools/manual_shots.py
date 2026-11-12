"""Screenshots for docs/MANUAL.html, taken from the real GUI, offscreen, no hardware.

    python tools/manual_shots.py            # writes tools/_shots/*.png (for a look)

`capture()` is what tools/make_manual.py calls. Everything runs against a scratch
working directory, so the developer's gui_settings.json / logs are never touched.
Some shots pretend both arms are online (chips, ghost, alignment) and show made-up
values (gauge readings, telemetry, a camera test pattern) to show the layout;
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
    os.environ.setdefault("MUJOCO_GL", "wgl")                      # same as main.py: the twin needs a GL backend
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

W, H = 1560, 900
FULL_WIDTH = 1280   # full-window shots are scaled to this


def to_pil(widget) -> Image.Image:
    image = widget.grab().toImage()
    rgb = image.convertToFormat(image.Format.Format_RGB888)
    return Image.frombuffer("RGB", (rgb.width(), rgb.height()), bytes(rgb.constBits()), "raw", "RGB", rgb.bytesPerLine())


def stack(images: list[Image.Image], gap: int = 10, background=(18, 22, 27)) -> Image.Image:
    width = max(i.width for i in images)
    out = Image.new("RGB", (width, sum(i.height for i in images) + gap * (len(images) - 1)), background)
    y = 0
    for image in images:
        out.paste(image, (0, y))
        y += image.height + gap
    return out


def test_pattern(width: int = 640, height: int = 400, shift: int = 0) -> np.ndarray:
    """A made-up camera frame (a colour gradient with a grid), so the camera tiles do not look empty."""
    x = np.linspace(0, 1, width)[None, :].repeat(height, 0)
    y = np.linspace(0, 1, height)[:, None].repeat(width, 1)
    frame = np.stack([(60 + 150 * x), (70 + 120 * y), (150 + 80 * np.sin((x + shift / 40) * 6))], axis=-1)
    frame[::40, :] = 235
    frame[:, ::40] = 235
    return np.clip(frame, 0, 255).astype(np.uint8)


class Session:
    """A MainWindow plus the helpers every capture script needs (shared with tools/make_gifs.py)."""

    def __init__(self, layout: str = "workspace"):
        os.chdir(tempfile.mkdtemp())   # scratch cwd: settings and logs land here, not in the repo
        from ui.main_window import MainWindow
        from ui.style import STYLE_SHEET

        self.app = QApplication.instance() or QApplication([])
        self.app.setStyleSheet(STYLE_SHEET)
        self.win = MainWindow(layout=layout)
        self.win.resize(W, H)
        self.win.show()

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait_for_twin(self) -> None:
        deadline = time.time() + 40
        self.win.twin_panel._last_pixmap = None
        while self.win.twin_panel._last_pixmap is None and time.time() < deadline:
            self.pump(0.2)
        self.pump(1.0)
        self.mask_paths()

    def mask_paths(self) -> None:
        """The twin caption shows an absolute path (a user name, a drive): show a relative one instead."""
        twin = self.win.twin_panel
        tail = "/".join(twin.path_edit.text().replace("\\", "/").split("/")[-2:])
        twin.set_caption(f"loaded: models/{tail}")

    def frame(self, scale_to: int | None = FULL_WIDTH, settle: float = 0.4) -> Image.Image:
        self.pump(settle)
        img = to_pil(self.win)
        if scale_to:
            img = img.resize((scale_to, round(img.height * scale_to / img.width)), Image.LANCZOS)
        return img

    def pose(self, degs: dict[str, float], ranges: dict | None = None, settle: float = 0.5) -> None:
        win = self.win
        ranges = ranges or dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
        for joint in win.robot_profile.joint_order:
            lo, hi = ranges.get(joint, (-90.0, 90.0))
            win.current_positions[joint] = max(lo, min(hi, degs.get(joint, 0.0)))
        win._refresh_ui()
        self.pump(settle)

    def pretend_online(self, on: bool) -> None:
        win = self.win
        win.connection_panel.set_connected(on)
        win.control_source_panel.set_leader_connected(on)
        win._follower_connected = win._leader_connected = on
        win.follower_torque_enabled = on
        if not on:
            win._leader_converted.clear()
        win._update_status_chips()

    def mode(self, key: str) -> None:
        """Pick a task on the rail (key: operate, gauge, waypoints, record, tune, telemetry, setup)."""
        self.win._on_rail_mode(key)
        self.pump(0.3)

    def pick_robot(self, key: str) -> None:
        self.win.robot_combo.setCurrentIndex(self.win.robot_combo.findData(key))
        self.pump(0.3)

    def feed_cameras(self, count: int, shift: int = 0) -> None:
        panel = self.win.camera_panel
        while len(panel.slots) < count:
            panel.add_slot()
        for i, slot in enumerate(panel.slots):
            slot.show_frame(test_pattern(shift=shift + 17 * i))
        self.pump(0.2)

    def gauge_demo(self, holding: bool = True) -> None:
        """Calibrated gripper gauge with a few made-up measurements (B601-DM)."""
        win = self.win
        win._gauge_cal.set_point(80.0, 0.0)
        win._gauge_cal.set_point(64.0, 10.0)
        win._gauge_cal.set_point(32.0, 30.0)
        win._refresh_gauge_ui()
        panel = win.gripper_gauge_panel
        for width, spread, angle in ((23.41, 0.03, 52.1), (23.38, 0.02, 52.2), (23.44, 0.04, 52.0)):
            panel.show_result(width, spread, angle, 3, [])
        state, caption = ("grip", "Holding") if holding else ("free", "Jaw opening")
        panel.set_live("23.4 mm", state, caption)
        win.top_bar.set_width("23.4 mm", state, "Holding" if holding else "Gripper")
        self.pump(0.3)


def capture() -> dict[str, Image.Image]:
    import math as _math  # noqa: F401  (kept for the tune chart below)

    from ui.about_dialog import AboutDialog
    from ui.command_palette import CommandPalette

    s = Session("workspace")
    win = s.win
    shots: dict[str, Image.Image] = {}

    def full(name: str) -> None:
        shots[name] = s.frame()

    # =============================================================== SO-101
    s.wait_for_twin()   # the SO-101 twin is bundled and loads on first launch
    joints = list(win.robot_profile.joint_order)
    ranges = dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
    s.pose({"shoulder_pan": 25, "shoulder_lift": 20, "elbow_flex": -30, "wrist_flex": 25, "wrist_roll": 20}, ranges)
    full("stage_so101")
    shots["topbar"] = to_pil(win).crop((0, 0, W, 76)).resize((FULL_WIDTH, round(76 * FULL_WIDTH / W)), Image.LANCZOS)
    shots["rail"] = to_pil(win.rail)

    # View options popup
    s.pump(0.2)
    button = win.workspace.view_btn
    win.workspace.view_menu.popup(button.mapToGlobal(QPoint(0, button.height())))
    s.pump(0.4)
    shots["card_view"] = to_pil(win.twin_panel.view_card)
    win.workspace.view_menu.hide()
    s.pump(0.2)

    shots["card_jog"] = to_pil(win.jog_panel)
    shots["card_dock"] = stack([to_pil(win.connection_panel), to_pil(win.control_source_panel)])

    # leader on standby: ghost + alignment gate
    s.pretend_online(True)
    win.control_source_panel.leader_radio.click()
    s.pump(0.2)
    win._leader_converted.update({j: win.current_positions[j] + 32.0 for j in joints})
    win._update_teleop_ui()
    win._update_status_chips()
    full("gate_far")
    win._leader_converted.update({j: win.current_positions[j] + 2.0 for j in joints})
    win._update_teleop_ui()
    full("gate_ok")
    shots["dock_gate"] = stack([to_pil(win.connection_panel), to_pil(win.control_source_panel)])
    win.control_source_panel.manual_radio.click()
    s.pretend_online(False)

    # tasks on the rail
    win.waypoints = [
        {"label": "Waypoint 1", "positions": {**dict.fromkeys(joints, 0.0), "shoulder_pan": 30.0}, "dwell_ms": 0},
        {"label": "Waypoint 2 (grip)", "positions": {**dict.fromkeys(joints, 0.0), "shoulder_lift": 35.0}, "dwell_ms": 800},
        {"label": "Waypoint 3", "positions": {**dict.fromkeys(joints, 0.0), "elbow_flex": -35.0}, "dwell_ms": 0},
    ]
    win._refresh_waypoint_list()
    s.mode("waypoints")
    win.teaching_panel.list_widget.setCurrentRow(1)
    full("drawer_waypoints")
    s.mode("telemetry")
    telemetry = {
        j: {"position": 2048 + 90 * i, "velocity": 40 * (i % 3), "load": 60 + 25 * i, "current": 45 + 10 * i,
            "voltage": 74, "temperature": 33 + i}
        for i, j in enumerate(joints)
    }
    win._on_telemetry_updated(telemetry)
    full("drawer_telemetry")
    win.workspace.jog_toggle.setChecked(True)
    full("jog_fold")
    win.workspace.jog_toggle.setChecked(False)
    s.mode("operate")

    # tiles: two cameras, then the twin expanded
    s.feed_cameras(2)
    full("cameras_two")
    win.workspace.twin_max_btn.setChecked(True)
    full("twin_expanded")
    win.workspace.restore_tiles()
    win.camera_panel.remove_last_slot()
    win.camera_panel.slot(0).mark_stopped()   # the made-up test pattern must not follow us to the next robot

    # setup hub
    s.mode("setup")
    for section, name in (("Motors and ids", "setup_so_ids"), ("Calibration", "setup_so_cal"),
                          ("Inputs", "setup_inputs"), ("Data and logs", "setup_logs")):
        win.setup_hub.show_section(section)
        full(name)
    s.mode("operate")

    # palette and about (dialogs are grabbed on their own)
    palette = CommandPalette(win)
    palette.set_actions(win._palette_actions())
    palette.search.setText("source")
    palette.show()
    s.pump(0.3)
    shots["palette"] = to_pil(palette)
    palette.close()
    about = AboutDialog(win)
    about.show()
    s.pump(0.3)
    shots["about"] = to_pil(about)
    about.close()

    # =============================================================== reBot B601-DM
    s.pick_robot("rebot_b601_dm")
    s.wait_for_twin()
    ranges = dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
    s.pose({"joint1": 25, "joint2": 20, "joint3": -25, "joint4": 20, "joint5": 20}, ranges)
    full("stage_dm")
    shots["card_dock_dm"] = stack([to_pil(win.connection_panel), to_pil(win.control_source_panel)])

    s.mode("tune")
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

    # the gripper as a caliper
    s.mode("gauge")
    s.gauge_demo(holding=True)
    full("tool_measure_holding")
    win.gripper_gauge_panel.calibration_fold.set_open(True)
    win.gripper_gauge_panel.settings_fold.set_open(True)
    shots["tool_measure_folds"] = to_pil(win.gripper_gauge_panel)
    win.gripper_gauge_panel.calibration_fold.set_open(False)
    win.gripper_gauge_panel.settings_fold.set_open(False)

    s.mode("record")
    full("tool_record")
    s.mode("operate")

    s.mode("setup")
    for section, name in (("Motors and ids", "setup_dm_ids"), ("Calibration", "setup_dm_cal")):
        win.setup_hub.show_section(section)
        full(name)
    s.mode("operate")

    # the Classic layout, for the manual's note on it
    win.robot_worker = None
    win.close()
    classic = Session("classic")
    classic.pick_robot("so101")
    classic.wait_for_twin()
    classic.pose({"shoulder_pan": 25, "shoulder_lift": 20, "elbow_flex": -30, "wrist_flex": 25, "wrist_roll": 20})
    shots["stage_classic"] = classic.frame()
    classic.win.robot_worker = None
    classic.win.close()
    return shots


if __name__ == "__main__":
    out = ROOT / "tools" / "_shots"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    for key, image in capture().items():
        image.save(out / f"{key}.png")
    print("saved", out)
    sys.stdout.flush()
    os._exit(0)
