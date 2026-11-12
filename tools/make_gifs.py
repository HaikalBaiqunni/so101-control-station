"""Builds the layout-tour GIFs in docs/ from the real GUI, offscreen, no hardware.

    python tools/make_gifs.py              # both robots
    python tools/make_gifs.py so101        # one of them: so101 | b601

The tours walk the Workspace layout: the stage, the task rail, the tiles, the right panel and the Setup hub.
Connection state, leader poses, gauge readings, telemetry and the camera picture are made up to show the layout
(nothing here talks to hardware), as the README says. Everything runs in a scratch working directory.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from manual_shots import Session  # noqa: E402
from PIL import Image  # noqa: E402

GIF_WIDTH = 1100
COLORS = 160


class Tour:
    def __init__(self, session: Session):
        self.s = session
        self.frames: list[tuple[Image.Image, int]] = []

    def hold(self, ms: int, settle: float = 0.45) -> None:
        self.frames.append((self.s.frame(scale_to=GIF_WIDTH, settle=settle), ms))

    def move(self, start: dict[str, float], end: dict[str, float], steps: int = 8, ms: int = 110) -> None:
        """The arm sweeping from one pose to another: one frame per step."""
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)   # ease in and out
            pose = {j: start.get(j, 0.0) + (end.get(j, 0.0) - start.get(j, 0.0)) * t for j in set(start) | set(end)}
            self.s.pose(pose, settle=0.14)
            self.frames.append((self.s.frame(scale_to=GIF_WIDTH, settle=0.02), ms))

    def save(self, path: Path) -> None:
        # one palette per frame: the twin render and the dark UI do not share colours well
        images = [img.quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
                  for img, _ in self.frames]
        images[0].save(path, save_all=True, append_images=images[1:], duration=[ms for _, ms in self.frames],
                       loop=0, optimize=True, disposal=1)
        print(f"wrote {path} ({path.stat().st_size / 1e6:.2f} MB, {len(images)} frames)")
        sys.stdout.flush()


def so101_tour(out: Path) -> None:
    s = Session("workspace")
    win, tour = s.win, None
    s.wait_for_twin()
    joints = list(win.robot_profile.joint_order)
    ranges = dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
    a = {"shoulder_pan": 25, "shoulder_lift": 20, "elbow_flex": -30, "wrist_flex": 25, "wrist_roll": 20}
    b = {"shoulder_pan": -30, "shoulder_lift": 45, "elbow_flex": -55, "wrist_flex": 10, "wrist_roll": -20}
    s.pose(a, ranges)
    tour = Tour(s)

    tour.hold(1500)                                     # Operate: twin, camera tile, Connection + Control source + Jog
    tour.move(a, b)
    tour.hold(700)
    s.pretend_online(True)                              # leader on standby: the ghost and the alignment gate
    win.control_source_panel.leader_radio.click()
    win._leader_converted.update({j: win.current_positions[j] + 32.0 for j in joints})
    win._update_teleop_ui()
    win._update_status_chips()
    tour.hold(1500)
    win._leader_converted.update({j: win.current_positions[j] + 2.0 for j in joints})
    win._update_teleop_ui()
    tour.hold(1500)
    win.control_source_panel.manual_radio.click()
    s.pretend_online(False)

    win.waypoints = [
        {"label": "Waypoint 1", "positions": {**dict.fromkeys(joints, 0.0), "shoulder_pan": 30.0}, "dwell_ms": 0},
        {"label": "Waypoint 2 (grip)", "positions": {**dict.fromkeys(joints, 0.0), "shoulder_lift": 35.0}, "dwell_ms": 800},
        {"label": "Waypoint 3", "positions": {**dict.fromkeys(joints, 0.0), "elbow_flex": -35.0}, "dwell_ms": 0},
    ]
    win._refresh_waypoint_list()
    s.mode("waypoints")                                 # Teach
    win.teaching_panel.list_widget.setCurrentRow(1)
    tour.hold(1600)
    s.mode("record")                                    # Record
    tour.hold(1400)
    s.mode("telemetry")                                 # Telemetry, with Jog folded out underneath
    win._on_telemetry_updated({
        j: {"position": 2048 + 90 * i, "velocity": 40 * (i % 3), "load": 60 + 25 * i, "current": 45 + 10 * i,
            "voltage": 74, "temperature": 33 + i} for i, j in enumerate(joints)})
    tour.hold(1500)
    win.workspace.jog_toggle.setChecked(True)
    tour.hold(1500)
    win.workspace.jog_toggle.setChecked(False)
    s.mode("operate")

    s.feed_cameras(2)                                   # tiles: two cameras, then the twin expanded
    tour.hold(1600)
    win.workspace.twin_max_btn.setChecked(True)
    tour.hold(1400)
    win.workspace.restore_tiles()
    win.camera_panel.remove_last_slot()
    win.camera_panel.slot(0).mark_stopped()

    s.mode("setup")                                     # Setup hub
    for section in ("Motors and ids", "Calibration", "Inputs", "Data and logs"):
        win.setup_hub.show_section(section)
        tour.hold(1200)
    s.mode("operate")
    tour.hold(1000)
    tour.save(out)
    win.robot_worker = None
    win.close()


def b601_tour(out: Path) -> None:
    s = Session("workspace")
    win = s.win
    s.pick_robot("rebot_b601_dm")
    s.wait_for_twin()
    ranges = dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
    a = {"joint1": 25, "joint2": 20, "joint3": -25, "joint4": 20, "joint5": 20}
    b = {"joint1": -30, "joint2": 40, "joint3": -45, "joint4": 5, "joint5": -20}
    s.pose(a, ranges)
    tour = Tour(s)

    tour.hold(1500)                                     # Operate
    tour.move(a, b)
    tour.hold(700)

    s.mode("gauge")                                     # Measure: the gripper as a caliper
    s.gauge_demo(holding=False)
    win.top_bar.set_width("31.8 mm", "free", "Gripper")
    win.gripper_gauge_panel.set_live("31.8 mm", "free", "Jaw opening")
    tour.hold(1500)
    s.gauge_demo(holding=True)                          # the jaw touches something: the width latches, green
    tour.hold(1800)
    win.gripper_gauge_panel.mode_buttons["normal"].click()   # Normal: follow the leader at full speed
    tour.hold(1400)
    win.gripper_gauge_panel.mode_buttons["measure"].click()
    win.gripper_gauge_panel.calibration_fold.set_open(True)
    tour.hold(1500)
    win.gripper_gauge_panel.calibration_fold.set_open(False)

    s.mode("record")
    tour.hold(1300)
    s.mode("tune")                                      # Tune: MIT gains, presets, tracking chart
    panel = win.dm_tune_panel
    import math
    import time
    t0 = time.monotonic() - 9
    for i in range(270):
        goal = 0.0 if i < 80 else (12.0 if i < 200 else -3.0)
        x = (i - 80) / 30 if i < 200 else (i - 200) / 30
        meas = 12 * (1 - math.exp(-4 * x) * math.cos(6 * x)) if 80 <= i < 200 else (
            12 - 15 * (1 - math.exp(-4 * x) * math.cos(6 * x)) if i >= 200 else 0.0)
        panel.tracking_chart.add_sample(t0 + i / 30, goal, meas)
    panel._refresh_stats()
    tour.hold(1700)
    s.mode("telemetry")
    joints = list(win.robot_profile.joint_order)
    win._on_telemetry_updated({
        j: {"position": 2048 + 90 * i, "velocity": 40 * (i % 3), "load": 60 + 25 * i, "current": 45 + 10 * i,
            "voltage": 74, "temperature": 33 + i} for i, j in enumerate(joints)})
    tour.hold(1400)
    s.mode("operate")

    s.feed_cameras(2)
    tour.hold(1600)
    win.workspace.twin_max_btn.setChecked(True)
    tour.hold(1300)
    win.workspace.restore_tiles()

    s.mode("setup")
    for section in ("Motors and ids", "Calibration"):
        win.setup_hub.show_section(section)
        tour.hold(1300)
    s.mode("operate")
    tour.hold(1000)
    tour.save(out)
    win.robot_worker = None
    win.close()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    docs = ROOT / "docs"
    if which in ("all", "so101"):
        so101_tour(docs / "layout_tour_so101.gif")
    if which in ("all", "b601"):
        b601_tour(docs / "layout_tour_b601_dm.gif")
    os._exit(0)
