from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.calibration_worker import CalibrationWorker
from core.servo_bus import JOINT_ORDER
from core.session_logger import SessionLogger
from core.twin_worker import TwinWorker
from core.workers import CameraWorker, GamepadWorker, RobotWorker

from .calibration_panel import CalibrationPanel
from .camera_panel import CameraPanel
from .connection_panel import ConnectionPanel
from .control_source_panel import ControlSourcePanel
from .gamepad_panel import DEFAULT_AXIS_MAP, DEFAULT_BUTTON_MAP, GamepadPanel
from .joint_panel import JointPanel
from .teaching_panel import TeachingPanel
from .twin_panel import TwinPanel

GAMEPAD_TICK_MS = 33          # ~30 Hz jog integration
GAMEPAD_DEADZONE = 0.15
GAMEPAD_MAX_DEG_PER_S = 45.0  # full stick deflection = 45 deg/s

PLAYBACK_TICK_MS = 33
PLAYBACK_ARRIVE_TOLERANCE_DEG = 3.0
PLAYBACK_DEG_PER_S = 60.0    # smooth playback speed cap - a raw single goal jump lets the
                             # servo firmware move at whatever its max speed is (jerky/scary);
                             # interpolating our own trajectory at a capped rate is what tames it
PLAYBACK_MIN_MOVE_S = 0.3    # floor so a near-zero-distance waypoint doesn't collapse to 0s
PLAYBACK_SETTLE_GRACE_S = 1.5  # extra time allowed after the interpolated move finishes, in case
                                # the real arm lags slightly behind the commanded trajectory
PLAYBACK_MAX_DWELL_S = 4.0  # move on even if a waypoint is never reached exactly (stall/near a limit)

