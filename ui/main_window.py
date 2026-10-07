from __future__ import annotations

import csv
import json
import math
import os
import time

import numpy as np
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.calibration_worker import CalibrationWorker
from core.dm_can import Control_Type
from core.dm_robot_worker import DmRobotWorker
from core.dm_setup_worker import DmSetupWorker
from core.fashionstar_leader_worker import FashionStarLeaderWorker
from core.gravity import GravityModel
from core.kinematics import AXES, KinematicChain, TcpSpec, rpy_deg_from_matrix
from core.robot_profiles import (
    DEFAULT_PROFILE_KEY,
    PROFILES,
    RobotProfile,
    get_profile,
    profile_arm_joints,
)
from core.servo_bus import JOINT_ORDER, MAX_RES, MODEL_RESOLUTION
from core.session_logger import SessionLogger
from core.setup_worker import SetupWorker
from core.twin_worker import TwinWorker
from core.workers import CameraWorker, GamepadWorker, RobotWorker

from .about_dialog import AboutDialog
from .calibration_panel import CalibrationPanel
from .camera_panel import CameraPanel
from .command_palette import CommandPalette, PaletteAction
from .connection_panel import ConnectionPanel
from .control_source_panel import ControlSourcePanel
from .data_logs_page import DataLogsPage
from .dm_calibration_page import DmCalibrationPage
from .dm_gripper_calibration_dialog import DmGripperCalibrationDialog
from .dm_setup_panel import DmSetupPanel
from .dm_tune_panel import DmTunePanel
from .fashionstar_calibration_dialog import FashionStarCalibrationDialog
from .gamepad_panel import DEFAULT_AXIS_MAP, DEFAULT_BUTTON_MAP, GamepadPanel
from .jog_panel import JogPanel
from .joint_panel import JointPanel
from .keyboard_jog_panel import KEY_JOG_MAP, KeyboardJogPanel
from .setup_hub import SetupHub
from .setup_panel import SetupPanel
from .stage_view import StageView
from .teaching_panel import TeachingPanel
from .telemetry_panel import TelemetryPanel, convert_telemetry
from .top_bar import ShellHost, TopBar
from .twin_panel import TwinPanel

GAMEPAD_TICK_MS = 33          # ~30 Hz jog integration
GAMEPAD_DEADZONE = 0.15
GAMEPAD_MAX_DEG_PER_S = 45.0  # full stick deflection = 45 deg/s

KEYBOARD_JOG_TICK_MS = 33
KEYBOARD_MAX_DEG_PER_S = 30.0  # gentler than the gamepad's max - keys are on/off, not proportional

JOG_TICK_MS = 33
# What 100% on the Jog panel's speed slider means. The slider defaults to 30%, so
# a first press is ~14 deg/s or ~24 mm/s - deliberately slow. Cartesian speeds
# are in the TWIN MODEL's units (see MainWindow._build_kinematics): a real arm
# whose calibrated joint ranges differ from the model's moves proportionally
# more or less than these numbers say.
JOG_MAX_JOINT_DEG_S = 45.0
JOG_MAX_LINEAR_MM_S = 80.0
JOG_MAX_ANGULAR_DEG_S = 45.0
# The ghost is only drawn when its pose is meaningfully away from the real arm's:
# drawn on top of an arm that is already there it would just z-fight with it.
GHOST_MIN_DIFF_DEG = 2.0
JOG_GHOST_LINGER_S = 2.0  # keep showing a jog's target briefly after release, while the arm catches up

PLAYBACK_TICK_MS = 33
PLAYBACK_ARRIVE_TOLERANCE_DEG = 3.0
# Speed cap is user-adjustable at runtime - see TeachingPanel.speed_slider /
# playback_speed_deg_per_s() - a raw single goal jump lets the servo firmware
# move at whatever its own max speed is (jerky/scary); interpolating our own
# trajectory at a capped rate is what tames it.
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

# Telemetry CSV logs BOTH the raw register value and the converted one for
# every field, with the unit in the column name. Logging only raw counts (what
# this used to do) means a capture silently disagrees with the on-screen table
# it was taken from; logging only converted values throws away the one number
# that is definitely correct, since two of the conversions are cross-checked
# rather than vendor-documented (see convert_telemetry). Position gets both
# raw ticks AND the calibrated degree value (see _raw_to_deg) - unlike the
# other fields there's no vendor scale factor to cross-check, but a plot
# wants degrees, not an arbitrary 0-4095 count.
GRAVITY_CHECK_MIN_NM = 1.0          # joints loaded less than this are not used to judge the model
GRAVITY_CHECK_MAX_SPEED_DEG_S = 3.0   # "at rest"
GRAVITY_CHECK_RATIO = (0.25, 4.0)     # measured / model: friction and model error allowed, not a wrong model
TELEMETRY_CSV_FIELDS = ("velocity", "load", "current", "voltage", "temperature")
_TELEMETRY_CSV_UNITS = ("deg_per_s_est", "percent", "ma", "volts", "celsius")
# strict=True so adding a field without its unit is an import-time error
# rather than a header that quietly stops describing the columns under it.
TELEMETRY_CSV_HEADER = ["unix_time", "joint", "position_ticks", "position_deg"] + [
    column
    for field, unit in zip(TELEMETRY_CSV_FIELDS, _TELEMETRY_CSV_UNITS, strict=True)
    for column in (f"{field}_raw", f"{field}_{unit}")
]


