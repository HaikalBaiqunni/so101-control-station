"""Builds the layout-tour GIFs in docs/ from the real GUI, offscreen, no hardware.

    python tools/make_gifs.py              # both robots
    python tools/make_gifs.py so101        # one of them: so101 | b601

The tours walk the Workspace layout: the stage, the task rail, the tiles, the right panel and the Setup hub.
Things move: the arm sweeps through poses, the leader's ghost slides onto the arm until the alignment bar turns
green, the gripper closes on something and its width counts down until it latches, the camera pictures run.
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

GIF_WIDTH = 1000
COLORS = 224
MOVE_MS = 60        # one frame of motion (about 16 fps; the twin itself renders at about 15 fps)
MOVE_SETTLE = 0.085  # let the twin draw one new frame between two captures


def smooth(t: float) -> float:
    return t * t * (3 - 2 * t)   # ease in and out


class Tour:
    def __init__(self, session: Session):
        self.s = session
        self.frames: list[tuple[Image.Image, int]] = []

    def hold(self, ms: int, settle: float = 0.45) -> None:
        self.frames.append((self.s.frame(scale_to=GIF_WIDTH, settle=settle), ms))

    def animate(self, step, steps: int, ms: int = MOVE_MS, settle: float = MOVE_SETTLE) -> None:
        """Call step(t) for t in (0, 1] and capture a frame after each call."""
        for i in range(1, steps + 1):
            step(i / steps)
            self.frames.append((self.s.frame(scale_to=GIF_WIDTH, settle=settle), ms))

    def move(self, start: dict[str, float], end: dict[str, float], steps: int = 14) -> None:
        """The arm sweeping from one pose to another."""
        keys = set(start) | set(end)

        def step(t: float) -> None:
            k = smooth(t)
            self.s.pose({j: start.get(j, 0.0) + (end.get(j, 0.0) - start.get(j, 0.0)) * k for j in keys}, settle=0.0)

        self.animate(step, steps)

    def cameras(self, count: int, steps: int = 12) -> None:
        """Camera tiles with a moving test pattern, as if live."""
        self.s.feed_cameras(count)
        counter = [0]

        def step(_t: float) -> None:
            counter[0] += 3
            self.s.feed_cameras(count, shift=counter[0])

        self.animate(step, steps, ms=80, settle=0.05)

    def save(self, path: Path) -> None:
        # One palette for the whole GIF, built from a mosaic of thumbnails of every frame: a palette per frame
        # flickers on the twin render, and a palette from a single frame loses the other screens' colours.
        sample = [img.resize((img.width // 3, img.height // 3), Image.LANCZOS) for img, _ in self.frames[::2]]
        columns = 6
        rows = (len(sample) + columns - 1) // columns
        mosaic = Image.new("RGB", (sample[0].width * columns, sample[0].height * rows))
        for i, thumb in enumerate(sample):
            mosaic.paste(thumb, ((i % columns) * thumb.width, (i // columns) * thumb.height))
        palette = mosaic.quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        images = [img.quantize(palette=palette, dither=Image.Dither.NONE) for img, _ in self.frames]
        images[0].save(path, save_all=True, append_images=images[1:], duration=[ms for _, ms in self.frames],
                       loop=0, optimize=True, disposal=1)
        print(f"wrote {path} ({path.stat().st_size / 1e6:.2f} MB, {len(images)} frames)")
        sys.stdout.flush()


def so101_tour(out: Path) -> None:
    s = Session("workspace")
    win = s.win
    s.wait_for_twin()
    joints = list(win.robot_profile.joint_order)
    ranges = dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
    a = {"shoulder_pan": 25, "shoulder_lift": 20, "elbow_flex": -30, "wrist_flex": 25, "wrist_roll": 20}
    b = {"shoulder_pan": -30, "shoulder_lift": 45, "elbow_flex": -55, "wrist_flex": 10, "wrist_roll": -20}
    c = {"shoulder_pan": 10, "shoulder_lift": 5, "elbow_flex": -10, "wrist_flex": 40, "wrist_roll": 35}
    s.pose(a, ranges)
    tour = Tour(s)

    tour.hold(1100)                                     # Operate: twin, camera tile, Connection + Control source + Jog
    tour.move(a, b)
    tour.move(b, c)
    tour.move(c, a)
    tour.hold(400)

    s.pretend_online(True)                              # leader on standby: its ghost slides onto the arm
    win.control_source_panel.leader_radio.click()
    mine = dict(win.current_positions)

    def converge(t: float) -> None:
        offset = 32.0 - 30.0 * smooth(t)
        win._leader_converted.update({j: mine[j] + offset for j in joints})
        win._update_teleop_ui()
        win._update_status_chips()

    converge(0.0)
    tour.hold(1000)
    tour.animate(converge, 16, ms=70, settle=0.1)
    tour.hold(1200)
    win.control_source_panel.manual_radio.click()
    s.pretend_online(False)

    win.waypoints = [
        {"label": "Waypoint 1", "positions": {**dict.fromkeys(joints, 0.0), "shoulder_pan": 30.0}, "dwell_ms": 0},
        {"label": "Waypoint 2 (grip)", "positions": {**dict.fromkeys(joints, 0.0), "shoulder_lift": 35.0}, "dwell_ms": 800},
        {"label": "Waypoint 3", "positions": {**dict.fromkeys(joints, 0.0), "elbow_flex": -35.0}, "dwell_ms": 0},
    ]
    win._refresh_waypoint_list()
    s.mode("waypoints")                                 # Teach: the ghost jumps from waypoint to waypoint
    for row in (0, 1, 2):
        win.teaching_panel.list_widget.setCurrentRow(row)
        tour.hold(750)
    s.mode("record")                                    # Record
    tour.hold(900)
    s.mode("telemetry")                                 # Telemetry, with Jog folded out underneath
    win._on_telemetry_updated({
        j: {"position": 2048 + 90 * i, "velocity": 40 * (i % 3), "load": 60 + 25 * i, "current": 45 + 10 * i,
            "voltage": 74, "temperature": 33 + i} for i, j in enumerate(joints)})
    tour.hold(1200)
    win.workspace.jog_toggle.setChecked(True)
    tour.hold(1200)
    win.workspace.jog_toggle.setChecked(False)
    s.mode("operate")

    tour.cameras(2)                                     # tiles: two cameras running, then the twin expanded
    tour.hold(500, settle=0.1)
    win.workspace.twin_max_btn.setChecked(True)
    tour.hold(1100)
    win.workspace.restore_tiles()
    win.camera_panel.remove_last_slot()
    win.camera_panel.slot(0).mark_stopped()

    s.mode("setup")                                     # Setup hub
    for section in ("Motors and ids", "Calibration", "Inputs", "Data and logs"):
        win.setup_hub.show_section(section)
        tour.hold(900)
    s.mode("operate")
    tour.hold(800)
    tour.save(out)
    win.robot_worker = None
    win.close()


def b601_tour(out: Path) -> None:
    import math
    import time

    s = Session("workspace")
    win = s.win
    s.pick_robot("rebot_b601_dm")
    s.wait_for_twin()
    ranges = dict(win.joint_deg_ranges) or dict(win.robot_profile.preview_ranges)
    a = {"joint1": 25, "joint2": 20, "joint3": -25, "joint4": 20, "joint5": 20}
    b = {"joint1": -30, "joint2": 40, "joint3": -45, "joint4": 5, "joint5": -20}
    c = {"joint1": 10, "joint2": 5, "joint3": -10, "joint4": 40, "joint5": 35}
    s.pose({**a, "finger_left": 60}, ranges)
    tour = Tour(s)

    tour.hold(1100)                                     # Operate
    tour.move(a, b)
    tour.move(b, c)
    tour.move(c, a)
    tour.hold(400)

    s.mode("gauge")                                     # Measure: the gripper as a caliper closes on a block
    s.gauge_demo(holding=False)
    panel = win.gripper_gauge_panel

    def close_jaws(t: float) -> None:
        width = 40.0 - 16.6 * smooth(t)                 # 40 mm open, down to a 23.4 mm object
        text = f"{width:.1f} mm"
        s.pose({**a, "finger_left": width}, ranges, settle=0.0)   # the twin's gripper closes with it
        panel.set_live(text, "free", "Jaw opening")
        win.top_bar.set_width(text, "free", "Gripper")

    close_jaws(0.0)
    tour.hold(1000)
    tour.animate(close_jaws, 18, ms=70, settle=0.1)
    panel.set_live("23.4 mm", "grip", "Holding")        # it touches: the width latches, green
    win.top_bar.set_width("23.4 mm", "grip", "Holding")
    tour.hold(1600)
    panel.mode_buttons["normal"].click()                # Normal: follow the leader at full speed
    tour.hold(1100)
    panel.mode_buttons["measure"].click()
    panel.calibration_fold.set_open(True)
    tour.hold(1300)
    panel.calibration_fold.set_open(False)
    win.top_bar.set_width("")

    s.mode("record")
    tour.hold(900)
    s.mode("tune")                                      # Tune: MIT gains, presets, tracking chart
    tune = win.dm_tune_panel
    t0 = time.monotonic() - 9
    for i in range(270):
        goal = 0.0 if i < 80 else (12.0 if i < 200 else -3.0)
        x = (i - 80) / 30 if i < 200 else (i - 200) / 30
        meas = 12 * (1 - math.exp(-4 * x) * math.cos(6 * x)) if 80 <= i < 200 else (
            12 - 15 * (1 - math.exp(-4 * x) * math.cos(6 * x)) if i >= 200 else 0.0)
        tune.tracking_chart.add_sample(t0 + i / 30, goal, meas)
    tune._refresh_stats()
    tour.hold(1400)
    s.mode("telemetry")
    joints = list(win.robot_profile.joint_order)
    win._on_telemetry_updated({
        j: {"position": 2048 + 90 * i, "velocity": 40 * (i % 3), "load": 60 + 25 * i, "current": 45 + 10 * i,
            "voltage": 74, "temperature": 33 + i} for i, j in enumerate(joints)})
    tour.hold(1100)
    s.mode("operate")

    tour.cameras(2)
    tour.hold(500, settle=0.1)
    win.workspace.twin_max_btn.setChecked(True)
    tour.hold(1000)
    win.workspace.restore_tiles()

    s.mode("setup")
    for section in ("Motors and ids", "Calibration"):
        win.setup_hub.show_section(section)
        tour.hold(1000)
    s.mode("operate")
    tour.hold(800)
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