# The sliders/spinboxes are the expensive part of the UI (custom QSS styling
# makes each setValue() a real repaint, ~5ms for all 6 together) - raw
# position data arrives from the robot worker(s) at 60Hz each, but the visible
# display only needs to refresh at a much lower, BOUNDED rate. Decoupling
# "data arrives" from "widgets repaint" is what keeps the main thread from
# falling behind when both a leader and a follower are streaming at once.
UI_REFRESH_MS = 33  # ~30 Hz display refresh, independent of control-loop rate


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SO-101 Control Station")
        self.resize(1560, 880)

        # ================================================================ CONTROL TAB
        self.connection_panel = ConnectionPanel()
        self.control_source_panel = ControlSourcePanel()
        self.joint_panel = JointPanel()
        self.teaching_panel = TeachingPanel()
        self.gamepad_panel = GamepadPanel()
        self.twin_panel = TwinPanel()
        self.camera_panel = CameraPanel()

        left = QVBoxLayout()
        left.addWidget(self.connection_panel)
        left.addWidget(self.control_source_panel)
        left.addWidget(self.joint_panel)
        left.addWidget(self.teaching_panel)
        left.addWidget(self.gamepad_panel)
        left.addStretch(1)
        left_widget = QWidget()
        left_widget.setLayout(left)

        # a narrow, fixed-ish control column + digital twin/camera side by
        # side (not stacked) - comparing "is the twin doing what the camera
        # shows" is much easier glancing left-right than scrolling up-down.
        # QSplitter (not a plain grid) so the user can also just drag to
        # resize instead of living with whatever ratio I hardcode.
        left_scroll = QScrollArea()
        left_scroll.setWidget(left_widget)
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(360)  # enough for "Refresh"/"Browse"/"DISCONNECTED" to not clip

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(self.twin_panel)
        splitter.addWidget(self.camera_panel)
        splitter.setStretchFactor(0, 0)   # controls: stay narrow
        splitter.setStretchFactor(1, 2)   # digital twin: the star of the show
        splitter.setStretchFactor(2, 1)   # camera: secondary, for comparison
        splitter.setSizes([380, 760, 420])

        control_tab = QWidget()
        control_layout = QVBoxLayout(control_tab)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.addWidget(splitter)

        # ================================================================ CALIBRATION TAB
        self.calibration_panel = CalibrationPanel()

        tabs = QTabWidget()
        tabs.addTab(control_tab, "Control")
        tabs.addTab(self.calibration_panel, "Calibration")
        self.setCentralWidget(tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

        self.session_logger = SessionLogger()
        self.statusBar().showMessage(f"Ready. Logging to {self.session_logger.path}")

        # -- state -------------------------------------------------------
        self.robot_worker: RobotWorker | None = None
        self.leader_worker: RobotWorker | None = None
        self.calibration_worker: CalibrationWorker | None = None
        self.camera_worker: CameraWorker | None = None
        self.gamepad_worker: GamepadWorker | None = None
        self.twin_worker: TwinWorker | None = None
        self.current_positions: dict[str, float] = dict.fromkeys(JOINT_ORDER, 0.0)
        # (lo, hi) in degrees, from whichever calibration is currently loaded -
        # used to turn a raw degree value into "fraction of real travel" for
        # the twin, since the twin's own MJCF zero-reference doesn't line up
        # with an arbitrary calibration's (see DigitalTwin.set_joint_fraction).
        self.joint_deg_ranges: dict[str, tuple[float, float]] = {}
        # leader's OWN calibrated range, kept separately from joint_deg_ranges
        # (which is the FOLLOWER's/twin's range) - needed to reconcile the two
        # scales during teleop relay, see _on_leader_positions.
        self.leader_deg_ranges: dict[str, tuple[float, float]] = {}
        self.control_source: str = "manual"
        self._last_gamepad_axes: dict[int, float] = {}
        self._last_tick = time.monotonic()
        # Workers mid-shutdown: stop() only flips a flag, the OS thread is
        # still alive for a bit after that. Dropping the last Python
        # reference to a QThread while its thread is still running makes
        # PySide6 destroy it out from under itself - a hard Qt abort, not a
        # graceful no-op. Holding a reference here until `finished` actually
        # fires is what prevents that (self-referencing lambda closures rely
        # on a GC cycle collection with no defined timing - not good enough).
        self._shutting_down_workers: list = []

        # -- state: teaching (record/playback waypoints) --------------------
        self.waypoints: list[dict] = []  # [{"label": str, "positions": {joint: deg}}]
        self._playback_index: int | None = None
        self._playback_start_positions: dict[str, float] = {}
        self._playback_target_positions: dict[str, float] = {}
        self._playback_move_start = 0.0
        self._playback_move_duration = PLAYBACK_MIN_MOVE_S
        self._playback_deadline = 0.0
        self.playback_timer = QTimer(self)
        self.playback_timer.timeout.connect(self._playback_tick)

        # -- wiring: robot connection --------------------------------------
        self.connection_panel.connect_requested.connect(self._on_connect)
        self.connection_panel.disconnect_requested.connect(self._on_disconnect)
        self.connection_panel.torque_requested.connect(self._on_torque)
        self.joint_panel.goal_changed.connect(self._on_goal_changed)

        # -- wiring: control source / teleoperation -------------------------
        self.control_source_panel.source_changed.connect(self._on_control_source_changed)
        self.control_source_panel.leader_connect_requested.connect(self._on_leader_connect)
        self.control_source_panel.leader_disconnect_requested.connect(self._on_leader_disconnect)

        # -- wiring: camera --------------------------------------------------
        self.camera_panel.start_requested.connect(self._on_camera_start)
        self.camera_panel.stop_requested.connect(self._on_camera_stop)

        # -- wiring: gamepad ---------------------------------------------------
        self.gamepad_panel.enable_toggled.connect(self._on_gamepad_toggled)

        # -- wiring: teaching (waypoints) ---------------------------------------
        self.teaching_panel.record_requested.connect(self._on_record_waypoint)
        self.teaching_panel.delete_requested.connect(self._on_delete_waypoint)
        self.teaching_panel.move_up_requested.connect(lambda: self._on_move_waypoint(-1))
        self.teaching_panel.move_down_requested.connect(lambda: self._on_move_waypoint(1))
        self.teaching_panel.play_requested.connect(self._on_play_sequence)
        self.teaching_panel.stop_requested.connect(self._on_stop_sequence)
        self.teaching_panel.save_requested.connect(self._on_save_waypoints)
        self.teaching_panel.load_requested.connect(self._on_load_waypoints)

        # -- wiring: digital twin -----------------------------------------------
        self.twin_panel.load_requested.connect(self._on_twin_load)

        # -- wiring: calibration -------------------------------------------------
        self.calibration_panel.connect_requested.connect(self._on_calibration_connect)
        self.calibration_panel.disconnect_requested.connect(self._on_calibration_disconnect)
        self.calibration_panel.reset_requested.connect(self._on_calibration_reset)
        self.calibration_panel.set_middle_requested.connect(self._on_calibration_set_middle)
        self.calibration_panel.start_recording_requested.connect(self._on_calibration_start_recording)
        self.calibration_panel.stop_recording_requested.connect(self._on_calibration_stop_recording)
        self.calibration_panel.finish_requested.connect(self._on_calibration_finish)

        self.ui_refresh_timer = QTimer(self)
        self.ui_refresh_timer.timeout.connect(self._refresh_ui)
        self.ui_refresh_timer.start(UI_REFRESH_MS)

        self.gamepad_timer = QTimer(self)
        self.gamepad_timer.timeout.connect(self._gamepad_tick)

        self._apply_control_source_lock()

    # ---------------------------------------------------------------- shutdown helper
    def _retire_worker(self, worker) -> None:
        """Stop a QThread worker without blocking the GUI thread and without
        letting it get garbage-collected while its OS thread is still alive
        (see _shutting_down_workers)."""
        self._shutting_down_workers.append(worker)

        def _on_finished():
            if worker in self._shutting_down_workers:
                self._shutting_down_workers.remove(worker)

        worker.finished.connect(_on_finished)
        worker.stop()

    # ---------------------------------------------------------------- robot (follower / target)
    def _on_connect(self, port: str, calibration_path: str) -> None:
        if not port:
            QMessageBox.warning(self, "No port", "Pick a serial port first.")
            return
        if not calibration_path:
            QMessageBox.warning(
                self, "No calibration",
                "A LeRobot calibration .json is required - this app refuses to "
                "move a joint it doesn't know the safe range for.",
            )
            return

        self.session_logger.log_event(f"Follower: connecting on {port} (calibration: {calibration_path})")
        self.robot_worker = RobotWorker(port, calibration_path)
        self.robot_worker.positions_updated.connect(self._on_positions_updated)
        self.robot_worker.error.connect(self._on_robot_error)
        self.robot_worker.connection_changed.connect(self._on_connection_changed)
        self.robot_worker.start()

    def _on_disconnect(self) -> None:
        # Never QThread.wait() here: on a flaky/settling USB connection, a
        # read or write can sit blocked for a while inside the SDK before it
        # times out on its own - waiting for that on the GUI thread is a
        # freeze for however long that takes (this is the same class of bug
        # the camera worker had). _retire_worker() stops it without blocking
        # and without letting it get GC'd mid-shutdown.
        if self.robot_worker:
            worker = self.robot_worker
            self.robot_worker = None
            self.session_logger.log_event(f"Follower: disconnecting from {worker.port}")
            self._retire_worker(worker)
        self.connection_panel.set_connected(False)

    def _on_connection_changed(self, connected: bool) -> None:
        self.connection_panel.set_connected(connected)
        if connected:
            self.statusBar().showMessage(f"Connected to {self.robot_worker.port}")
            self.session_logger.log_event(f"Follower: connected on {self.robot_worker.port}")
            self._load_joint_limits()
        else:
            self.statusBar().showMessage("Disconnected.")
            self.session_logger.log_event("Follower: disconnected")

    def _load_joint_limits(self) -> None:
        if not self.robot_worker or not self.robot_worker.bus:
            return
        for name in JOINT_ORDER:
            lo, hi = self.robot_worker.bus.deg_limits(name)
            self.joint_panel.set_limits(name, lo, hi)
            self.joint_deg_ranges[name] = (lo, hi)

    def _on_robot_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Robot error: {message}")
        self.session_logger.log_event(f"ERROR: {message}")

    def _on_torque(self, enabled: bool) -> None:
        if self.robot_worker:
            self.robot_worker.request_torque(enabled)
            self.session_logger.log_event(f"Follower: torque {'ENABLED' if enabled else 'disabled'}")

    def _on_positions_updated(self, positions: dict[str, float]) -> None:
        """Cheap on purpose - just a dict merge. Runs at the robot worker's
        full 60Hz; widget/twin refresh happens separately at a bounded rate
        via _refresh_ui(), so this can never fall behind."""
        self.current_positions.update(positions)
        self.session_logger.log_positions("follower", positions)  # internally throttled to 2Hz

    def _on_goal_changed(self, name: str, degrees: float) -> None:
        """Fired by a slider/spinbox edit - only takes effect in Manual mode
        (sliders are disabled in the other modes, but this guard is cheap
        insurance against anything re-enabling them)."""
        if self.control_source != "manual" or self._playback_index is not None:
            return
        self.current_positions[name] = degrees
        if self.robot_worker:
            self.robot_worker.request_goal(name, degrees)

    # ---------------------------------------------------------------- control source / teleoperation
    def _on_control_source_changed(self, source: str) -> None:
        self.control_source = source
        self._apply_control_source_lock()
        self.statusBar().showMessage(f"Control source: {source}")
        self.session_logger.log_event(f"Control source changed to: {source}")

    def _apply_control_source_lock(self) -> None:
        manual = self.control_source == "manual"
        for row in self.joint_panel.rows.values():
            row.slider.setEnabled(manual)
            row.spin.setEnabled(manual)

    def _on_leader_connect(self, port: str, calibration_path: str) -> None:
        if not port or not calibration_path:
            QMessageBox.warning(self, "Missing info", "Leader needs both a port and a calibration file.")
            return
        self.session_logger.log_event(f"Leader: connecting on {port} (calibration: {calibration_path})")
        self.leader_worker = RobotWorker(port, calibration_path)
        self.leader_worker.positions_updated.connect(self._on_leader_positions)
        self.leader_worker.error.connect(self._on_robot_error)
        self.leader_worker.connection_changed.connect(self._on_leader_connection_changed)
        self.leader_worker.start()

    def _on_leader_disconnect(self) -> None:
        if self.leader_worker:
            worker = self.leader_worker
            self.leader_worker = None
            self.session_logger.log_event(f"Leader: disconnecting from {worker.port}")
            self._retire_worker(worker)
        self.control_source_panel.set_leader_connected(False)

    def _on_leader_connection_changed(self, connected: bool) -> None:
        self.control_source_panel.set_leader_connected(connected)
        if connected and self.leader_worker:
            # the leader is meant to be moved by hand - always free-spinning
            self.leader_worker.request_torque(False)
            self.statusBar().showMessage(f"Leader connected on {self.leader_worker.port}")
            self.session_logger.log_event(f"Leader: connected on {self.leader_worker.port}")
            if self.leader_worker.bus:
                for name in JOINT_ORDER:
                    self.leader_deg_ranges[name] = self.leader_worker.bus.deg_limits(name)
            # only source joint_deg_ranges (follower's/twin's) from the leader
            # if the follower isn't already providing them - follower's own
            # calibration is what actually matters once both are connected.
            if not self.robot_worker and self.leader_worker.bus:
                for name in JOINT_ORDER:
                    self.joint_deg_ranges[name] = self.leader_worker.bus.deg_limits(name)
        else:
            self.session_logger.log_event("Leader: disconnected")

    def _on_leader_positions(self, positions: dict[str, float]) -> None:
        """Live feed from the leader arm, at its own full 60Hz.

        When actively teleoperating, the follower's OWN feedback loop
        (_on_positions_updated) is what updates current_positions/the
        display - it's the ground truth of what's actually happening
        physically. This handler's only job then is to relay goals, as
        cheaply as possible. If there's no follower connected yet, fall back
        to showing the leader's own pose so the twin/sliders still preview
        something."""
        self.session_logger.log_positions("leader", positions)  # internally throttled to 2Hz
        if self.control_source == "leader" and self.robot_worker and self._playback_index is None:
            for name, degrees in positions.items():
                self.robot_worker.request_goal(name, self._leader_deg_to_follower_deg(name, degrees))
        else:
            self.current_positions.update(positions)

    def _leader_deg_to_follower_deg(self, name: str, leader_degrees: float) -> float:
        """Leader and follower are each calibrated independently - their
        degree scales don't necessarily agree on how far "fully closed" is
        (confirmed: squeezing the leader gripper all the way didn't drive the
        follower/twin to their own fully-closed extreme). Relaying raw
        degrees 1:1 silently assumes they do agree. Converting through
        "fraction of the leader's OWN range" -> "same fraction of the
        follower's OWN range" makes leader-fully-closed always mean
        follower-fully-closed, regardless of how the two calibrations differ
        in magnitude."""
        leader_lo, leader_hi = self.leader_deg_ranges.get(name, (-180.0, 180.0))
        follower_lo, follower_hi = self.joint_deg_ranges.get(name, (-180.0, 180.0))
        if leader_hi <= leader_lo or follower_hi <= follower_lo:
            return leader_degrees
        fraction = (leader_degrees - leader_lo) / (leader_hi - leader_lo)
        fraction = max(0.0, min(1.0, fraction))
        return follower_lo + fraction * (follower_hi - follower_lo)

    # ---------------------------------------------------------------- camera
    #
    # Deliberately never calls QThread.wait() from the GUI thread: that blocks
    # the whole UI for however long cap.release() takes (which is unbounded on
    # some USB webcam drivers - the old 2-second timed wait() would silently
    # give up and let a second camera open on top of a first one that hadn't
    # actually finished closing yet, which is exactly the "glitch when
    # switching" bug). QThread's own `finished` signal fires the instant the
    # thread really is done, with no arbitrary timeout to blow past, and
    # doesn't block anything while we wait for it.
    def _on_camera_start(self, index: int) -> None:
        self.camera_panel.set_busy(True, f"starting device #{index}...")
        worker = CameraWorker(index)
        worker.frame_ready.connect(self.camera_panel.show_frame)
        worker.error.connect(self.camera_panel.show_error)
        worker.started_ok.connect(lambda: self.camera_panel.set_busy(False))
        self.camera_worker = worker
        worker.start()

    def _on_camera_stop(self) -> None:
        if not self.camera_worker:
            return
        self.camera_panel.set_busy(True, "stopping...")
        worker = self.camera_worker
        self.camera_worker = None
        worker.finished.connect(lambda: self.camera_panel.set_busy(False))
        self._retire_worker(worker)  # just flips a flag - the thread notices and exits on its own

    # ---------------------------------------------------------------- gamepad
    def _on_gamepad_toggled(self, enabled: bool) -> None:
        if enabled:
            self.gamepad_worker = GamepadWorker()
            self.gamepad_worker.axes_updated.connect(self._on_gamepad_axes)
            self.gamepad_worker.button_pressed.connect(self._on_gamepad_button)
            self.gamepad_worker.no_gamepad.connect(lambda: self.gamepad_panel.set_connected(False))
            self.gamepad_worker.start()
            self.gamepad_panel.set_connected(True)
            self._last_tick = time.monotonic()
            self.gamepad_timer.start(GAMEPAD_TICK_MS)
        else:
            self.gamepad_timer.stop()
            if self.gamepad_worker:
                worker = self.gamepad_worker
                self.gamepad_worker = None
                self._retire_worker(worker)
            self.gamepad_panel.set_connected(False)

    def _on_gamepad_axes(self, axes: dict[int, float]) -> None:
        self._last_gamepad_axes = axes

    def _on_gamepad_button(self, index: int) -> None:
        if self.control_source != "gamepad":
            return
        mapping = DEFAULT_BUTTON_MAP.get(index)
        if not mapping:
            return
        name, delta = mapping
        new_deg = self.current_positions.get(name, 0.0) + delta
        self._drive_joint_programmatically(name, new_deg)

    def _gamepad_tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        if self.control_source != "gamepad" or self._playback_index is not None:
            return
        for axis_idx, (name, sign) in DEFAULT_AXIS_MAP.items():
            value = self._last_gamepad_axes.get(axis_idx, 0.0)
            if abs(value) < GAMEPAD_DEADZONE:
                continue
            delta = sign * value * GAMEPAD_MAX_DEG_PER_S * dt
            new_deg = self.current_positions.get(name, 0.0) + delta
            self._drive_joint_programmatically(name, new_deg)

    def _drive_joint_programmatically(self, name: str, degrees: float) -> None:
        """Used by gamepad/leader-relay paths that bypass the (disabled)
        slider widgets directly - updates state + sends the goal."""
        self.current_positions[name] = degrees
        if self.robot_worker:
            self.robot_worker.request_goal(name, degrees)
        row = self.joint_panel.rows.get(name)
        if row:
            row.set_feedback_deg(degrees)

    # ---------------------------------------------------------------- UI refresh (bounded rate)
    def _refresh_ui(self) -> None:
        """The ONLY place sliders/spinboxes and the twin actually repaint.
        Runs at a fixed ~30Hz regardless of how fast position data is
        arriving from one or two robot workers - this is what keeps the main
        thread from ever falling behind."""
        self.joint_panel.update_feedback(self.current_positions)
        if self.twin_worker:
            self.twin_worker.set_fractions(self._positions_to_fractions())

    def _positions_to_fractions(self) -> dict[str, float]:
        """Real degrees -> 0..1 fraction of that joint's OWN calibrated
        range, so the twin can map it into the MJCF's own joint range instead
        of assuming the two zero-references happen to agree (they don't, at
        least not for the gripper - see DigitalTwin.set_joint_fraction)."""
        fractions = {}
        for name, degrees in self.current_positions.items():
            lo, hi = self.joint_deg_ranges.get(name, (-180.0, 180.0))
            if hi <= lo:
                continue
            fractions[name] = (degrees - lo) / (hi - lo)
        return fractions

    # ---------------------------------------------------------------- digital twin
    def _on_twin_load(self, path: str) -> None:
        if not path:
            return
        if self.twin_worker:
            old_worker = self.twin_worker
            self.twin_worker = None
            old_worker.finished.connect(lambda: self._start_twin_worker(path))
            self._retire_worker(old_worker)
        else:
            self._start_twin_worker(path)

    def _start_twin_worker(self, path: str) -> None:
        self.twin_worker = TwinWorker(path)
        self.twin_worker.frame_ready.connect(self.twin_panel.show_frame)
        self.twin_worker.load_failed.connect(lambda msg: self.twin_panel.set_caption(f"failed to load: {msg}"))
        self.twin_worker.neutral_pose_ready.connect(self._on_neutral_pose_ready)
        self.twin_worker.start()
        self.twin_panel.set_caption(f"loaded: {path}")

    # ---------------------------------------------------------------- calibration tab
    def _on_calibration_connect(self, port: str) -> None:
        if not port:
            QMessageBox.warning(self, "No port", "Pick a serial port first.")
            return
        self.calibration_worker = CalibrationWorker(port, JOINT_ORDER)
        self.calibration_worker.connected.connect(self.calibration_panel.set_connected)
        self.calibration_worker.error.connect(self._on_robot_error)
        self.calibration_worker.live_update.connect(self.calibration_panel.update_live_table)
        self.calibration_worker.finished_calibration.connect(self._on_calibration_finished)
        self.calibration_worker.start()

    def _on_calibration_disconnect(self) -> None:
        if self.calibration_worker:
            worker = self.calibration_worker
            self.calibration_worker = None
            self._retire_worker(worker)
        self.calibration_panel.set_connected(False)

    def _on_calibration_reset(self) -> None:
        if self.calibration_worker:
            self.calibration_worker.request_reset()

    def _on_calibration_set_middle(self) -> None:
        """Show a "here's what the middle pose should look like" reference
        before actually locking it in - the calibration step itself
        (set_half_turn_homing) trusts wherever the arm physically is the
        instant it runs, so it's worth a beat to confirm the user actually
        parked it somewhere sensible first, especially for someone new to
        this GUI who's never seen what "middle" is supposed to mean here."""
        if self.twin_worker:
            self.twin_worker.request_neutral_snapshot()
        else:
            self._on_neutral_pose_ready(None)

    def _on_neutral_pose_ready(self, frame) -> None:
        if self._confirm_middle_pose_dialog(frame) and self.calibration_worker:
            self.calibration_worker.request_set_middle()

    def _confirm_middle_pose_dialog(self, frame) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Middle - target pose")
        layout = QVBoxLayout(dialog)

        message = QLabel(
            "Before clicking OK: move EVERY joint of the real arm by hand "
            "(torque off) to roughly match the pose below - this is the "
            "middle of each joint's intended travel, which is what 'Set "
            "Middle' will lock in as that joint's 0deg reference.\n\n"
            "This is the digital twin's own designed zero pose - a visual "
            "target, not necessarily a pixel-perfect match to your specific "
            "arm's real middle, but a good reference if you've never done "
            "this before."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        if frame is not None:
            height, width, _ = frame.shape
            image = QImage(frame.data, width, height, 3 * width, QImage.Format_RGB888)
            picture = QLabel()
            picture.setPixmap(
                QPixmap.fromImage(image).scaled(420, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            picture.setAlignment(Qt.AlignCenter)
            layout.addWidget(picture)
        else:
            note = QLabel(
                "(load a digital twin in the Control tab first to see a reference image here)"
            )
            note.setObjectName("sectionCaption")
            note.setWordWrap(True)
            layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        return dialog.exec() == QDialog.Accepted

    def _on_calibration_start_recording(self) -> None:
        if self.calibration_worker:
            self.calibration_worker.request_start_recording()

    def _on_calibration_stop_recording(self) -> None:
        if self.calibration_worker:
            self.calibration_worker.request_stop_recording()

    def _on_calibration_finish(self) -> None:
        if self.calibration_worker:
            self.calibration_worker.request_finish()

    def _on_calibration_finished(self, calibration_dict: dict) -> None:
        self.calibration_panel.prompt_save(calibration_dict)

    # ---------------------------------------------------------------- teaching (record & playback)
    def _on_record_waypoint(self) -> None:
        """Snapshot the follower's CURRENT pose, however it got there - hand-
        guided with torque off, driven by the leader, or the manual sliders.
        Doesn't care which; current_positions is already the one place all
        three sources funnel into."""
        label = f"Waypoint {len(self.waypoints) + 1}"
        self.waypoints.append({"label": label, "positions": dict(self.current_positions)})
        self._refresh_waypoint_list()
        self.session_logger.log_event(f"Teaching: recorded '{label}'")

    def _refresh_waypoint_list(self) -> None:
        self.teaching_panel.set_waypoints([wp["label"] for wp in self.waypoints])

    def _on_delete_waypoint(self) -> None:
        index = self.teaching_panel.selected_index()
        if index is None:
            return
        removed = self.waypoints.pop(index)
        self._refresh_waypoint_list()
        self.session_logger.log_event(f"Teaching: deleted '{removed['label']}'")

    def _on_move_waypoint(self, delta: int) -> None:
        index = self.teaching_panel.selected_index()
        if index is None:
            return
        new_index = index + delta
        if not (0 <= new_index < len(self.waypoints)):
            return
        self.waypoints[index], self.waypoints[new_index] = self.waypoints[new_index], self.waypoints[index]
        self._refresh_waypoint_list()
        self.teaching_panel.select_row(new_index)

    def _on_play_sequence(self) -> None:
        if not self.waypoints:
            QMessageBox.information(self, "No waypoints", "Record at least one waypoint first.")
            return
        if not self.robot_worker:
            QMessageBox.warning(self, "No follower", "Connect the follower first - playback drives it directly.")
            return
        self.robot_worker.request_torque(True)
        self._playback_index = 0
        self._start_playback_waypoint()
        self.teaching_panel.set_playing(True)
        self.playback_timer.start(PLAYBACK_TICK_MS)
        self.session_logger.log_event("Teaching: playback started")

    def _start_playback_waypoint(self) -> None:
        """Sets up a linear trajectory from wherever the follower is RIGHT
        NOW to this waypoint's target, capped at PLAYBACK_DEG_PER_S - a raw
        single Goal_Position jump lets the servo firmware move at whatever
        its own max speed is (jerky, no ramp), so the smoothing has to
        happen here, one small intermediate goal per tick, instead."""
        wp = self.waypoints[self._playback_index]
        self._playback_start_positions = dict(self.current_positions)
        self._playback_target_positions = dict(wp["positions"])
        max_delta = max(
            (
                abs(self._playback_target_positions[name] - self._playback_start_positions.get(name, 0.0))
                for name in self._playback_target_positions
            ),
            default=0.0,
        )
        self._playback_move_duration = max(PLAYBACK_MIN_MOVE_S, max_delta / PLAYBACK_DEG_PER_S)
        self._playback_move_start = time.monotonic()
        self._playback_deadline = (
            self._playback_move_start + self._playback_move_duration
            + PLAYBACK_SETTLE_GRACE_S + PLAYBACK_MAX_DWELL_S
        )
        self.teaching_panel.set_status(
            f"Moving to '{wp['label']}' ({self._playback_index + 1}/{len(self.waypoints)})"
        )
        self.teaching_panel.select_row(self._playback_index)

    def _playback_tick(self) -> None:
        """Sends one interpolated step of the current waypoint's trajectory,
        then advances once the follower's OWN feedback (current_positions,
        updated by _on_positions_updated at 60Hz) says it arrived within
        tolerance - or once it's waited long enough that it's clearly not
        going to (a stalled joint, a target near a mechanical limit it can't
        quite reach) so one bad waypoint can't hang the whole sequence
        forever."""
        if self._playback_index is None:
            self.playback_timer.stop()
            return

        now = time.monotonic()
        t = max(0.0, min(1.0, (now - self._playback_move_start) / self._playback_move_duration))
        for name, target in self._playback_target_positions.items():
            start = self._playback_start_positions.get(name, target)
            self.robot_worker.request_goal(name, start + t * (target - start))

        arrived = t >= 1.0 and all(
            abs(self.current_positions.get(name, 0.0) - degrees) <= PLAYBACK_ARRIVE_TOLERANCE_DEG
            for name, degrees in self._playback_target_positions.items()
        )
        if arrived or now >= self._playback_deadline:
            self._playback_index += 1
            if self._playback_index >= len(self.waypoints):
                self._on_stop_sequence()
                self.session_logger.log_event("Teaching: playback finished")
                return
            self._start_playback_waypoint()

    def _on_stop_sequence(self) -> None:
        self.playback_timer.stop()
        self._playback_index = None
        self.teaching_panel.set_playing(False)
        self.teaching_panel.set_status("")

    def _on_save_waypoints(self) -> None:
        if not self.waypoints:
            QMessageBox.information(self, "No waypoints", "Nothing to save yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save waypoints", "waypoints.json", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.waypoints, f, indent=2)
        self.session_logger.log_event(f"Teaching: saved {len(self.waypoints)} waypoint(s) to {path}")

    def _on_load_waypoints(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load waypoints", "", "JSON (*.json)")
        if not path:
            return
        with open(path, encoding="utf-8") as f:
            self.waypoints = json.load(f)
        self._refresh_waypoint_list()
        self.session_logger.log_event(f"Teaching: loaded {len(self.waypoints)} waypoint(s) from {path}")

    # ---------------------------------------------------------------- lifecycle
    def closeEvent(self, event) -> None:
        self.playback_timer.stop()
        self._on_disconnect()
        self._on_leader_disconnect()
        self._on_calibration_disconnect()
        self._on_camera_stop()
        if self.gamepad_worker:
            self.gamepad_worker.stop()
            self.gamepad_worker.wait(2000)
        if self.twin_worker:
            self.twin_worker.stop()
            self.twin_worker.wait(2000)
        super().closeEvent(event)