def _validate_waypoints(raw) -> list[dict]:
    """Coerce a loaded .json into the exact shape playback assumes, or raise
    ValueError naming what's wrong. Unknown joint names are dropped rather
    than rejected, so a sequence recorded on an arm with an extra joint still
    loads usefully on a standard SO-101."""
    if not isinstance(raw, list):
        raise ValueError("expected a list of waypoints at the top level")
    validated = []
    for index, entry in enumerate(raw):
        where = f"waypoint {index + 1}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: expected an object, got {type(entry).__name__}")
        positions = entry.get("positions")
        if not isinstance(positions, dict):
            raise ValueError(f"{where}: missing a 'positions' object")
        cleaned = {}
        for name, value in positions.items():
            if name not in JOINT_ORDER:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{where}: '{name}' is {value!r}, expected a number of degrees")
            cleaned[name] = float(value)
        if not cleaned:
            raise ValueError(f"{where}: no recognisable joints in 'positions'")
        validated.append({"label": str(entry.get("label", f"Waypoint {index + 1}")), "positions": cleaned})
    if not validated:
        raise ValueError("the file contains no waypoints")
    return validated


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SO-101 Control Station")
        self.resize(1560, 880)
        self.setMinimumSize(1100, 700)
        self.setFocusPolicy(Qt.StrongFocus)  # so keyPressEvent fires without a child widget stealing focus first

        # ================================================================ PANELS
        self.connection_panel = ConnectionPanel()
        self.control_source_panel = ControlSourcePanel()
        self.joint_panel = JointPanel()
        self.jog_panel = JogPanel(self.joint_panel)
        self.teaching_panel = TeachingPanel()
        self.gamepad_panel = GamepadPanel()
        self.keyboard_jog_panel = KeyboardJogPanel()
        self.twin_panel = TwinPanel()
        self.camera_panel = CameraPanel()
        self.telemetry_panel = TelemetryPanel()
        self.dm_tune_panel = DmTunePanel(joint_order=PROFILES['rebot_b601_dm'].joint_order)

        # ================================================================ STAGE PAGE
        # The twin fills the stage with floating cards over it (View, Jog, the
        # connect / control-source dock, optional camera); Waypoints / Telemetry
        # open in a drawer on the right only when asked for.
        self.stage = StageView(
            self.twin_panel, self.camera_panel, self.jog_panel,
            [self.connection_panel, self.control_source_panel],
        )
        self.stage.set_layout_state(self._load_settings().get("stage_layout", {}))
        self.stage.layout_changed.connect(lambda state: self._save_setting("stage_layout", state))

        self.drawer = QStackedWidget()
        self.drawer.addWidget(self.teaching_panel)
        self.drawer.addWidget(self.telemetry_panel)
        self.drawer.addWidget(self.dm_tune_panel)
        self.drawer.setMinimumWidth(440)
        self.drawer.setContentsMargins(0, TopBar.BAR_H, 0, 0)
        self.drawer.hide()

        self.stage_splitter = QSplitter(Qt.Horizontal)
        self.stage_splitter.addWidget(self.stage)
        self.stage_splitter.addWidget(self.drawer)
        self.stage_splitter.setStretchFactor(0, 1)
        self.stage_splitter.setStretchFactor(1, 0)
        self.stage_splitter.setSizes([1100, 440])

        # ================================================================ SETUP HUB
        self.setup_panel = SetupPanel()
        # A per-robot id-assignment scheme (SO-101's Feetech servos and the
        # B601-DM's Damiao motors are utterly different buses/protocols - see
        # core/dm_setup_worker.py) means the "Motors and ids" section swaps its
        # WHOLE content by profile rather than being one panel with some rows
        # hidden - a stacked widget is what lets both live fully-built,
        # independently wired to their own worker, with only one ever
        # visible/enabled at a time (see _on_robot_profile_changed).
        self.dm_setup_panel = DmSetupPanel(joint_order=PROFILES["rebot_b601_dm"].joint_order)
        self.setup_stack = QStackedWidget()
        self.setup_stack.addWidget(self.setup_panel)
        self.setup_stack.addWidget(self.dm_setup_panel)
        self.calibration_panel = CalibrationPanel()
        self.dm_calibration_page = DmCalibrationPage()
        # Calibration is per robot: Feetech's multi-step wizard vs the B601-DM's
        # sweep tools. Both are built; the profile picks which one is shown.
        self.calibration_stack = QStackedWidget()
        self.calibration_stack.addWidget(self.calibration_panel)
        self.calibration_stack.addWidget(self.dm_calibration_page)
        self.data_logs_page = DataLogsPage("")   # real path set once the SessionLogger exists

        inputs_widget = QWidget()
        inputs_layout = QVBoxLayout(inputs_widget)
        inputs_layout.addWidget(self.gamepad_panel)
        inputs_layout.addWidget(self.keyboard_jog_panel)
        inputs_layout.addStretch(1)

        # Every section scrolls (SetupHub wraps it): DmSetupPanel alone can
        # exceed the window height, which once meant silent clipping.
        self.setup_hub = SetupHub()
        self.setup_hub.add_section("Motors and ids", self.setup_stack)
        self.setup_hub.add_section("Calibration", self.calibration_stack)
        self.setup_hub.add_section("Inputs", inputs_widget)
        self.setup_hub.add_section("Data and logs", self.data_logs_page)
        self.setup_hub.back_requested.connect(lambda: self.top_bar.nav_buttons["setup"].setChecked(False))

        self.pages = QStackedWidget()
        self.pages.addWidget(self.stage_splitter)   # PAGE_STAGE
        self.pages.addWidget(self.setup_hub)        # PAGE_SETUP

        # Robot selection lives in the always-visible top bar - which robot is
        # being driven decides whether Setup/Calibration make sense to use AT
        # ALL (see _apply_robot_hardware_gate), not just what the stage shows.
        self.robot_combo = QComboBox()
        for profile in PROFILES.values():
            self.robot_combo.addItem(profile.label, profile.key)
        self.robot_combo.currentIndexChanged.connect(self._on_robot_profile_changed)

        self.top_bar = TopBar(self.robot_combo)
        self.top_bar.nav_toggled.connect(self._on_nav_toggled)
        self.top_bar.stop_requested.connect(self._on_emergency_stop)
        self.top_bar.palette_requested.connect(self._open_palette)
        self.top_bar.about_requested.connect(self._show_about)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_palette)
        QShortcut(QKeySequence("Ctrl+Shift+Space"), self, activated=self._on_emergency_stop)

        # The bar floats over the pages (mockup B1) instead of taking a row above
        # them; every page reserves TopBar.BAR_H at its top so nothing hides under it.
        self.setCentralWidget(ShellHost(self.pages, self.top_bar))

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

        self.session_logger = SessionLogger()
        self.data_logs_page.log_path = self.session_logger.path
        self.data_logs_page.path_label.setText(str(self.session_logger.path))
        self.statusBar().showMessage(f"Ready. Logging to {self.session_logger.path}")

        # -- state -------------------------------------------------------
        self.robot_profile: RobotProfile = get_profile(
            self._load_settings().get("robot_profile", DEFAULT_PROFILE_KEY)
        )
        self.robot_worker: RobotWorker | None = None
        # Nothing tracked this before a real incident showed it was needed:
        # ui/dm_gripper_calibration_dialog.py refuses to run while torque is
        # on (the motor would fight the hand, measuring nothing real), and
        # this is the only place that state exists - _on_torque is the sole
        # writer, matching request_torque's own fire-and-forget shape.
        self.follower_torque_enabled: bool = False
        # Leader -> follower tracking only runs while this is True (see
        # _on_engage_toggled); merely selecting the Leader arm source never starts it.
        self.teleop_engaged: bool = False
        # Gravity feed-forward is OFF every session and unlocks only after a check (see
        # _on_gravity_check) against the motors' own torque feedback.
        self._gravity_ok: bool = False
        self._gravity_verified: set[str] = set()   # joints the check verified: only these get feed-forward
        self._last_telemetry: dict = {}
        self._gravity_model: GravityModel | None = None
        self._leader_converted: dict[str, float] = {}   # latest leader pose in FOLLOWER degrees
        self.teleop_tolerance_deg: float = float(self._load_settings().get("teleop_align_tolerance_deg", 10.0))
        self._follower_connected = False
        self._leader_connected = False
        self.leader_worker: RobotWorker | None = None
        self.calibration_worker: CalibrationWorker | None = None
        self.setup_worker: SetupWorker | None = None
        self.dm_setup_worker: DmSetupWorker | None = None
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
        # per side: {joint: True if opening counts encoder ticks UP}. Only
        # populated for joints whose calibration recorded closed/open ticks
        # (the manual 2-point capture); absent entries mean "unknown", which
        # _relay_direction_flipped() treats as "don't flip".
        self.leader_open_directions: dict[str, bool] = {}
        self.follower_open_directions: dict[str, bool] = {}
        self.gripper_invert_override: bool = self._load_gripper_invert()
        # Per-joint constant correction added to the relayed target, in degrees.
        # A joint's zero is the midpoint of its calibrated range, and for
        # wrist_roll that midpoint is pinned to nothing physical at all - the
        # calibration forces its range to a full turn, so the zero is wherever
        # the arm happened to be held during "Set middle". Two arms homed at
        # even slightly different wrist orientations therefore disagree by a
        # constant angle that no amount of range normalisation can remove
        # (measured here: wrist_flex +10.5 deg, wrist_roll +12.7 deg). This is
        # the only mechanism that can cancel it.
        self.relay_trim_deg: dict[str, float] = {
            name: float(value)
            for name, value in (self._load_settings().get("relay_trim_deg") or {}).items()
            if name in JOINT_ORDER
        }
        self.control_source: str = "manual"
        self._update_status_chips()
        self._last_gamepad_axes: dict[int, float] = {}
        self._last_tick = time.monotonic()
        self._held_keys: set = set()
        self._last_keyboard_tick = time.monotonic()

        # -- state: Jog panel (JAKA-style manual movement) ------------------------
        # Cartesian jogging runs on the twin's own MJCF; None until a twin is
        # loaded (or if its model can't be used), which greys out World/Tool.
        self.kinematics: KinematicChain | None = None
        # {(mode, axis): [direction, expires_at_or_None]}. None = a held button
        # (continuous); a time = a fixed-size step that runs to completion.
        self._jog_active: dict[tuple[str, str], list] = {}
        # Where the jog is heading, in degrees. Integrated from THIS rather than
        # from current_positions each tick: with a follower connected,
        # current_positions is the MEASURED pose and lags the command, so
        # integrating from it would make the arm chase itself and crawl. It is
        # also exactly the "commanded target" the ghost shows.
        self._jog_target: dict[str, float] = {}
        self._jog_ghost_until = 0.0
        self._last_jog_tick = time.monotonic()
        # Workers mid-shutdown: stop() only flips a flag, the OS thread is
        # still alive for a bit after that. Dropping the last Python
        # reference to a QThread while its thread is still running makes
        # PySide6 destroy it out from under itself - a hard Qt abort, not a
        # graceful no-op. Holding a reference here until `finished` actually
        # fires is what prevents that (self-referencing lambda closures rely
        # on a GC cycle collection with no defined timing - not good enough).
        self._shutting_down_workers: list = []

        # -- state: teaching (record/playback waypoints) --------------------
        self.waypoints: list[dict] = []  # [{"label": str, "positions": {joint: deg}, "dwell_ms": int}]
        self._playback_index: int | None = None
        self._playback_start_positions: dict[str, float] = {}
        self._playback_target_positions: dict[str, float] = {}
        self._playback_move_start = 0.0
        self._playback_move_duration = PLAYBACK_MIN_MOVE_S
        self._playback_deadline = 0.0
        # None = still moving or not yet arrived; once arrived this holds the
        # monotonic time the dwell ends. Kept separate from _playback_deadline
        # (a timeout for a move that never arrives) - this is a deliberate
        # pause AFTER a successful arrival, not a stall fallback.
        self._playback_hold_until: float | None = None
        self._playback_dwell_s = 0.0
        self.playback_timer = QTimer(self)
        self.playback_timer.timeout.connect(self._playback_tick)

        # -- state: telemetry CSV capture -----------------------------------
        self._telemetry_csv = None
        self._telemetry_csv_writer = None

        # -- wiring: robot connection --------------------------------------
        self.connection_panel.connect_requested.connect(self._on_connect)
        self.connection_panel.disconnect_requested.connect(self._on_disconnect)
        self.connection_panel.torque_requested.connect(self._on_torque)
        self.connection_panel.calibrate_gripper_requested.connect(self._on_dm_gripper_calibrate_requested)
        self.jog_panel.goal_changed.connect(self._on_goal_changed)
        self.jog_panel.jog_pressed.connect(self._on_jog_pressed)
        self.jog_panel.jog_released.connect(self._on_jog_released)
        self.jog_panel.mode_changed.connect(self._on_jog_mode_changed)

        # -- wiring: control source / teleoperation -------------------------
        self.control_source_panel.source_changed.connect(self._on_control_source_changed)
        self.control_source_panel.engage_toggled.connect(self._on_engage_toggled)
        self.control_source_panel.tolerance_changed.connect(self._on_teleop_tolerance_changed)
        self.control_source_panel.set_tolerance(self.teleop_tolerance_deg)
        self.control_source_panel.set_gripper_invert(self.gripper_invert_override)
        self.control_source_panel.gripper_invert_toggled.connect(self._on_gripper_invert_toggled)
        self.control_source_panel.set_relay_trim(self.relay_trim_deg)
        self.control_source_panel.relay_trim_changed.connect(self._on_relay_trim_changed)
        self.control_source_panel.leader_connect_requested.connect(self._on_leader_connect)
        self.control_source_panel.leader_disconnect_requested.connect(self._on_leader_disconnect)
        self.control_source_panel.leader_calibrate_requested.connect(self._on_leader_calibrate_requested)

        # -- wiring: camera --------------------------------------------------
        self.camera_panel.start_requested.connect(self._on_camera_start)
        self.camera_panel.stop_requested.connect(self._on_camera_stop)

        # -- wiring: gamepad ---------------------------------------------------
        self.gamepad_panel.enable_toggled.connect(self._on_gamepad_toggled)

        # -- wiring: teaching (waypoints) ---------------------------------------
        self.teaching_panel.record_requested.connect(self._on_record_waypoint)
        self.teaching_panel.record_grip_requested.connect(lambda: self._on_record_waypoint(force_grip=True))
        self.teaching_panel.delete_requested.connect(self._on_delete_waypoint)
        self.teaching_panel.move_up_requested.connect(lambda: self._on_move_waypoint(-1))
        self.teaching_panel.move_down_requested.connect(lambda: self._on_move_waypoint(1))
        self.teaching_panel.play_requested.connect(self._on_play_sequence)
        self.teaching_panel.stop_requested.connect(self._on_stop_sequence)
        self.teaching_panel.save_requested.connect(self._on_save_waypoints)
        self.teaching_panel.load_requested.connect(self._on_load_waypoints)
        self.teaching_panel.delay_set_requested.connect(self._on_set_delay)
        self.teaching_panel.row_selected.connect(self._on_waypoint_row_selected)

        # -- wiring: telemetry ---------------------------------------------------
        self.telemetry_panel.log_toggled.connect(self._on_telemetry_log_toggled)
        self.telemetry_panel.register_write_requested.connect(self._on_register_write_requested)

        # -- wiring: digital twin -----------------------------------------------
        self.twin_panel.load_requested.connect(self._on_twin_load)

        # -- wiring: first-time motor setup ---------------------------------------
        self.setup_panel.connect_requested.connect(self._on_setup_connect)
        self.setup_panel.disconnect_requested.connect(self._on_setup_disconnect)
        self.setup_panel.scan_requested.connect(self._on_setup_scan)
        self.setup_panel.deep_scan_requested.connect(self._on_setup_deep_scan)
        self.setup_panel.assign_id_requested.connect(self._on_setup_assign_id)
        self.setup_panel.set_baudrate_requested.connect(self._on_setup_set_baudrate)

        # -- wiring: reBot B601-DM CAN id setup -----------------------------------
        self.dm_setup_panel.connect_requested.connect(self._on_dm_setup_connect)
        self.dm_setup_panel.disconnect_requested.connect(self._on_dm_setup_disconnect)
        self.dm_setup_panel.probe_requested.connect(self._on_dm_setup_probe)
        self.dm_setup_panel.assign_requested.connect(self._on_dm_setup_assign)
        self.dm_setup_panel.enable_requested.connect(self._on_dm_setup_enable)
        self.dm_setup_panel.disable_requested.connect(self._on_dm_setup_disable)
        self.dm_setup_panel.read_pid_requested.connect(self._on_dm_setup_read_pid)
        self.dm_setup_panel.write_pid_requested.connect(self._on_dm_setup_write_pid)
        self.dm_setup_panel.set_zero_requested.connect(self._on_dm_setup_set_zero)
        self.dm_setup_panel.verify_all_requested.connect(self._on_dm_setup_verify_all)
        self.dm_tune_panel.control_mode_changed.connect(self._on_dm_setup_control_mode_changed)
        self.dm_tune_panel.mit_gains_changed.connect(self._on_dm_setup_mit_gains_changed)
        self.dm_tune_panel.presets_changed.connect(lambda presets: self._save_setting("dm_mit_gain_presets", presets))
        self.dm_tune_panel.nudge_requested.connect(self._on_nudge_requested)
        self.dm_tune_panel.gravity_check_requested.connect(self._on_gravity_check)
        self.dm_tune_panel.gravity_percent_changed.connect(self._on_gravity_percent)
        self.dm_tune_panel.set_presets(self._load_settings().get("dm_mit_gain_presets", {}))
        self.jog_panel.speed_slider.valueChanged.connect(self._push_speed)
        self.dm_calibration_page.gripper_calibrate_requested.connect(self._on_dm_gripper_calibrate_requested)
        self.dm_calibration_page.leader_calibrate_requested.connect(self._on_leader_calibrate_requested)
        self.dm_calibration_page.open_ids_requested.connect(self._show_motors_and_ids)
        self.dm_setup_panel.set_mapping(self._load_settings().get("dm_can_id_mapping", {}))
        self.dm_tune_panel.set_control_mode(self._load_settings().get("dm_control_mode", "pos_vel"))
        saved_mit_gains = self._load_settings().get("dm_mit_gains", {})
        if saved_mit_gains:
            self.dm_tune_panel.set_mit_gains(
                {name: tuple(gains) for name, gains in saved_mit_gains.items()}
            )

        # -- wiring: calibration -------------------------------------------------
        self.calibration_panel.connect_requested.connect(self._on_calibration_connect)
        self.calibration_panel.disconnect_requested.connect(self._on_calibration_disconnect)
        self.calibration_panel.reset_requested.connect(self._on_calibration_reset)
        self.calibration_panel.set_middle_requested.connect(self._on_calibration_set_middle)
        self.calibration_panel.start_recording_requested.connect(self._on_calibration_start_recording)
        self.calibration_panel.stop_recording_requested.connect(self._on_calibration_stop_recording)
        self.calibration_panel.finish_requested.connect(self._on_calibration_finish)
        self.calibration_panel.gripper_manual_mode_toggled.connect(self._on_gripper_manual_mode_toggled)
        self.calibration_panel.gripper_capture_requested.connect(self._on_gripper_capture_requested)
        self.calibration_panel.auto_calibrate_requested.connect(self._on_auto_calibrate_requested)

        self.ui_refresh_timer = QTimer(self)
        self.ui_refresh_timer.timeout.connect(self._refresh_ui)
        self.ui_refresh_timer.start(UI_REFRESH_MS)

        self.gamepad_timer = QTimer(self)
        self.gamepad_timer.timeout.connect(self._gamepad_tick)

        # No hardware to enable/detect (unlike the gamepad) - just runs
        # continuously and gates on self.control_source inside the tick,
        # same as the gamepad timer does once it's started.
        self.keyboard_timer = QTimer(self)
        self.keyboard_timer.timeout.connect(self._keyboard_jog_tick)
        self.keyboard_timer.start(KEYBOARD_JOG_TICK_MS)

        self.jog_timer = QTimer(self)
        self.jog_timer.timeout.connect(self._jog_tick)
        self.jog_timer.start(JOG_TICK_MS)

        self._apply_control_source_lock()

        # Apply whichever robot profile was persisted (default: SO-101) -
        # setCurrentIndex() only actually fires currentIndexChanged when the
        # index is genuinely different from the combo's own already-default
        # 0, so the common "still on SO-101" case would otherwise never run
        # _on_robot_profile_changed at all. Calling it explicitly makes
        # startup behave identically whichever profile was last selected.
        # setCurrentIndex(N) only fires currentIndexChanged (and therefore
        # _on_robot_profile_changed) when N actually differs from the
        # combo's current value - which is only sometimes true here,
        # depending on whether the persisted profile happens to be the
        # combo's own already-default index 0. Confirmed the hard way:
        # calling _on_robot_profile_changed explicitly on top of that,
        # unconditionally, meant a persisted non-default profile (index 1)
        # got the handler invoked TWICE at startup - once from the signal,
        # once from this line - which raced two TwinWorkers into existence
        # for the same profile and left one of them stopped but never
        # actually torn down, still holding the render view on a stale
        # frame from a robot no longer selected. Call the handler exactly
        # once, from whichever path is actually needed.
        default_index = self.robot_combo.findData(self.robot_profile.key)
        if default_index < 0:
            default_index = 0
        if self.robot_combo.currentIndex() == default_index:
            self._on_robot_profile_changed(default_index)
        else:
            self.robot_combo.setCurrentIndex(default_index)

    # ---------------------------------------------------------------- robot profile
    def _on_robot_profile_changed(self, index: int) -> None:
        """Fires when the user picks a different entry in the Robot combo
        (and once at startup - see __init__ - so the persisted profile is
        applied identically whichever one it is)."""
        key = self.robot_combo.itemData(index)
        profile = get_profile(key)
        self.robot_profile = profile
        self._save_setting("robot_profile", key)

        # Any real hardware connection belonged to the PREVIOUS robot -
        # unconditional on every profile switch now, not just when moving to
        # a profile with no hardware backend at all. That conditional used
        # to be correct when only SO-101 had hardware_available=True, but
        # once reBot B601-DM does too (Phase 2), switching SO-101 -> reBot
        # B601-DM must ALSO tear down a stale RobotWorker/CalibrationWorker -
        # otherwise it would keep talking to the wrong bus for the wrong arm
        # instead of being disconnected the way the panel visually shows.
        if self.robot_worker:
            self._on_disconnect()
        if self.leader_worker:
            self._on_leader_disconnect()
        if self.calibration_worker:
            self._on_calibration_disconnect()
        if self.setup_worker:
            self._on_setup_disconnect()
        if key != "rebot_b601_dm" and self.dm_setup_worker:
            # Mirrors the block above but keyed on the SPECIFIC profile
            # rather than hardware_available - DM setup is the one thing
            # that IS meant to work for this "no hardware_available" robot
            # (see _apply_robot_hardware_gate), so it needs its own release
            # condition instead of piggybacking on that flag.
            self._on_dm_setup_disconnect()

        # A different robot has entirely different joints, not just
        # different limits on the same SO-101 six - current_positions/
        # joint_deg_ranges get reseeded, and the Joint Control sliders
        # rebuild with fresh rows (see JointPanel.rebuild). preview_ranges
        # is empty for SO-101, so joint_deg_ranges just starts empty there
        # too, exactly as it always has - a real connect's _load_joint_limits
        # overwrites it either way.
        self.current_positions = dict.fromkeys(profile.joint_order, 0.0)
        self.joint_deg_ranges = dict(profile.preview_ranges)
        self.joint_panel.rebuild(list(profile.joint_order), limits=profile.preview_ranges)
        self.telemetry_panel.rebuild(profile.joint_order, profile.key)

        # Anything mid-jog belonged to the previous robot, and so does its
        # kinematic model. A robot with no hardware backend can only ever be
        # driven from the Jog panel (its Control Source panel is greyed out), so
        # make sure that panel is the live one.
        self._jog_release_all()
        self._jog_target = {}
        self.kinematics = None
        self.jog_panel.set_cartesian_available(False, "Load a digital twin to enable World/Tool jogging.")
        if not profile.hardware_available and self.control_source != "manual":
            self.control_source_panel.force_manual()

        # Auto-load this profile's twin - a customized path from a previous
        # session (if any) wins over the profile's own bundled default, so
        # switching back and forth doesn't keep discarding a path the user
        # deliberately typed in.
        mjcf_paths = self._load_settings().get("mjcf_path_by_profile", {})
        path = mjcf_paths.get(key) or profile.default_mjcf_path
        if path:
            self.twin_panel.path_edit.setText(path)
            self._on_twin_load(path)
        else:
            # No bundled/remembered path for this profile - confirmed a real
            # gap here without this: clearing just the caption/path text
            # left the OLD robot's last-rendered frame sitting in the view,
            # looking like a live render of whatever's selected now, while a
            # TwinWorker nothing points at anymore kept quietly running.
            self.twin_panel.path_edit.clear()
            self.twin_panel.set_caption("not loaded - pick a scene.xml and click Load")
            self.twin_panel.clear_frame()
            if self.twin_worker:
                worker = self.twin_worker
                self.twin_worker = None
                self._retire_worker(worker)

        self._apply_robot_hardware_gate()
        self.statusBar().showMessage(f"Robot: {profile.label}")
        self.session_logger.log_event(f"Robot profile changed to: {profile.label}")

    def _apply_robot_hardware_gate(self) -> None:
        """Grey out every panel that only makes sense with a real bus behind
        it, for a robot this app has no hardware backend for yet.
        Joint Control, the Digital Twin and the Camera panel stay enabled:
        sliders already only reach hardware through
        `if self.robot_worker: ...` guards, so with no worker ever
        constructed for this profile they just drive the twin preview, and
        the camera is robot-agnostic regardless.

        Setup is its own case, deliberately NOT gated by hardware_available:
        CAN id assignment for the B601-DM's Damiao motors (core/dm_setup_worker.py)
        is a distinct concern from driving the arm, so "1 - Setup" swaps to a
        whole different, always-enabled panel for this profile rather than
        following the same flag as Connection/Torque/etc.

        Calibration is ALSO its own case, now that reBot B601-DM has real
        hardware_available=True too (Phase 2): Feetech's multi-step homing/
        range-sweep process (core/calibration_worker.py, role combo,
        per-servo homing offsets) has no Damiao equivalent this phase - see
        core/robot_profiles.py's docstring on preview_ranges - so it stays
        disabled specifically for this robot regardless of hardware_available,
        instead of showing a form shaped for the wrong motor family."""
        available = self.robot_profile.hardware_available
        note = "" if available else f"Not available for {self.robot_profile.label} yet."
        for panel in (
            self.connection_panel,
            self.control_source_panel,
            self.teaching_panel,
            self.gamepad_panel,
            self.keyboard_jog_panel,
            self.telemetry_panel,
        ):
            panel.setEnabled(available)
            panel.setToolTip(note)

        is_dm = self.robot_profile.key == "rebot_b601_dm"
        # The Feetech wizard has no Damiao equivalent; the B601-DM gets its own
        # sweep tools page (which just launches the existing dialogs) instead.
        self.calibration_stack.setCurrentWidget(self.dm_calibration_page if is_dm else self.calibration_panel)
        self.calibration_panel.setEnabled(available and not is_dm)
        self.calibration_panel.setToolTip(note)
        self._refresh_dm_calibration_summary()
        # Tune (MIT / POS_VEL, kp/kd) only exists for the Damiao follower.
        self.top_bar.nav_buttons["tune"].setVisible(is_dm)
        if not is_dm and self.drawer.currentWidget() is self.dm_tune_panel:
            self.top_bar.nav_buttons["tune"].setChecked(False)

        if self.robot_profile.key == "rebot_b601_dm":
            self.setup_stack.setCurrentWidget(self.dm_setup_panel)
        else:
            self.setup_stack.setCurrentWidget(self.setup_panel)
            self.setup_panel.setEnabled(available)
            self.setup_panel.setToolTip(note)

        self.connection_panel.set_gripper_calibration_visible(self.robot_profile.key == "rebot_b601_dm")

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

        if self.robot_profile.key == "rebot_b601_dm":
            self._on_dm_connect_real(port)
            return

        if not calibration_path:
            QMessageBox.warning(
                self, "No calibration",
                "A LeRobot calibration .json is required - this app refuses to "
                "move a joint it doesn't know the safe range for.\n\n"
                "Don't have one yet? Produce it on the Calibration tab (or with "
                "lerobot-calibrate); both write the same format.",
            )
            return
        if self.setup_worker:
            QMessageBox.warning(
                self, "Port already in use",
                "The Setup tab is holding the serial port - disconnect there first.",
            )
            return

        self.session_logger.log_event(f"Follower: connecting on {port} (calibration: {calibration_path})")
        self.robot_worker = RobotWorker(port, calibration_path)
        self.robot_worker.servo_config_read.connect(
            lambda joint, cfg: self.session_logger.log_event(
                f"ServoConfig({joint}): " + "  ".join(f"{k}={v}" for k, v in cfg.items())
            )
        )
        self.robot_worker.positions_updated.connect(self._on_positions_updated)
        self.robot_worker.telemetry_updated.connect(self._on_telemetry_updated)
        self.robot_worker.error.connect(self._on_robot_error)
        self.robot_worker.connection_changed.connect(self._on_connection_changed)
        self._push_speed()
        self.robot_worker.start()

    def _on_dm_connect_real(self, port: str) -> None:
        """reBot B601-DM's real-hardware connect path - deliberately separate
        from the Feetech one above rather than threading DamiaoBus-specific
        branches through it, since the two robots' safety preconditions
        genuinely differ (a calibration FILE for one, a complete CAN id
        mapping for the other) rather than being the same check in disguise."""
        if self.dm_setup_worker:
            QMessageBox.warning(
                self, "Port already in use",
                "The Setup tab is holding the serial port - disconnect there first.",
            )
            return
        mapping = self._load_settings().get("dm_can_id_mapping", {})
        missing = [name for name in self.robot_profile.joint_order if name not in mapping]
        if missing:
            QMessageBox.warning(
                self, "Motors not fully assigned",
                "Every joint needs its CAN id assigned and verified on the Setup "
                f"tab before connecting here - missing: {', '.join(missing)}.",
            )
            return
        id_master_by_joint = {
            name: (mapping[name]["can_id"], mapping[name]["master_id"])
            for name in self.robot_profile.joint_order
        }

        settings = self._load_settings()
        control_mode = Control_Type.MIT if settings.get("dm_control_mode") == "mit" else Control_Type.POS_VEL
        mit_gains = {
            name: tuple(gains) for name, gains in settings.get("dm_mit_gains", {}).items()
        }
        # Assumed defaults, overridden per-joint by anything actually
        # measured via "Calibrate gripper range..." - added after a real
        # incident where preview_ranges["finger_left"]'s ASSUMED range let
        # write_goals_deg clamp toward a position past this unit's true
        # mechanical stop, stalling the motor until a fuse blew.
        ranges_deg = dict(self.robot_profile.preview_ranges)
        ranges_deg.update(
            {name: tuple(span) for name, span in settings.get("dm_follower_calibrated_ranges", {}).items()}
        )

        self.session_logger.log_event(
            f"Follower (Damiao): connecting on {port}, control mode {control_mode.name}"
        )
        self.robot_worker = DmRobotWorker(
            port,
            id_master_by_joint,
            ranges_deg,
            control_mode=control_mode,
            mit_gains=mit_gains,
            gravity_mjcf_path=self._gravity_path(),
        )
        self.robot_worker.gravity_status.connect(
            lambda ok, text: self.dm_tune_panel.set_gravity_controls(False, False, text)
        )
        self.robot_worker.positions_updated.connect(self._on_positions_updated)
        self.robot_worker.telemetry_updated.connect(self._on_telemetry_updated)
        self.robot_worker.error.connect(self._on_robot_error)
        self.robot_worker.connection_changed.connect(self._on_connection_changed)
        self._push_speed()   # the Speed slider may have been moved before connecting
        self.robot_worker.start()

    def _on_fashionstar_leader_connect(self, port: str) -> None:
        """Leader-arm connect for the B601-DM's real leader - Seeed's Star Arm
        102 (FashionStar UART smart servos, core/fashionstar_bus.py) - a
        completely different bus/protocol from the follower's Damiao CAN.
        Confirmed directly this session (real hardware bring-up) after an
        earlier, wrong assumption that this leader was Damiao-based too.

        No calibration file or CAN-id mapping needed from the GUI's side: the
        servo's own zero point lives in its flash after a one-time
        `lerobot-calibrate`-style set_origin_point() call, which this leader
        already has.

        native_ranges_deg (per-joint measured span, FashionStar-name keyed)
        comes from the "Calibrate leader..." dialog if it's ever been run
        (ui/fashionstar_calibration_dialog.py) - falls back to
        core/fashionstar_bus.py's hardcoded (confirmed-imperfect) defaults
        for any joint not yet swept."""
        settings = self._load_settings()
        native_ranges = {
            name: tuple(span)
            for name, span in settings.get("fashionstar_leader_native_ranges", {}).items()
        }
        self.session_logger.log_event(f"Leader (FashionStar): connecting on {port}")
        self.leader_worker = FashionStarLeaderWorker(
            port, dict(self.robot_profile.preview_ranges), native_ranges
        )
        self.leader_worker.positions_updated.connect(self._on_leader_positions)
        self.leader_worker.error.connect(self._on_robot_error)
        self.leader_worker.connection_changed.connect(self._on_leader_connection_changed)
        self.leader_worker.start()

    def _on_leader_calibrate_requested(self) -> None:
        if not isinstance(self.leader_worker, FashionStarLeaderWorker):
            QMessageBox.information(
                self, "Not applicable",
                "Leader calibration is only for the reBot B601-DM's Star Arm 102 "
                "leader - connect it first (Leader arm, FashionStar over USB).",
            )
            return
        dialog = FashionStarCalibrationDialog(self.leader_worker, self)
        dialog.exec()
        if dialog.saved_native_ranges:
            self._save_setting(
                "fashionstar_leader_native_ranges",
                {name: list(span) for name, span in dialog.saved_native_ranges.items()},
            )
            self.statusBar().showMessage(
                "Leader joint ranges saved - reconnect the leader to apply them."
            )
            self.session_logger.log_event(
                f"Leader (FashionStar): saved calibrated ranges for {len(dialog.saved_native_ranges)} joint(s)"
            )
            self._refresh_dm_calibration_summary()

    def _on_dm_gripper_calibrate_requested(self) -> None:
        if self.robot_profile.key != "rebot_b601_dm" or self.robot_worker is None:
            QMessageBox.information(
                self, "Not applicable",
                "Gripper calibration is only for the reBot B601-DM follower - "
                "connect it first (Connection panel, Control tab).",
            )
            return
        dialog = DmGripperCalibrationDialog(self.robot_worker, self.follower_torque_enabled, self)
        dialog.exec()
        if dialog.saved_range:
            saved = dict(self._load_settings().get("dm_follower_calibrated_ranges", {}))
            saved["finger_left"] = list(dialog.saved_range)
            self._save_setting("dm_follower_calibrated_ranges", saved)
            self.statusBar().showMessage(
                "Gripper range saved - reconnect the follower to apply it."
            )
            self.session_logger.log_event(
                f"Follower (Damiao): saved calibrated gripper range {dialog.saved_range}"
            )
            self._refresh_dm_calibration_summary()

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
        # Drop the follower's calibrated ranges with it. Keeping them would
        # leave the jog clamp and the twin's degree->fraction mapping silently
        # scaled to an arm that is no longer attached - and a NEW connection
        # with a different calibration only overwrites them on success, so a
        # failed reconnect would keep using the old one indefinitely.
        self.joint_deg_ranges.clear()
        self.connection_panel.set_connected(False)
        self._follower_connected = False
        self.follower_torque_enabled = False
        self._disengage_teleop("follower disconnected")
        self._gravity_off("follower disconnected")
        self._update_status_chips()

    def _on_connection_changed(self, connected: bool) -> None:
        self.connection_panel.set_connected(connected)
        self._follower_connected = connected
        if not connected:
            self.follower_torque_enabled = False
            self._disengage_teleop("follower disconnected")
            self._gravity_off("follower disconnected")
        self._update_status_chips()
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
        bus = self.robot_worker.bus
        # This robot's OWN joints, not always SO-101's six - DamiaoBus.
        # deg_limits() reads straight from RobotProfile.preview_ranges (see
        # its own docstring), ServoBus.deg_limits() from a loaded calibration
        # file; either way this loop doesn't need to know which.
        for name in self.robot_profile.joint_order:
            lo, hi = bus.deg_limits(name)
            self.joint_panel.set_limits(name, lo, hi)
            self.joint_deg_ranges[name] = (lo, hi)
        self.follower_open_directions = self._open_directions(bus)
        self._apply_persisted_gripper_torque_limit()

    @staticmethod
    def _open_directions(bus) -> dict[str, bool]:
        return {
            name: cal.opens_with_rising_ticks
            for name, cal in bus.calibration.items()
            if cal.opens_with_rising_ticks is not None
        }

    def _on_robot_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Robot error: {message}")
        self.session_logger.log_event(f"ERROR: {message}")

    def _on_torque(self, enabled: bool) -> None:
        if self.robot_worker:
            self.robot_worker.request_torque(enabled)
            self.session_logger.log_event(f"Follower: torque {'ENABLED' if enabled else 'disabled'}")
        self.follower_torque_enabled = enabled
        if not enabled:
            self._disengage_teleop("follower torque off")
            self._gravity_off("follower torque off")
        self._update_status_chips()

    def _on_positions_updated(self, positions: dict[str, float]) -> None:
        """Cheap on purpose - just a dict merge. Runs at the robot worker's
        full 60Hz; widget/twin refresh happens separately at a bounded rate
        via _refresh_ui(), so this can never fall behind."""
        self.current_positions.update(positions)
        self.session_logger.log_positions("follower", positions)  # internally throttled to 2Hz
        # Log the gripper's commanded value next to its measured one, at the
        # same throttled rate. "Won't open" and "was never asked to open" look
        # identical in a log of measured positions alone.
        if self.robot_worker and "gripper" in self.robot_worker.last_goals:
            self.session_logger.log_positions(
                "goal", {"gripper": self.robot_worker.last_goals["gripper"]}
            )

    def _on_goal_changed(self, name: str, degrees: float) -> None:
        """Fired by a slider/spinbox edit - only takes effect in Manual mode
        (sliders are disabled in the other modes, but this guard is cheap
        insurance against anything re-enabling them)."""
        if self.control_source != "manual" or self._playback_index is not None:
            return
        self.current_positions[name] = degrees
        if self.robot_worker:
            self.robot_worker.request_goal(name, degrees)

    # ---------------------------------------------------------------- top bar / pages
    PAGE_STAGE = 0
    PAGE_SETUP = 1
    DRAWER_PANELS = {"waypoints": "teaching_panel", "telemetry": "telemetry_panel", "tune": "dm_tune_panel"}

    def _show_motors_and_ids(self) -> None:
        self.setup_hub.show_section("Motors and ids")

    def _refresh_dm_calibration_summary(self) -> None:
        settings = self._load_settings()
        grip = settings.get("dm_follower_calibrated_ranges", {}).get("finger_left")
        self.dm_calibration_page.set_gripper_summary(
            f"{grip[0]:.1f} to {grip[1]:.1f} deg  (span {abs(grip[1] - grip[0]):.1f})" if grip
            else "not measured yet - using the profile's assumed range"
        )
        native = settings.get("fashionstar_leader_native_ranges", {})
        self.dm_calibration_page.set_leader_summary(
            "\n".join(f"{name}: {lo:.1f} to {hi:.1f}" for name, (lo, hi) in native.items())
            if native else "using vendor default ranges - sweep to measure this unit"
        )

    def _on_nav_toggled(self, key: str, checked: bool) -> None:
        if key == "setup":
            self.pages.setCurrentIndex(self.PAGE_SETUP if checked else self.PAGE_STAGE)
            if checked:
                for other in self.DRAWER_PANELS:
                    self.top_bar.set_nav_checked(other, False)
                self.drawer.hide()
            return
        # waypoints / telemetry / tune share one drawer on the stage
        if checked:
            for other in self.DRAWER_PANELS:
                if other != key:
                    self.top_bar.set_nav_checked(other, False)
            self.top_bar.set_nav_checked("setup", False)
            self.pages.setCurrentIndex(self.PAGE_STAGE)
            self.drawer.setCurrentWidget(getattr(self, self.DRAWER_PANELS[key]))
            self.drawer.show()
        else:
            self.drawer.hide()

    # ---------------------------------------------------------------- command palette
    def _palette_actions(self) -> list[PaletteAction]:
        """Only navigation, view settings, control-source choices and the existing
        calibration dialogs - nothing here starts a motion or engages teleop."""
        is_dm = self.robot_profile.key == "rebot_b601_dm"
        buttons = self.top_bar.nav_buttons

        def show(key: str):
            return lambda: buttons[key].setChecked(True)

        def to_stage():
            for button in buttons.values():
                button.setChecked(False)

        def setup_section(title: str):
            def run():
                buttons["setup"].setChecked(True)
                self.setup_hub.show_section(title)
            return run

        actions = [
            PaletteAction("Back to stage", "close drawers and Setup", to_stage, "home main"),
            PaletteAction("Open Waypoints", "drawer", show("waypoints"), "teach record playback"),
            PaletteAction("Open Telemetry", "drawer", show("telemetry"), "graph table csv"),
            PaletteAction("Open Setup", "hub", show("setup"), "settings"),
        ]
        if is_dm:
            actions.append(PaletteAction("Open Tune", "drawer: MIT gains, presets, tracking", show("tune"), "kp kd gain pid"))
        for title in self.setup_hub.section_titles():
            actions.append(PaletteAction(f"Setup: {title}", "", setup_section(title)))
        actions += [
            PaletteAction("Stop: follower torque off, control to Manual", "Ctrl+Shift+Space",
                          self._on_emergency_stop, "emergency halt"),
            PaletteAction("Control source: Manual", "jog panel", self.control_source_panel.manual_radio.click),
            PaletteAction("Control source: Leader arm", "standby - does not engage teleop",
                          self.control_source_panel.leader_radio.click, "teleoperate"),
            PaletteAction("Control source: Gamepad", "", self.control_source_panel.gamepad_radio.click),
            PaletteAction("Control source: Keyboard", "", self.control_source_panel.keyboard_radio.click),
            PaletteAction("Toggle ghost target", "view", self.twin_panel.ghost_check.toggle),
            PaletteAction("Toggle axes", "view", self.twin_panel.axes_check.toggle),
            PaletteAction("Toggle HUD overlay", "view", self.twin_panel.hud_check.toggle),
            PaletteAction("Toggle camera card", "view", self.twin_panel.camera_check.toggle),
            PaletteAction("Reset 3D view", "camera angle", self.twin_panel.reset_view_requested.emit),
            PaletteAction("Reset card layout", "positions and minimised cards", self.stage.reset_layout),
            PaletteAction("About", "creator, version, tech stack", self._show_about, "credits license"),
        ]
        if is_dm:
            actions += [
                PaletteAction("Calibrate follower gripper range", "follower connected, torque off",
                              self._on_dm_gripper_calibrate_requested, "sweep"),
                PaletteAction("Calibrate leader (Star Arm 102)", "leader connected",
                              self._on_leader_calibrate_requested, "sweep"),
            ]
        return actions

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    def _open_palette(self) -> None:
        palette = CommandPalette(self)
        palette.set_actions(self._palette_actions())
        palette.exec()

    def _on_emergency_stop(self) -> None:
        """Follower torque OFF and control back to Manual. Forcing Manual matters:
        with a leader relay still active, re-enabling torque later would make the
        arm jump straight to wherever the leader is. The leader itself is always
        free-spinning, so it needs nothing."""
        if self.robot_worker:
            self.robot_worker.request_torque(False)
        self.follower_torque_enabled = False
        self._disengage_teleop("Stop pressed")
        self._gravity_off("Stop pressed")
        self.control_source_panel.force_manual()
        self._jog_release_all()
        self._on_stop_sequence()
        self.statusBar().showMessage("STOP: follower torque off, control set to Manual")
        self.session_logger.log_event("STOP pressed: follower torque disabled, control source -> manual")
        self._update_status_chips()

    # ---------------------------------------------------------------- teleop engage gate
    def _teleop_alignment(self) -> tuple[bool, str, list[float | None]]:
        """(can_engage, explanation, per-arm-joint error in degrees or None).

        Engaging makes the follower chase the leader at once, so it is only
        allowed when both arms are live, the follower has torque (else Engage
        would say nothing about the arm it is about to move), and every ARM
        joint is already within the tolerance. The gripper is excluded: it is
        allowed to differ, and a gripper closing is not the hazard here."""
        joints = list(self.robot_profile.joint_order[:-1])
        errors: list[float | None] = []
        for name in joints:
            if name in self._leader_converted and name in self.current_positions:
                errors.append(abs(self._leader_converted[name] - self.current_positions[name]))
            else:
                errors.append(None)
        tol = self.teleop_tolerance_deg
        if not self._leader_connected:
            return False, "Connect the leader arm first.", errors
        if not self._follower_connected:
            return False, "Connect the follower first.", errors
        if not self.follower_torque_enabled:
            return False, "Turn follower torque ON first.", errors
        if any(e is None for e in errors):
            return False, "Waiting for position data from both arms.", errors
        worst_index = max(range(len(errors)), key=lambda i: errors[i])
        worst = errors[worst_index]
        if worst > tol:
            return False, (f"Move the leader closer to the follower: {joints[worst_index]} is "
                           f"{worst:.0f} deg apart (limit {tol:.0f}). The ghost shows the leader pose."), errors
        return True, f"Aligned (worst joint {worst:.1f} deg). Safe to engage.", errors

    # ---------------------------------------------------------------- gravity feed-forward
    def _gravity_path(self) -> str | None:
        """The B601-DM MJCF the gravity torques are computed from (the same model as the twin)."""
        import os
        path = (self._load_settings().get("mjcf_path_by_profile", {}).get("rebot_b601_dm")
                or PROFILES["rebot_b601_dm"].default_mjcf_path)
        return path if path and os.path.isfile(path) else None

    def _gravity_can_run(self) -> tuple[bool, str]:
        worker = self.robot_worker
        if not isinstance(worker, DmRobotWorker) or not self._follower_connected:
            return False, "Connect the B601-DM follower first."
        if getattr(worker, "control_mode", None) != Control_Type.MIT:
            return False, "Gravity feed-forward is for MIT mode (choose MIT above and reconnect)."
        if not self.follower_torque_enabled:
            return False, "Turn torque ON first."
        return True, ""

    def _refresh_gravity_ui(self) -> None:
        can, reason = self._gravity_can_run()
        if not can and self._gravity_ok:
            self._gravity_off(reason)
        if can and self._gravity_ok:
            note = "Check passed. Raise Amount slowly (watch the tracking chart); it ramps in over a few seconds."
        elif can:
            note = "Run the check with the arm still."
        else:
            note = reason
        self.dm_tune_panel.set_gravity_controls(can, can and self._gravity_ok, note)

    def _gravity_off(self, reason: str) -> None:
        """Switch the feed-forward off at once and make it earn its way back with a new check."""
        was_on = self.dm_tune_panel.gravity_percent() > 0
        if isinstance(self.robot_worker, DmRobotWorker):
            self.robot_worker.request_gravity_percent(0)
        self.dm_tune_panel.reset_gravity()
        self._gravity_ok = False
        if was_on:
            self.session_logger.log_event(f"Gravity feed-forward OFF ({reason})")
            self.statusBar().showMessage(f"Gravity feed-forward off: {reason}")

    def _gravity_model_for_check(self) -> GravityModel | None:
        if self._gravity_model is None:
            path = self._gravity_path()
            if path:
                try:
                    self._gravity_model = GravityModel(path, list(self.robot_profile.joint_order))
                except Exception:   # an unreadable MJCF just means no check, so no feed-forward
                    self._gravity_model = None
        return self._gravity_model

    def _on_gravity_check(self) -> None:
        """Compare the model's holding torques with what the motors report. At rest the
        torque a motor outputs equals the torque gravity asks of it, so the two must agree
        in sign (and roughly in size). Anything else - a flipped sign convention, a joint
        resting on a stop, a wrong model - leaves the feed-forward locked."""
        self._gravity_ok = False
        can, reason = self._gravity_can_run()
        if not can:
            self.dm_tune_panel.set_gravity_result([reason], False)
            return
        model = self._gravity_model_for_check()
        telemetry = self._last_telemetry
        if model is None or not telemetry:
            self.dm_tune_panel.set_gravity_result(["No model or no telemetry yet (wait a second and retry)."], False)
            return
        positions = {n: v["position"] for n, v in telemetry.items() if "position" in v}
        expected = model.torques(positions)
        lines: list[str] = []
        eligible, ok = 0, True
        verified: set[str] = set()
        for joint in self.robot_profile.arm_joints:
            m, t = expected.get(joint), telemetry.get(joint)
            if m is None or t is None:
                continue
            measured, speed = t.get("load"), t.get("velocity", 0.0)
            if abs(m) < GRAVITY_CHECK_MIN_NM:
                lines.append(f"{joint}: model {m:+.1f} N*m (too small to check)")
                continue
            if measured is None or abs(speed) > GRAVITY_CHECK_MAX_SPEED_DEG_S:
                # not judged, and so NOT given feed-forward: only verified joints are
                lines.append(f"{joint}: model {m:+.1f} N*m - moving ({abs(speed or 0):.0f} deg/s), skipped: no feed-forward on this joint")
                continue
            ratio = abs(measured) / abs(m)
            agrees = measured * m > 0 and GRAVITY_CHECK_RATIO[0] <= ratio <= GRAVITY_CHECK_RATIO[1]
            if agrees:
                eligible += 1
                verified.add(joint)
            else:
                ok = False
            lines.append(f"{joint}: model {m:+.1f}  measured {measured:+.1f} N*m  {'OK' if agrees else 'MISMATCH'}")
        if eligible == 0:
            ok = False
            lines.append(f"No joint carries at least {GRAVITY_CHECK_MIN_NM:.0f} N*m here: move the arm to a pose that loads it, hold still, check again.")
        self._gravity_ok = ok
        self._gravity_verified = verified if ok else set()
        self.dm_tune_panel.set_gravity_result(lines, ok)
        self.session_logger.log_event("Gravity check " + ("PASSED" if ok else "NOT passed") + ": " + "; ".join(lines))
        self._refresh_gravity_ui()

    def _on_gravity_percent(self, percent: int) -> None:
        if percent == 0:
            if isinstance(self.robot_worker, DmRobotWorker):
                self.robot_worker.request_gravity_percent(0)
            return
        can, reason = self._gravity_can_run()
        if not (can and self._gravity_ok):
            self._gravity_off("check not passed" if can else reason)
            return
        self.robot_worker.request_gravity_joints(self._gravity_verified)
        self.robot_worker.request_gravity_percent(percent)
        self.session_logger.log_event(f"Gravity feed-forward -> {percent} % on {sorted(self._gravity_verified)}")

    def _push_speed(self, _value: int = 0) -> None:
        """The Jog panel's Speed slider is THE speed setting: besides scaling jog
        motion it caps how fast a Damiao follower chases any target (POS_VEL), so
        it also slows teleop and waypoint playback. Feetech follower: unchanged."""
        worker = self.robot_worker
        if worker is not None and hasattr(worker, "request_speed_percent"):
            worker.request_speed_percent(self.jog_panel.speed_slider.value())

    def _nudge_allowed(self) -> tuple[bool, str]:
        if not isinstance(self.robot_worker, DmRobotWorker) or not self._follower_connected:
            return False, "connect the follower first."
        if not self.follower_torque_enabled:
            return False, "turn torque ON first."
        if self.teleop_engaged or self.control_source != "manual":
            return False, "switch Control source to Manual."
        if self._playback_index is not None:
            return False, "a waypoint sequence is playing."
        return True, ""

    def _on_nudge_requested(self, joint: str, delta: float) -> None:
        allowed, reason = self._nudge_allowed()
        if not allowed:
            self.statusBar().showMessage(f"Nudge refused: {reason}")
            return
        base = self.robot_worker.last_goals.get(joint, self.current_positions.get(joint))
        if base is None:
            return
        target = base + delta
        lo_hi = self.joint_deg_ranges.get(joint)
        if lo_hi:
            target = max(lo_hi[0], min(lo_hi[1], target))
        self.robot_worker.request_goal(joint, target)
        self.session_logger.log_event(f"Nudge: {joint} {delta:+.1f} deg -> goal {target:.1f}")

    def _update_teleop_ui(self) -> None:
        allowed, reason = self._nudge_allowed()
        self.dm_tune_panel.set_nudge_state(allowed, reason)
        self._refresh_gravity_ui()
        if isinstance(self.robot_worker, DmRobotWorker) and not self.drawer.isHidden() \
                and self.drawer.currentWidget() is self.dm_tune_panel:
            joint = self.dm_tune_panel.tracking_joint()
            goal = self.robot_worker.last_goals.get(joint)
            measured = self.current_positions.get(joint)
            if goal is not None and measured is not None:
                self.dm_tune_panel.add_tracking_sample(goal, measured)
        can, text, errors = self._teleop_alignment()
        if self.teleop_engaged:
            text = "Teleop engaged: the follower is tracking the leader."
        self.control_source_panel.set_teleop_state(self.teleop_engaged, can, text, errors, self.teleop_tolerance_deg)

    def _on_engage_toggled(self, want: bool) -> None:
        if not want:
            self._disengage_teleop("button")
            return
        can, text, _errors = self._teleop_alignment()
        if not can or self.control_source != "leader":
            self.statusBar().showMessage(f"Cannot engage teleop: {text}")
            return
        self.teleop_engaged = True
        self.statusBar().showMessage("Teleop engaged")
        self.session_logger.log_event(
            f"Teleop: ENGAGED (arms aligned within {self.teleop_tolerance_deg:.0f} deg)"
        )
        self._update_teleop_ui()
        self._update_status_chips()

    def _disengage_teleop(self, reason: str) -> None:
        if not self.teleop_engaged:
            return
        self.teleop_engaged = False
        self.session_logger.log_event(f"Teleop: disengaged ({reason})")
        self.statusBar().showMessage(f"Teleop disengaged: {reason}")
        self._update_teleop_ui()
        self._update_status_chips()

    def _on_teleop_tolerance_changed(self, degrees: float) -> None:
        self.teleop_tolerance_deg = degrees
        self._save_setting("teleop_align_tolerance_deg", degrees)
        self._update_teleop_ui()

    def _update_status_chips(self) -> None:
        bar = self.top_bar
        bar.set_chip("follower", "Follower online" if self._follower_connected else "Follower offline",
                     "good" if self._follower_connected else "off")
        bar.set_chip("leader", "Leader online" if self._leader_connected else "Leader offline",
                     "good" if self._leader_connected else "off")
        torque = self._follower_connected and self.follower_torque_enabled
        bar.set_chip("torque", "Torque ON" if torque else "Torque off", "warn" if torque else "off")
        if self.control_source == "leader":
            bar.set_chip("source", "Teleop engaged" if self.teleop_engaged else "Leader (standby)",
                         "good" if self.teleop_engaged else "warn")
        else:
            bar.set_chip("source", self.control_source.capitalize(),
                         "off" if self.control_source == "manual" else "good")

    # ---------------------------------------------------------------- control source / teleoperation
    def _on_control_source_changed(self, source: str) -> None:
        self.control_source = source
        if source != "leader":
            self._disengage_teleop(f"control source -> {source}")
        self._apply_control_source_lock()
        self._update_status_chips()
        if source != "keyboard":
            self._held_keys.clear()
            self.keyboard_jog_panel.clear_all()
        self.statusBar().showMessage(f"Control source: {source}")
        self.session_logger.log_event(f"Control source changed to: {source}")

    def _apply_control_source_lock(self) -> None:
        manual = self.control_source == "manual"
        # Manual = the Jog panel. Disabling a button mid-press can swallow its
        # release, so drop any motion in progress first rather than trust it.
        if not manual:
            self._jog_release_all()
        self.jog_panel.set_input_enabled(manual)

    def _on_leader_connect(self, port: str, calibration_path: str) -> None:
        if not port:
            QMessageBox.warning(self, "No port", "Pick a serial port first.")
            return

        if self.robot_profile.key == "rebot_b601_dm":
            self._on_fashionstar_leader_connect(port)
            return

        if not calibration_path:
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
        self.leader_deg_ranges.clear()
        self.control_source_panel.set_leader_connected(False)
        self._leader_connected = False
        self._leader_converted.clear()
        self._disengage_teleop("leader disconnected")
        self._update_status_chips()

    def _on_leader_connection_changed(self, connected: bool) -> None:
        self.control_source_panel.set_leader_connected(connected)
        self._leader_connected = connected
        if not connected:
            self._leader_converted.clear()
            self._disengage_teleop("leader disconnected")
        self._update_status_chips()
        if connected and self.leader_worker:
            # the leader is meant to be moved by hand - always free-spinning
            self.leader_worker.request_torque(False)
            self.statusBar().showMessage(f"Leader connected on {self.leader_worker.port}")
            self.session_logger.log_event(f"Leader: connected on {self.leader_worker.port}")
            if self.leader_worker.bus:
                for name in self.robot_profile.joint_order:
                    self.leader_deg_ranges[name] = self.leader_worker.bus.deg_limits(name)
                self.leader_open_directions = self._open_directions(self.leader_worker.bus)
            self._log_gripper_relay_decision()
            # only source joint_deg_ranges (follower's/twin's) from the leader
            # if the follower isn't already providing them - follower's own
            # calibration is what actually matters once both are connected.
            if not self.robot_worker and self.leader_worker.bus:
                for name in self.robot_profile.joint_order:
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
        something.

        Either way, every value is routed through _leader_deg_to_follower_deg
        first - confirmed a real gap here doing leader-only bring-up without
        a follower connected: the preview branch used to show RAW leader
        degrees untouched, silently skipping the gripper invert override,
        edge-snap and relay trim entirely, so what the preview showed didn't
        match what teleoperating would actually send once a follower joined."""
        self.session_logger.log_positions("leader", positions)  # internally throttled to 2Hz
        converted = {name: self._leader_deg_to_follower_deg(name, degrees) for name, degrees in positions.items()}
        self._leader_converted.update(converted)
        if self.teleop_engaged and self.control_source == "leader" and self.robot_worker and self._playback_index is None:
            for name, degrees in converted.items():
                self.robot_worker.request_goal(name, degrees)
        elif self.robot_worker is None:
            # leader-only bring-up: show the leader's pose. With a follower connected the
            # follower's own feedback is the truth, and the leader is only shown as a ghost.
            self.current_positions.update(converted)

    # Fraction-remap alone still leaves the gripper joint short of true
    # full-open/full-close in practice: the leader's jaw handle (the
    # ORIGINAL, unmodified mechanism) has no crisp, tactile hard stop the way
    # a UI slider has a definite end-of-travel - a hand squeeze routinely
    # stops a few percent short of the calibrated extreme, especially now
    # that the follower's own mechanism (the new belt-driven gripper) feels
    # nothing like what the operator's hand is actually holding. This snaps
    # the outer band of the leader's travel to the true extreme, so "squeeze
    # as far as it goes" reliably means fully closed on the follower even if
    # the hand doesn't hit the exact calibrated endpoint.
    #
    # ONLY for the gripper joint (see _leader_deg_to_follower_deg's own
    # check) - a rigid joint (shoulder/elbow/wrist) has no "mushy end"
    # problem to correct for, and snapping it would instead be a hazard: the
    # moment the leader arm drifts into the outer band of ITS OWN calibrated
    # range (easy after any recalibration shifts that range even slightly),
    # the follower would suddenly jump straight to its hard mechanical limit
    # instead of tracking the leader smoothly - which is exactly what "tiba-
    # tiba ga terkendali" was: this snap firing on a joint it was never meant
    # to apply to.
    LEADER_EDGE_SNAP_FRACTION = 0.06

    # Last-resort manual override, normally empty. "range_min"/"range_max"
    # cannot say WHICH end is physically closed - that depends on the pose
    # held during homing and which way the sweep ran, so it comes out
    # differently from one calibration to the next, and a hardcoded entry
    # here goes stale the moment either side is recalibrated (it did, twice).
    # Prefer the manual 2-point capture, which records the answer in the
    # calibration file and lets _relay_direction_flipped() derive this
    # instead of guessing. Only add a joint here if that isn't available.
    INVERTED_LEADER_JOINTS: set[str] = set()

    # Kept OUT of the calibration files on purpose: this is the operator's
    # answer about the physical setup, not a property of either arm's
    # calibration, and putting it in a calibration file would mean every
    # recalibration silently discards it - which is precisely the loop this
    # switch exists to end.
    GRIPPER_INVERT_SETTINGS_PATH = "gui_settings.json"

    def _load_settings(self) -> dict:
        try:
            with open(self.GRIPPER_INVERT_SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_setting(self, key: str, value) -> None:
        data = self._load_settings()
        data[key] = value
        try:
            with open(self.GRIPPER_INVERT_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, sort_keys=True)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not save {key}: {exc}")

    def _load_gripper_invert(self) -> bool:
        return bool(self._load_settings().get("gripper_relay_invert", False))

    def _on_gripper_invert_toggled(self, enabled: bool) -> None:
        self.gripper_invert_override = enabled
        self._save_setting("gripper_relay_invert", enabled)
        self.session_logger.log_event(f"Gripper relay invert -> {enabled}")
        self._log_gripper_relay_decision()

    def _on_relay_trim_changed(self, joint: str, degrees: float) -> None:
        if degrees:
            self.relay_trim_deg[joint] = degrees
        else:
            self.relay_trim_deg.pop(joint, None)
        self._save_setting("relay_trim_deg", self.relay_trim_deg)
        self.session_logger.log_event(f"Relay trim: {joint} = {degrees:+.1f} deg")

    def _apply_persisted_gripper_torque_limit(self) -> None:
        """Torque_Limit lives at address 48 - SRAM, not EEPROM - so the servo
        forgets it every time it loses power, silently reverting to its own
        default. That made a joint's usable force depend on whether the arm
        had been power-cycled since the value was last applied, which reads
        as "it behaves differently every restart" with nothing in software
        having changed. Re-asserting every persisted value on every connect
        is what makes an applied value actually mean something.

        Keyed per joint (not just "gripper" - the name stuck around from
        when this only handled the gripper, but every joint's Torque_Limit
        is exactly as volatile) since a payload added anywhere else on the
        arm needs the same headroom to survive a power cycle."""
        if not self.robot_worker:
            return
        settings = self._load_settings()
        limits = dict(settings.get("torque_limits", {}))
        legacy = settings.get("gripper_torque_limit")  # pre-generalisation key
        if legacy is not None and "gripper" not in limits:
            limits["gripper"] = legacy
        for joint, value in limits.items():
            if joint not in JOINT_ORDER:
                continue
            self.robot_worker.request_register_write("Torque_Limit", int(value), joint)
            self.session_logger.log_event(
                f"Re-applied persisted Torque_Limit={value} -> {joint} (SRAM, lost on power cycle)"
            )

    def _log_gripper_relay_decision(self) -> None:
        """Write down what the relay actually decided, so a "gripper goes the
        wrong way" report can be settled from the log instead of inferred from
        tick readings. Every input to the decision is stale-able (a JSON edited
        after connect, a range sourced from the wrong arm, a process still
        running pre-edit code), and guessing which one it was has cost several
        rounds of hardware testing already."""
        name = "gripper"
        leader_dir = self.leader_open_directions.get(name)
        follower_dir = self.follower_open_directions.get(name)
        lo, hi = self.leader_deg_ranges.get(name, (None, None))
        flo, fhi = self.joint_deg_ranges.get(name, (None, None))
        parts = [
            f"leader_opens_rising={leader_dir}",
            f"follower_opens_rising={follower_dir}",
            f"flip={self._relay_direction_flipped(name)}",
            f"user_invert={self.gripper_invert_override}",
            f"leader_deg_range=({lo}, {hi})",
            f"follower_deg_range=({flo}, {fhi})",
            f"follower_range_sourced_from={'follower' if self.robot_worker else 'LEADER (follower not connected!)'}",
        ]
        if lo is not None and flo is not None:
            for label, deg in (("leader_at_lo", lo), ("leader_at_hi", hi)):
                parts.append(f"{label}->follower_deg={self._leader_deg_to_follower_deg(name, deg):.1f}")
        self.session_logger.log_event("Relay(gripper): " + "  ".join(parts))

    def _relay_direction_flipped(self, name: str) -> bool:
        """Whether leader and follower disagree about which encoder direction
        opens this joint, decided from the closed/open ticks that the manual
        2-point capture recorded on each side. Returns False when either side
        never recorded them (any swept calibration), which keeps the previous
        pass-the-fraction-straight-through behaviour for every joint that
        isn't captured.

        The gripper's user override is XORed on top. Deriving this direction
        automatically failed repeatedly in practice, and always for the same
        reason: whichever physical extreme ends up on the low tick is decided
        by the pose the jaw happened to be held in during "Set middle", and
        the human labelling of that pose is exactly what's unreliable (the
        leader's handle hangs OPEN at rest, so homing it untouched records
        "open" as the closed end). A switch the operator flips once, after
        seeing which way it actually moved, needs no such labelling to be
        correct - and it persists, so a later recalibration on either side
        can't silently undo it."""
        flipped = False
        leader_dir = self.leader_open_directions.get(name)
        follower_dir = self.follower_open_directions.get(name)
        if leader_dir is None or follower_dir is None:
            flipped = name in self.INVERTED_LEADER_JOINTS
        else:
            flipped = leader_dir != follower_dir
        # "gripper" for SO-101, "finger_left" for reBot B601-DM - whichever
        # joint the current profile treats as the one actual gripper actuator
        # (profile_arm_joints/RobotProfile.arm_joints both exclude it, so
        # joint_order[-1] reliably names it for either profile). Confirmed a
        # real gap here on real B601-DM leader hardware: this used to check
        # the literal string "gripper" only, so the invert checkbox silently
        # never did anything for this robot at all.
        if name == self.robot_profile.joint_order[-1] and self.gripper_invert_override:
            flipped = not flipped
        return flipped

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
        trim = self.relay_trim_deg.get(name, 0.0)
        if leader_hi <= leader_lo or follower_hi <= follower_lo:
            return leader_degrees + trim
        fraction = (leader_degrees - leader_lo) / (leader_hi - leader_lo)
        if self._relay_direction_flipped(name):
            fraction = 1.0 - fraction
        # Same joint-name generalization as _relay_direction_flipped - the
        # gripper is whichever joint is last in the current profile's
        # joint_order ("gripper" for SO-101, "finger_left" for B601-DM), not
        # always the literal string "gripper".
        if name == self.robot_profile.joint_order[-1]:
            if fraction < self.LEADER_EDGE_SNAP_FRACTION:
                fraction = 0.0
            elif fraction > 1.0 - self.LEADER_EDGE_SNAP_FRACTION:
                fraction = 1.0
        fraction = max(0.0, min(1.0, fraction))
        target = follower_lo + fraction * (follower_hi - follower_lo)
        # Trim is applied AFTER the remap and then re-clamped, so a trim can
        # shift the whole travel but can never command the follower past its
        # own calibrated limits - the cost is that the last `trim` degrees at
        # one end saturate, which is the correct trade for a joint whose zero
        # was simply set in the wrong place.
        return max(follower_lo, min(follower_hi, target + trim))

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
    def _on_camera_start(self, source) -> None:
        from core.basler_camera import BaslerCameraWorker, is_basler_source, serial_of
        from core.realsense_camera import RealSenseCameraWorker, is_realsense_source, parse_source

        if is_realsense_source(source):
            serial, mode = parse_source(source)
            self.camera_panel.set_busy(True, f"starting RealSense {serial} ({mode})...")
            worker = RealSenseCameraWorker(serial, mode)
        elif is_basler_source(source):
            self.camera_panel.set_busy(True, f"starting Basler camera {serial_of(source)}...")
            worker = BaslerCameraWorker(serial_of(source))
        else:
            self.camera_panel.set_busy(True, f"starting device #{source}...")
            worker = CameraWorker(source)
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

    # ---------------------------------------------------------------- keyboard jog
    def keyPressEvent(self, event) -> None:
        if not event.isAutoRepeat() and event.key() in KEY_JOG_MAP:
            self._held_keys.add(event.key())
            self.keyboard_jog_panel.set_key_active(event.key(), True)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if not event.isAutoRepeat() and event.key() in KEY_JOG_MAP:
            self._held_keys.discard(event.key())
            self.keyboard_jog_panel.set_key_active(event.key(), False)
            return
        super().keyReleaseEvent(event)

    def changeEvent(self, event) -> None:
        # A key physically still held when the window loses focus (alt-tab,
        # clicking another app) never generates its keyReleaseEvent - without
        # this, that joint would jog forever until the key is pressed again.
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self._held_keys.clear()
            self.keyboard_jog_panel.clear_all()
        super().changeEvent(event)

    def _keyboard_jog_tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_keyboard_tick
        self._last_keyboard_tick = now
        if self.control_source != "keyboard" or self._playback_index is not None or not self._held_keys:
            return
        for key in self._held_keys:
            name, sign = KEY_JOG_MAP[key]
            delta = sign * KEYBOARD_MAX_DEG_PER_S * dt
            new_deg = self.current_positions.get(name, 0.0) + delta
            self._drive_joint_programmatically(name, new_deg)

    def _drive_joint_programmatically(self, name: str, degrees: float) -> None:
        """Used by the gamepad/keyboard jog paths that bypass the (disabled)
        slider widgets directly - updates state + sends the goal.

        Clamps to the calibrated range here as well as in write_goals_deg. The
        bus-level clamp already protects the hardware, but it clamps the raw
        ticks it sends, not this integrator: holding a jog key against a limit
        would otherwise keep adding degrees to current_positions forever, and
        the joint would then sit motionless for however long it took to unwind
        that phantom offset once the key was released and reversed. (With a
        follower connected the 60Hz position feedback overwrites the drift
        anyway - this is what makes jogging behave with twin-only preview and
        no hardware attached.)"""
        lo, hi = self.joint_deg_ranges.get(name, (-180.0, 180.0))
        degrees = max(lo, min(hi, degrees))
        self.current_positions[name] = degrees
        if self.robot_worker:
            self.robot_worker.request_goal(name, degrees)
        row = self.joint_panel.rows.get(name)
        if row:
            row.set_feedback_deg(degrees)

    # ---------------------------------------------------------------- jog panel (JAKA-style)
    #
    # Joint mode moves one joint at a time. World/Tool modes move the tool centre
    # point through space and let core/kinematics.py work out the joints. Either
    # way the result is a set of joint targets in degrees, driven through
    # _drive_joint_programmatically - the same path keyboard and gamepad jogging
    # use - so the calibrated-range clamp, the twin and the hardware all see it
    # exactly as they see any other source.
    def _build_kinematics(self, path: str) -> None:
        self.kinematics = None
        profile = self.robot_profile
        try:
            chain = KinematicChain(path, profile_arm_joints(profile), self._tcp_spec())
        except Exception as exc:  # bad/missing MJCF - the twin reports its own load error
            self.jog_panel.set_cartesian_available(False, f"World/Tool jogging unavailable: {exc}")
            return
        self.kinematics = chain
        self.jog_panel.set_cartesian_available(True)
        note = (
            f"TCP: {chain.tcp.description}. Pose is in the WORLD frame, measured on the twin model - "
            "if the real arm's calibrated ranges differ from the model's, real distances scale with them."
        )
        if chain.n < 6:
            note += (
                f" This arm has {chain.n} joints in its kinematic chain, so it cannot make every "
                "direction: buttons drawn dashed are only partly reachable from the current pose."
            )
        self.jog_panel.set_tcp_note(note)

    def _tcp_spec(self) -> TcpSpec:
        profile = self.robot_profile
        return TcpSpec(site=profile.tcp_site, body=profile.tcp_body, offset=profile.tcp_offset)

    def _jog_rate(self, mode: str, axis: str) -> float:
        """deg/s for a joint or rotation, mm/s for a translation, at the
        panel's current speed."""
        speed = self.jog_panel.speed_fraction()
        if mode == "joint":
            return JOG_MAX_JOINT_DEG_S * speed
        return (JOG_MAX_LINEAR_MM_S if axis in ("x", "y", "z") else JOG_MAX_ANGULAR_DEG_S) * speed

    def _on_jog_pressed(self, mode: str, axis: str, direction: int) -> None:
        if self.control_source != "manual" or self._playback_index is not None:
            return
        if mode != "joint" and self.kinematics is None:
            return
        now = time.monotonic()
        if not self._jog_active:
            # A fresh jog starts from wherever the arm actually is.
            self._jog_target = dict(self.current_positions)
            self._last_jog_tick = now
        step = self.jog_panel.step_size()
        expires = now + step / max(self._jog_rate(mode, axis), 1e-6) if step > 0 else None
        self._jog_active[(mode, axis)] = [direction, expires]

    def _on_jog_released(self, mode: str, axis: str, direction: int) -> None:
        entry = self._jog_active.get((mode, axis))
        # A step move runs to completion whatever the button does; only a held
        # (continuous) jog stops on release.
        if entry is not None and entry[1] is None and entry[0] == direction:
            del self._jog_active[(mode, axis)]
            self._jog_ghost_until = time.monotonic() + JOG_GHOST_LINGER_S

    def _on_jog_mode_changed(self, _mode: str) -> None:
        self._jog_release_all()

    def _jog_release_all(self) -> None:
        if self._jog_active:
            self._jog_ghost_until = time.monotonic() + JOG_GHOST_LINGER_S
        self._jog_active.clear()
        self.jog_panel.set_status("")

    def _jog_tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_jog_tick, 0.1)  # a stalled GUI must not turn into one huge lunge
        self._last_jog_tick = now
        if not self._jog_active:
            return
        if self.control_source != "manual" or self._playback_index is not None:
            self._jog_release_all()
            return
        # {(mode, axis): (direction, weight)} - weight is how much of THIS tick the
        # command is still owed. A held button gets all of it; a fixed-size step
        # only up to its own expiry. Without that partial last tick a step falls
        # short by up to one tick's travel (measured: a 5 mm step covering
        # 4.0 mm), a visible error on exactly the small steps this mode is for.
        # Snapshotted before expired entries are dropped, so the last sliver still
        # knows which way it was going.
        commands: dict[tuple[str, str], tuple[int, float]] = {}
        for key, (direction, expires) in list(self._jog_active.items()):
            if expires is None:
                commands[key] = (direction, 1.0)
                continue
            owed = min(now, expires) - (now - dt)
            commands[key] = (direction, max(0.0, min(1.0, owed / dt)) if dt > 0 else 0.0)
            if now >= expires:
                del self._jog_active[key]
                self._jog_ghost_until = now + JOG_GHOST_LINGER_S

        mode = next(iter(commands))[0]
        if mode == "joint":
            self._jog_joints(dt, commands)
        else:
            self._jog_cartesian(mode, dt, commands)
        if not self._jog_active:
            self.jog_panel.set_status("")

    def _jog_joints(self, dt: float, commands: dict[tuple[str, str], tuple[int, float]]) -> None:
        for (_mode, name), (direction, weight) in commands.items():
            if name not in self._jog_target:
                continue
            lo, hi = self.joint_deg_ranges.get(name, (-180.0, 180.0))
            target = self._jog_target[name] + direction * self._jog_rate("joint", name) * dt * weight
            target = max(lo, min(hi, target))
            self._jog_target[name] = target
            self._drive_joint_programmatically(name, target)

    def _jog_cartesian(
        self, frame: str, dt: float, commands: dict[tuple[str, str], tuple[int, float]]
    ) -> None:
        chain = self.kinematics
        if chain is None:
            return
        twist = np.zeros(6)
        for (_mode, axis), (direction, weight) in commands.items():
            i = AXES.index(axis)
            rate = self._jog_rate(frame, axis)
            twist[i] += direction * weight * (rate / 1000.0 if i < 3 else math.radians(rate))  # m/s, rad/s

        fractions = self._positions_to_fractions(self._jog_target)
        q = chain.fractions_to_q({n: fractions[n] for n in chain.joint_names if n in fractions})
        result = chain.step(q, twist, frame, dt)
        for name, fraction in chain.q_to_fractions(result.q).items():
            lo, hi = self.joint_deg_ranges.get(name, (-180.0, 180.0))
            degrees = lo + fraction * (hi - lo)
            self._jog_target[name] = degrees
            self._drive_joint_programmatically(name, degrees)

        if result.blocked:
            self.jog_panel.set_status(f"At joint limit: {', '.join(result.blocked)} - sliding along the boundary.")
        elif result.saturated:
            self.jog_panel.set_status("Near a singularity: speed reduced to keep joint speeds safe.")
        else:
            self.jog_panel.set_status("")

    def _update_cartesian_readout(self) -> None:
        """TCP pose and per-axis reachability for the Cartesian page, only while
        that page is actually showing."""
        chain = self.kinematics
        mode = self.jog_panel.mode()
        if chain is None:
            self.jog_panel.set_tcp_summary("")
            return
        fractions = self._positions_to_fractions()
        chain.set_q(chain.fractions_to_q({n: fractions[n] for n in chain.joint_names if n in fractions}))
        position, rotation = chain.tcp_pose()
        rpy = rpy_deg_from_matrix(rotation)
        if mode == "joint":
            self.jog_panel.set_tcp_summary(
                f"TCP (world)  X {position[0] * 1000:.0f}  Y {position[1] * 1000:.0f}  Z {position[2] * 1000:.0f} mm\n"
                f"Rx {rpy[0]:.0f}  Ry {rpy[1]:.0f}  Rz {rpy[2]:.0f} deg"
            )
            return
        self.jog_panel.set_pose(position, rpy)
        self.jog_panel.set_reach(chain.reachability(mode))

    # ---------------------------------------------------------------- twin overlays (ghost, axes)
    def _differs_from_live(self, positions: dict[str, float]) -> bool:
        return any(
            abs(degrees - self.current_positions.get(name, degrees)) > GHOST_MIN_DIFF_DEG
            for name, degrees in positions.items()
        )

    def _compute_ghost(self) -> dict[str, float] | None:
        """The pose the ghost arm should be drawn at, as twin fractions, or None
        for no ghost. In priority order: the waypoint playback is moving toward,
        then the target a jog is driving to (while it lasts and shortly after),
        then whichever waypoint is selected in the list. A candidate identical to
        where the arm already is is dropped - see GHOST_MIN_DIFF_DEG."""
        if not self.twin_panel.ghost_enabled():
            return None
        candidates: list[dict[str, float]] = []
        # Whenever a leader is online and not driving the follower, its pose is the ghost, whatever the
        # control source: the solid twin mirrors the REAL follower, so without this moving the leader
        # by hand showed nothing at all.
        if (not self.teleop_engaged and self._leader_connected and self._leader_converted
                and self._follower_connected):
            candidates.append(dict(self._leader_converted))   # where the follower WOULD go on Engage
        if self._playback_index is not None and self._playback_target_positions:
            candidates.append(self._playback_target_positions)
        if self._jog_target and self.robot_worker and (
            self._jog_active or time.monotonic() < self._jog_ghost_until
        ):
            candidates.append(self._jog_target)
        index = self.teaching_panel.selected_index()
        if index is not None and 0 <= index < len(self.waypoints) and self.teaching_panel.isEnabled():
            candidates.append(self.waypoints[index]["positions"])
        for positions in candidates:
            if self._differs_from_live(positions):
                return self._positions_to_fractions(positions)
        return None

    def _frame_overlay(self) -> str | None:
        if not self.twin_panel.axes_enabled() or self.kinematics is None:
            return None
        mode = self.jog_panel.mode()
        return mode if mode in ("world", "tool") else "world"

    # ---------------------------------------------------------------- UI refresh (bounded rate)
    def _refresh_ui(self) -> None:
        """The ONLY place sliders/spinboxes and the twin actually repaint.
        Runs at a fixed ~30Hz regardless of how fast position data is
        arriving from one or two robot workers - this is what keeps the main
        thread from ever falling behind."""
        self.joint_panel.update_feedback(self.current_positions)
        self.joint_panel.set_targets(dict(getattr(self.robot_worker, "last_goals", {}) or {}) if self.robot_worker else {})
        self._update_cartesian_readout()
        self._update_teleop_ui()
        if self.twin_worker:
            self.twin_worker.set_fractions(self._positions_to_fractions())
            self.twin_worker.set_ghost(self._compute_ghost())
            self.twin_worker.set_frame_overlay(self._frame_overlay())

    def _positions_to_fractions(self, positions: dict[str, float] | None = None) -> dict[str, float]:
        """Real degrees -> 0..1 fraction of that joint's OWN calibrated
        range, so the twin can map it into the MJCF's own joint range instead
        of assuming the two zero-references happen to agree (they don't, at
        least not for the gripper - see DigitalTwin.set_joint_fraction).

        `positions` defaults to the live pose; pass any other {joint: degrees}
        (a waypoint, a jog target) to get the same mapping for it."""
        fractions = {}
        gripper_name = self.robot_profile.joint_order[-1] if self.robot_profile.joint_order else None
        for name, degrees in (self.current_positions if positions is None else positions).items():
            lo, hi = self.joint_deg_ranges.get(name, (-180.0, 180.0))
            if hi <= lo:
                continue
            fraction = (degrees - lo) / (hi - lo)
            # A calibration sweep only ever records numeric min/max - it has
            # no way to know which physical extreme is open vs closed, so
            # this fraction has a 50/50 chance of landing backwards against
            # the twin MJCF's own convention for that joint. Confirmed wrong
            # specifically for this profile's real gripper on real hardware
            # (see RobotProfile.twin_gripper_fraction_inverted's own
            # comment) - flipping it here affects only what the TWIN draws,
            # never real hardware control.
            if name == gripper_name and self.robot_profile.twin_gripper_fraction_inverted:
                fraction = 1.0 - fraction
            fractions[name] = fraction
        return fractions

    # ---------------------------------------------------------------- digital twin
    def _on_twin_load(self, path: str) -> None:
        if not path:
            return
        self._build_kinematics(path)
        # Remembered per robot profile (not one single global path), so
        # switching the Robot combo back and forth doesn't keep discarding
        # a path the user deliberately typed in for one of them - covers
        # both this being reached from the user's own Browse+Load click and
        # from _on_robot_profile_changed's auto-load.
        mjcf_paths = dict(self._load_settings().get("mjcf_path_by_profile", {}))
        mjcf_paths[self.robot_profile.key] = path
        self._save_setting("mjcf_path_by_profile", mjcf_paths)
        if self.twin_worker:
            old_worker = self.twin_worker
            self.twin_worker = None
            old_worker.finished.connect(lambda: self._start_twin_worker(path))
            self._retire_worker(old_worker)
        else:
            self._start_twin_worker(path)

    def _on_twin_frame(self, frame) -> None:
        # A QueuedConnection's emit() posts its event to this thread's queue
        # at emit time - once posted, a later disconnect() can't retract it,
        # so the worker that's being retired can always land exactly one
        # more frame here after we've already moved on from it (confirmed
        # directly: disconnecting frame_ready in the retire path did not
        # stop this). self.sender() is that specific worker, tracked
        # per-connection by Qt regardless of what self.twin_worker points at
        # by the time this actually runs - comparing the two is what
        # actually discards a straggler instead of racing disconnect timing.
        if self.sender() is not self.twin_worker:
            return
        self.twin_panel.show_frame(frame)

    def _start_twin_worker(self, path: str) -> None:
        self.twin_worker = TwinWorker(
            path,
            joint_names=list(self.robot_profile.joint_order),
            tcp_spec=self._tcp_spec(),
            arm_joints=list(profile_arm_joints(self.robot_profile)),
        )
        self.twin_worker.frame_ready.connect(self._on_twin_frame)
        self.twin_worker.load_failed.connect(lambda msg: self.twin_panel.set_caption(f"failed to load: {msg}"))
        self.twin_worker.neutral_pose_ready.connect(self._on_neutral_pose_ready)
        # Re-connected per worker (not once in __init__) because a new scene
        # load retires the old TwinWorker and starts a fresh one - a signal
        # connected to the OLD worker's request_orbit/etc would silently stop
        # doing anything the moment that worker is retired, with nothing
        # visibly wrong (the mouse events still fire, they'd just vanish into
        # a QThread nobody drains anymore).
        self.twin_panel.orbit_requested.connect(self.twin_worker.request_orbit)
        self.twin_panel.pan_requested.connect(self.twin_worker.request_pan)
        self.twin_panel.zoom_requested.connect(self.twin_worker.request_zoom)
        self.twin_panel.reset_view_requested.connect(self.twin_worker.request_reset_camera)
        self.twin_panel.render_size_requested.connect(self.twin_worker.request_resize)
        self.twin_worker.request_resize(*self.twin_panel.render_size())
        self.twin_worker.start()
        self.twin_panel.set_caption(f"loaded: {path}")

    # ---------------------------------------------------------------- setup tab (first-time motor ids)
    #
    # Shares the physical port with the Control/Calibration tabs, so it holds
    # its own ServoBus only while its tab is actually connected - two open
    # handles on one COM port is an OS-level "access denied", not something
    # either side can recover from gracefully.
    def _on_setup_connect(self, port: str) -> None:
        if not port:
            QMessageBox.warning(self, "No port", "Pick a serial port first.")
            return
        if self.robot_worker or self.leader_worker or self.calibration_worker:
            QMessageBox.warning(
                self, "Port already in use",
                "Disconnect on the Control and Calibration tabs first - only one "
                "of them can hold the serial port at a time.",
            )
            return
        self.session_logger.log_event(f"Setup: connecting on {port}")
        self.setup_worker = SetupWorker(port)
        self.setup_worker.connected.connect(self.setup_panel.set_connected)
        self.setup_worker.error.connect(self._on_setup_error)
        self.setup_worker.progress.connect(self.setup_panel.set_progress)
        self.setup_worker.scan_finished.connect(self.setup_panel.set_scan_result)
        self.setup_worker.id_assigned.connect(
            lambda old, new: self.session_logger.log_event(f"Setup: servo id {old} -> {new}")
        )
        self.setup_worker.start()

    def _on_setup_disconnect(self) -> None:
        if self.setup_worker:
            worker = self.setup_worker
            self.setup_worker = None
            self.session_logger.log_event("Setup: disconnecting")
            self._retire_worker(worker)
        self.setup_panel.set_connected(False)

    def _on_setup_error(self, message: str) -> None:
        self.setup_panel.set_progress(f"error: {message}")
        self._on_robot_error(message)

    def _on_setup_scan(self, all_baudrates: bool) -> None:
        if self.setup_worker:
            self.setup_worker.request_scan(all_baudrates)

    def _on_setup_deep_scan(self) -> None:
        if self.setup_worker:
            self.setup_worker.request_deep_scan()

    def _on_setup_assign_id(self, current_id: int, new_id: int) -> None:
        if self.setup_worker:
            self.setup_worker.request_assign_id(current_id, new_id)

    def _on_setup_set_baudrate(self, motor_id: int, baudrate: int) -> None:
        if self.setup_worker:
            self.setup_worker.request_set_baudrate(motor_id, baudrate)

    # ---------------------------------------------------------------- reBot B601-DM CAN id setup
    def _on_dm_setup_connect(self, port: str) -> None:
        if not port:
            QMessageBox.warning(self, "No port", "Pick a serial port first.")
            return
        self.session_logger.log_event(f"DM setup: connecting on {port}")
        self.dm_setup_worker = DmSetupWorker(port)
        self.dm_setup_worker.connected.connect(self.dm_setup_panel.set_connected)
        self.dm_setup_worker.error.connect(self._on_dm_setup_error)
        self.dm_setup_worker.probe_result.connect(self.dm_setup_panel.set_probe_result)
        self.dm_setup_worker.id_assigned.connect(self._on_dm_id_assigned)
        self.dm_setup_worker.enabled_changed.connect(self._on_dm_enabled_changed)
        self.dm_setup_worker.pid_result.connect(self.dm_setup_panel.set_pid_result)
        self.dm_setup_worker.pid_written.connect(
            lambda: self.statusBar().showMessage("DM setup: PID gains saved")
        )
        self.dm_setup_worker.zero_set.connect(
            lambda: self.statusBar().showMessage("DM setup: zero position saved")
        )
        self.dm_setup_worker.verify_result.connect(self.dm_setup_panel.set_verify_result)
        self.dm_setup_worker.verify_done.connect(
            lambda: self.statusBar().showMessage("DM setup: verify all done")
        )
        self.dm_setup_worker.start()

    def _on_dm_setup_disconnect(self) -> None:
        if self.dm_setup_worker:
            worker = self.dm_setup_worker
            self.dm_setup_worker = None
            self.session_logger.log_event("DM setup: disconnecting")
            self._retire_worker(worker)
        self.dm_setup_panel.set_connected(False)

    def _on_dm_setup_error(self, message: str) -> None:
        self.statusBar().showMessage(f"DM setup error: {message}")
        self.session_logger.log_event(f"DM setup error: {message}")

    def _on_dm_setup_probe(self, current_id: int) -> None:
        if self.dm_setup_worker:
            self.dm_setup_worker.request_probe(current_id)

    def _on_dm_setup_assign(self, current_id: int, new_id: int, new_master_id: int) -> None:
        if self.dm_setup_worker:
            self.dm_setup_worker.request_assign(current_id, new_id, new_master_id)

    def _on_dm_setup_control_mode_changed(self, mode: str) -> None:
        self._save_setting("dm_control_mode", mode)
        self.session_logger.log_event(f"DM setup: follower control mode set to {mode}")

    def _on_dm_setup_mit_gains_changed(self, gains: dict[str, tuple[float, float]]) -> None:
        self._save_setting("dm_mit_gains", {name: list(pair) for name, pair in gains.items()})
        # Live retune, not just "takes effect on next Connect" - a connected
        # follower already in MIT mode picks up new kp/kd on its very next
        # motion command, no disconnect/reconnect (and no torque cycling)
        # needed. Harmless no-op if not connected or not a Damiao worker.
        if isinstance(self.robot_worker, DmRobotWorker):
            self.robot_worker.request_mit_gains(gains)

    def _on_dm_id_assigned(self, current_id: int, new_id: int, new_master_id: int) -> None:
        joint = self.dm_setup_panel.target_combo.currentText()
        mapping = dict(self._load_settings().get("dm_can_id_mapping", {}))
        mapping[joint] = {"can_id": new_id, "master_id": new_master_id}
        self._save_setting("dm_can_id_mapping", mapping)
        self.dm_setup_panel.set_mapping(mapping)
        # The motor now answers at new_id, not the current_id the panel
        # probed it at - confirmed a real gap here without this: Enable/PID
        # right after Assign (no re-probe in between) would still address
        # the OLD id, which for every joint except the one whose old and
        # new id happen to coincide no longer has anything answering there.
        # set_probe_result() also resets the PID fields/write-lock, which is
        # correct too - any values read at the OLD address aren't this
        # motor's current ones to trust.
        self.dm_setup_panel.set_probe_result(True, new_id)
        self.session_logger.log_event(f"DM setup: {joint} id {current_id:#04x} -> {new_id:#04x}")

    def _on_dm_setup_enable(self, motor_id: int) -> None:
        if self.dm_setup_worker:
            self.dm_setup_worker.request_enable(motor_id)

    def _on_dm_setup_set_zero(self, motor_id: int) -> None:
        if self.dm_setup_worker:
            self.dm_setup_worker.request_set_zero(motor_id)
            self.session_logger.log_event(f"DM setup: set zero for motor {motor_id:#04x}")

    def _on_dm_setup_verify_all(self, id_by_joint: dict[str, int]) -> None:
        if self.dm_setup_worker:
            self.dm_setup_worker.request_verify_all(id_by_joint)
            self.session_logger.log_event(f"DM setup: verifying {len(id_by_joint)} motor(s)")

    def _on_dm_setup_disable(self, motor_id: int) -> None:
        if self.dm_setup_worker:
            self.dm_setup_worker.request_disable(motor_id)

    def _on_dm_enabled_changed(self, enabled: bool) -> None:
        self.session_logger.log_event(f"DM setup: motor {'enabled' if enabled else 'disabled'}")

    def _on_dm_setup_read_pid(self, motor_id: int) -> None:
        if self.dm_setup_worker:
            self.dm_setup_worker.request_read_pid(motor_id)

    def _on_dm_setup_write_pid(
        self, motor_id: int, kp_asr: float, ki_asr: float, kp_apr: float, ki_apr: float
    ) -> None:
        if self.dm_setup_worker:
            self.dm_setup_worker.request_write_pid(motor_id, kp_asr, ki_asr, kp_apr, ki_apr)
            self.session_logger.log_event(
                f"DM setup: wrote PID for motor {motor_id:#04x} "
                f"(KP_ASR={kp_asr}, KI_ASR={ki_asr}, KP_APR={kp_apr}, KI_APR={ki_apr})"
            )

    # ---------------------------------------------------------------- calibration tab
    def _on_calibration_connect(self, port: str) -> None:
        if not port:
            QMessageBox.warning(self, "No port", "Pick a serial port first.")
            return
        if self.setup_worker or self.robot_worker:
            QMessageBox.warning(
                self, "Port already in use",
                "Disconnect on the Setup and Control tabs first - only one of "
                "them can hold the serial port at a time.",
            )
            return
        joints = self.calibration_panel.joints_to_calibrate()
        if not joints:
            QMessageBox.warning(self, "No joints selected", "Check at least one joint to calibrate.")
            return
        self.session_logger.log_event(f"Calibration: connecting on {port} (joints: {joints})")
        self.calibration_worker = CalibrationWorker(port, joints)
        self.calibration_worker.connected.connect(self.calibration_panel.set_connected)
        self.calibration_worker.connected.connect(
            lambda ok: self.session_logger.log_event(f"Calibration: {'connected on ' + port if ok else 'connect failed'}")
        )
        self.calibration_worker.error.connect(self._on_robot_error)
        self.calibration_worker.live_update.connect(self.calibration_panel.update_live_table)
        self.calibration_worker.finished_calibration.connect(self._on_calibration_finished)
        self.calibration_worker.point_captured.connect(
            lambda joint, label, raw: self.calibration_panel.set_gripper_capture_point(label, raw)
            if joint == "gripper" else None
        )
        self.calibration_worker.auto_calibrate_awaiting_label.connect(self._on_auto_calibrate_awaiting_label)
        self.calibration_worker.start()

        # Each connect gets a FRESH worker, whose manual_joints starts empty,
        # while the checkbox keeps whatever the user last ticked - so a still-
        # ticked box silently meant nothing to the new worker and the gripper
        # got swept anyway. That is exactly how a captured leader ended up
        # paired with a swept 24..4091 follower: same panel, box still ticked,
        # second arm never told. Re-assert the panel's state onto every new
        # worker, and drop the previous arm's captured ticks.
        self.calibration_panel.clear_gripper_captures()
        if self.calibration_panel.gripper_manual_enabled():
            self.calibration_worker.request_set_manual_joint("gripper", True)

    def _on_gripper_manual_mode_toggled(self, enabled: bool) -> None:
        if self.calibration_worker:
            self.calibration_worker.request_set_manual_joint("gripper", enabled)

    def _on_gripper_capture_requested(self, label: str) -> None:
        if self.calibration_worker:
            self.calibration_worker.request_capture_point("gripper", label)

    def _on_auto_calibrate_requested(self) -> None:
        if not self.calibration_worker:
            return
        self.statusBar().showMessage(
            "Auto-calibrating gripper (stall-safe) - driving to both extremes at reduced torque..."
        )
        self.session_logger.log_event("Calibration: auto-calibrate started for gripper")
        self.calibration_worker.request_auto_calibrate("gripper")

    def _on_auto_calibrate_awaiting_label(self, name: str, low_tick: int, high_tick: int) -> None:
        """The stall search found both extremes and is now holding the joint
        at low_tick (torque still on, at the reduced search limit) - this is
        the ONE deliberate, unhurried moment to decide which end is which,
        instead of that judgment call happening mid-squeeze the way the
        manual capture flow needed it to. Whichever button is answered
        determines closed_tick/open_tick; the other extreme gets the
        opposite label automatically."""
        self.statusBar().showMessage(f"'{name}' is holding at tick {low_tick} (other extreme: {high_tick}).")
        self.session_logger.log_event(
            f"Calibration: auto-calibrate found extremes for '{name}' - low={low_tick} high={high_tick}, awaiting label"
        )
        box = QMessageBox(self)
        box.setWindowTitle("Auto-calibrate: which end is this?")
        box.setText(
            f"'{name}' found its two physical extremes and is now holding at tick {low_tick} "
            f"(the other one is {high_tick}).\n\nLook at it now - is it CLOSED or OPEN?"
        )
        closed_btn = box.addButton("Closed", QMessageBox.YesRole)
        box.addButton("Open", QMessageBox.NoRole)
        box.exec()
        low_is = "closed" if box.clickedButton() is closed_btn else "open"
        self.calibration_worker.request_auto_calibrate_label(name, low_is)
        self.statusBar().showMessage(f"'{name}' labelled: tick {low_tick}={low_is}.")
        self.session_logger.log_event(f"Calibration: '{name}' labelled tick {low_tick}={low_is}")

    def _on_calibration_disconnect(self) -> None:
        if self.calibration_worker:
            worker = self.calibration_worker
            self.calibration_worker = None
            self._retire_worker(worker)
            self.session_logger.log_event("Calibration: disconnecting")
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

    # A sweep records "however far the joint went", which silently includes any
    # travel past the real mechanism - a belt or rack gripper that disengages
    # at its stop keeps turning, and the sweep dutifully records most of a full
    # revolution. That has landed in a saved file four separate times here,
    # each time only noticed later as the gripper mis-tracking during teleop,
    # so it's worth saying at save time instead. No physical parallel gripper
    # or arm joint plausibly needs this much rotation; wrist_roll is exempt
    # because it IS deliberately a full continuous turn.
    IMPLAUSIBLE_RANGE_TICKS = 3400  # ~299 deg; healthy joints here span ~2200-2500
    FULL_TURN_EXEMPT = {"wrist_roll"}

    def _implausible_ranges(self, calibration_dict: dict) -> list[str]:
        flagged = []
        for name, entry in calibration_dict.items():
            if name in self.FULL_TURN_EXEMPT:
                continue
            span = entry["range_max"] - entry["range_min"]
            if span > self.IMPLAUSIBLE_RANGE_TICKS:
                captured = "closed_tick" in entry
                flagged.append(
                    f"  {name}: {entry['range_min']}..{entry['range_max']} "
                    f"({span} ticks = {span * 360.0 / MAX_RES:.0f} deg)"
                    + ("" if captured else "  [swept, no 2-point capture]")
                )
        return flagged

    def _on_calibration_finished(self, calibration_dict: dict) -> None:
        flagged = self._implausible_ranges(calibration_dict)
        if flagged:
            answer = QMessageBox.warning(
                self,
                "Suspiciously wide range",
                "These joints recorded nearly a full revolution, which usually "
                "means the sweep continued past the real mechanism (a belt or "
                "rack gripper that disengages at its stop keeps turning):\n\n"
                + "\n".join(flagged)
                + "\n\nFor a gripper, tick the \"manual 2-point capture\" box and "
                  "capture CLOSED/OPEN instead of sweeping.\n\n"
                  "Save anyway?",
                QMessageBox.Save | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Save:
                return
        self.calibration_panel.prompt_save(calibration_dict)

    # ---------------------------------------------------------------- telemetry
    def _raw_to_deg(self, joint: str, raw: int) -> float:
        """Same conversion ServoBus.read_all_positions_deg() uses internally -
        duplicated here (read-only) so the CSV log can carry the calibrated
        degree value logged from the SAME raw sample/timestamp as position,
        instead of relying on the separately-timed positions_updated stream.
        That's what makes "is raw ticks monotonic but the degree readout
        isn't" actually checkable from one file instead of eyeballing two
        panels at once."""
        cal = self.robot_worker.bus.calibration.get(joint) if self.robot_worker and self.robot_worker.bus else None
        mid = cal.mid if cal else MODEL_RESOLUTION / 2
        return (raw - mid) * 360.0 / MAX_RES

    def _on_telemetry_updated(self, telemetry: dict) -> None:
        self._last_telemetry = telemetry
        self.telemetry_panel.update_telemetry(telemetry)
        self.twin_panel.update_telemetry(self._build_hud_telemetry(telemetry))
        if self._telemetry_csv_writer:
            stamp = time.time()
            profile_key = self.robot_profile.key
            for joint, values in telemetry.items():
                # _raw_to_deg assumes a Feetech raw tick (bus.calibration
                # holds the mid-point to convert around) - DamiaoBus's
                # "position" is already degrees (see its own read_telemetry
                # docstring), so there's no separate raw/degrees pair to log
                # for it, just the one number in both columns.
                position_deg = (
                    values["position"] if profile_key != "so101"
                    else self._raw_to_deg(joint, values["position"])
                )
                row = [f"{stamp:.3f}", joint, values["position"], f"{position_deg:.2f}"]
                for field in TELEMETRY_CSV_FIELDS:
                    raw = values.get(field)
                    if raw is None:
                        row += ["", ""]  # not reported by this profile's bus - see convert_telemetry
                    else:
                        row += [raw, f"{convert_telemetry(field, raw, profile_key)[0]:.3f}"]
                self._telemetry_csv_writer.writerow(row)

    def _build_hud_telemetry(self, telemetry: dict) -> dict[str, dict[str, float]]:
        """Reuses telemetry_panel.convert_telemetry() for load/temperature/
        current so the MuJoCo HUD and the Telemetry tab share one
        unit-conversion table instead of two that can quietly drift apart.
        Position is the one exception - it comes from self.current_positions
        (the same already-calibrated degrees the rest of the app uses, see
        _raw_to_deg/JointPanel) rather than convert_telemetry's raw-tick
        passthrough, since "calibrated degrees" is what a human reads on a
        HUD, not ticks.

        Only includes whichever of load/temperature/current the current
        profile's bus actually reports (TwinPanel.update_telemetry already
        tolerates a value being absent) - reBot B601-DM's DamiaoBus has no
        temperature/current reading at all (confirmed directly this session:
        no RID, no request frame for either), so inventing a 0 for those
        would read as "measured zero", not "not available"."""
        profile_key = self.robot_profile.key
        hud: dict[str, dict[str, float]] = {}
        for joint, values in telemetry.items():
            entry = {"position_deg": self.current_positions.get(joint, 0.0)}
            if "load" in values:
                entry["load"] = convert_telemetry("load", values["load"], profile_key)[0]
            if "temperature" in values:
                entry["temperature"] = convert_telemetry("temperature", values["temperature"], profile_key)[0]
            if "current" in values:
                entry["current_mA"] = convert_telemetry("current", values["current"], profile_key)[0]
            hud[joint] = entry
        return hud

    def _on_telemetry_log_toggled(self, enabled: bool) -> None:
        if enabled:
            os.makedirs("logs", exist_ok=True)
            path = os.path.join("logs", f"telemetry_{time.strftime('%Y-%m-%d_%H-%M-%S')}.csv")
            self._telemetry_csv = open(path, "w", newline="", encoding="utf-8")
            self._telemetry_csv_writer = csv.writer(self._telemetry_csv)
            self._telemetry_csv_writer.writerow(TELEMETRY_CSV_HEADER)
            self.telemetry_panel.set_log_path(f"writing {path}")
            self.session_logger.log_event(f"Telemetry: CSV logging started -> {path}")
        else:
            self._close_telemetry_csv()
            self.telemetry_panel.set_log_path("stopped")
            self.session_logger.log_event("Telemetry: CSV logging stopped")

    def _close_telemetry_csv(self) -> None:
        self._telemetry_csv_writer = None
        if self._telemetry_csv:
            self._telemetry_csv.close()
            self._telemetry_csv = None

    def _on_register_write_requested(self, data_name: str, value: int, joint: str) -> None:
        if self.robot_profile.key != "so101":
            # Torque_Limit/Dead_Zone are Feetech register addresses - DmRobotWorker
            # has no request_register_write at all (Damiao's equivalent concepts,
            # if any, aren't wired up this phase). Refusing here instead of
            # letting it raise AttributeError partway through.
            self.statusBar().showMessage(f"{data_name} isn't supported for {self.robot_profile.label}")
            return
        if not self.robot_worker:
            QMessageBox.warning(self, "Not connected", "Connect the follower before writing servo registers.")
            return
        self.robot_worker.request_register_write(data_name, value, joint or None)
        self.statusBar().showMessage(f"{data_name} = {value} -> {joint or 'all joints'}")
        self.session_logger.log_event(f"Register write: {data_name}={value} on {joint or 'all joints'}")
        if data_name == "Torque_Limit" and joint in JOINT_ORDER:
            # SRAM register: the servo drops it on power-down. Remember it so
            # _apply_persisted_gripper_torque_limit() can put it back, instead
            # of the value quietly reverting the next time the arm is switched
            # off and taking that joint's usable force with it.
            limits = dict(self._load_settings().get("torque_limits", {}))
            limits[joint] = int(value)
            self._save_setting("torque_limits", limits)

    # ---------------------------------------------------------------- teaching (record & playback)
    def _on_record_waypoint(self, force_grip: bool = False) -> None:
        """Snapshot the follower's CURRENT pose, however it got there - hand-
        guided with torque off, driven by the leader, or the manual sliders.
        Doesn't care which; current_positions is already the one place all
        three sources funnel into.

        force_grip=True (the "Record Grip" button) is for a waypoint that has
        to actually hold something. The measured gripper angle here is
        whatever position it happened to settle at against the object - under
        position control that's an equilibrium with ~zero position error, so
        there's no spare closing force behind it once torque is re-applied on
        playback (a bumped part, gravity, anything) is enough to lose the
        grip. Snapping the recorded value to whichever calibrated extreme
        (closed or open) it's already nearest to means playback keeps
        commanding a target the object physically can't let it reach, so the
        servo keeps pushing - that persistent position error IS the grip
        force, up to the servo's torque/current limit."""
        label = f"Waypoint {len(self.waypoints) + 1}"
        positions = dict(self.current_positions)
        if force_grip and "gripper" in positions and "gripper" in self.joint_deg_ranges:
            lo, hi = self.joint_deg_ranges["gripper"]
            measured = positions["gripper"]
            positions["gripper"] = lo if abs(measured - lo) <= abs(measured - hi) else hi
            label += " (grip)"
        self.waypoints.append({"label": label, "positions": positions, "dwell_ms": 0})
        self._refresh_waypoint_list()
        self.session_logger.log_event(f"Teaching: recorded '{label}'")

    def _refresh_waypoint_list(self) -> None:
        self.teaching_panel.set_waypoints([
            f"{wp['label']} - {wp['dwell_ms']} ms delay" if wp.get("dwell_ms") else wp["label"]
            for wp in self.waypoints
        ])

    def _on_set_delay(self, ms: int) -> None:
        index = self.teaching_panel.selected_index()
        if index is None:
            QMessageBox.information(self, "No waypoint selected", "Select a waypoint in the list first.")
            return
        self.waypoints[index]["dwell_ms"] = ms
        self._refresh_waypoint_list()  # preserves the current selection, see TeachingPanel.set_waypoints
        self.session_logger.log_event(f"Teaching: delay on '{self.waypoints[index]['label']}' -> {ms} ms")

    def _on_waypoint_row_selected(self, index: int) -> None:
        if 0 <= index < len(self.waypoints):
            self.teaching_panel.set_delay_spin_value(self.waypoints[index].get("dwell_ms", 0))

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
        # Confirmed real failure mode: playback drives the follower directly
        # via request_goal(), completely independent of control_source - the
        # leader relay is only blocked from ALSO calling request_goal while
        # _playback_index is not None. The instant Stop clears that (index ->
        # None), if Control Source was left on "Leader arm", the relay
        # resumes immediately and snaps the follower to wherever the leader
        # physically is right then - torque-off and hand-held, so often
        # drooped from gravity - which looked exactly like the follower
        # itself losing torque and falling. It never did; it was commanded
        # there. Force Manual before playback starts so Stop can never hand
        # control back to a leader pose nobody was tracking.
        if self.control_source == "leader":
            self.control_source_panel.force_manual()
        self._jog_release_all()
        self.robot_worker.request_torque(True)
        self._playback_index = 0
        self._start_playback_waypoint()
        self.teaching_panel.set_playing(True)
        self.playback_timer.start(PLAYBACK_TICK_MS)
        self.session_logger.log_event("Teaching: playback started")

    def _start_playback_waypoint(self) -> None:
        """Sets up a linear trajectory from wherever the follower is RIGHT
        NOW to this waypoint's target, capped at the Teaching panel's speed
        slider - a raw single Goal_Position jump lets the servo firmware move
        at whatever its own max speed is (jerky, no ramp), so the smoothing
        has to happen here, one small intermediate goal per tick, instead."""
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
        # Read live from the slider rather than a fixed constant, so dragging
        # it mid-sequence changes the NEXT waypoint-to-waypoint move without
        # needing to stop and replay.
        speed = max(1.0, self.teaching_panel.playback_speed_deg_per_s())
        self._playback_move_duration = max(PLAYBACK_MIN_MOVE_S, max_delta / speed)
        self._playback_move_start = time.monotonic()
        self._playback_deadline = (
            self._playback_move_start + self._playback_move_duration
            + PLAYBACK_SETTLE_GRACE_S + PLAYBACK_MAX_DWELL_S
        )
        self._playback_dwell_s = wp.get("dwell_ms", 0) / 1000.0
        self._playback_hold_until = None  # reset - this is a NEW move, not yet arrived
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
        if not self.robot_worker:
            # Disconnecting (or a bus error retiring the worker) mid-sequence
            # used to be an AttributeError on the next tick, 33ms later. There
            # is nothing left to drive, so end the sequence deliberately.
            self._on_stop_sequence()
            self.teaching_panel.set_status("Playback stopped - follower disconnected.")
            self.session_logger.log_event("Teaching: playback aborted (follower disconnected)")
            return

        now = time.monotonic()
        t = max(0.0, min(1.0, (now - self._playback_move_start) / self._playback_move_duration))
        # Quintic minimum-jerk easing (6t^5 - 15t^4 + 10t^3): zero velocity AND
        # zero acceleration at both endpoints, unlike a raw linear ramp - that's
        # what removes the jolt at the start/stop of each waypoint-to-waypoint
        # move. Still a full stop at every waypoint (that's the next increment,
        # a single continuous spline across the whole sequence, if it's wanted).
        eased_t = t * t * t * (t * (t * 6 - 15) + 10)
        for name, target in self._playback_target_positions.items():
            start = self._playback_start_positions.get(name, target)
            self.robot_worker.request_goal(name, start + eased_t * (target - start))

        arrived = t >= 1.0 and all(
            abs(self.current_positions.get(name, 0.0) - degrees) <= PLAYBACK_ARRIVE_TOLERANCE_DEG
            for name, degrees in self._playback_target_positions.items()
        )
        if not (arrived or now >= self._playback_deadline):
            return

        # Arrived (or gave up waiting) - hold here for this waypoint's own
        # dwell before moving on. Started lazily on the tick arrival is FIRST
        # detected, not from move-start, so the dwell is purely time spent
        # sitting at the target, not counted against the move itself.
        if self._playback_dwell_s > 0:
            if self._playback_hold_until is None:
                self._playback_hold_until = now + self._playback_dwell_s
                wp = self.waypoints[self._playback_index]
                self.teaching_panel.set_status(
                    f"Holding at '{wp['label']}' for {wp.get('dwell_ms', 0)} ms..."
                )
            if now < self._playback_hold_until:
                return
        self._playback_hold_until = None

        self._playback_index += 1
        if self._playback_index >= len(self.waypoints):
            # Checked live (not captured at Play time) so unticking Loop
            # mid-sequence lets the cycle already under way finish and
            # stop cleanly, rather than needing Stop to cut it off - see
            # TeachingPanel.loop_enabled().
            if self.teaching_panel.loop_enabled():
                self.session_logger.log_event("Teaching: loop -> restarting from waypoint 1")
                self._playback_index = 0
                self._start_playback_waypoint()
                return
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
        try:
            with open(path, encoding="utf-8") as f:
                loaded = _validate_waypoints(json.load(f))
        except (OSError, ValueError) as exc:
            # Anything malformed used to load fine and then blow up much later,
            # mid-playback, with the arm already moving - by far the worst
            # possible moment to discover the file was wrong.
            QMessageBox.warning(self, "Could not load waypoints", f"{path}\n\n{exc}")
            self.session_logger.log_event(f"ERROR: waypoint load failed ({path}): {exc}")
            return
        self.waypoints = loaded
        self._refresh_waypoint_list()
        self.session_logger.log_event(f"Teaching: loaded {len(self.waypoints)} waypoint(s) from {path}")

    # ---------------------------------------------------------------- lifecycle
    def closeEvent(self, event) -> None:
        self.playback_timer.stop()
        self.ui_refresh_timer.stop()
        self.keyboard_timer.stop()
        self.gamepad_timer.stop()
        self.jog_timer.stop()
        self._close_telemetry_csv()

        # These all go through _retire_worker, which deliberately does NOT
        # block - correct while the app is running, but on the way out there
        # is no event loop left to deliver the `finished` signals that would
        # otherwise release them, so the process would exit with live QThreads
        # still touching serial ports (a Qt-level abort, not a clean exit).
        # Shutdown is the one moment blocking the GUI thread is the right call.
        self._on_disconnect()
        self._on_leader_disconnect()
        self._on_calibration_disconnect()
        self._on_setup_disconnect()
        self._on_dm_setup_disconnect()
        self._on_camera_stop()
        if self.gamepad_worker:
            self.gamepad_worker.stop()
        if self.twin_worker:
            self.twin_worker.stop()

        for worker in list(self._shutting_down_workers) + [self.gamepad_worker, self.twin_worker]:
            if worker is not None:
                worker.wait(3000)  # a blocked SDK read can sit for ~1s before timing out
        super().closeEvent(event)
