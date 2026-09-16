"""
Background worker that runs the exact same calibration sequence as
`lerobot-calibrate` (reset -> half-turn homing -> record range of motion ->
force wrist_roll full-turn -> write limits), driven step-by-step from the GUI
instead of blocking `input()` prompts in a terminal.
"""
from __future__ import annotations

import queue
import time

from PySide6.QtCore import QThread, Signal

from .servo_bus import MAX_RES, ServoBus, ServoBusError

POLL_INTERVAL_S = 0.1  # 10 Hz is plenty for a human moving a joint by hand
FULL_TURN_JOINTS = ("wrist_roll",)  # matches lerobot's so_follower/so_leader.calibrate()


class CalibrationWorker(QThread):
    connected = Signal(bool)
    error = Signal(str)
    live_update = Signal(dict, dict, dict)      # positions, mins, maxes (raw ticks)
    middle_set = Signal(dict)                     # {joint: homing_offset}
    finished_calibration = Signal(dict)           # {joint: {id, drive_mode, homing_offset, range_min, range_max}}
    point_captured = Signal(str, str, int)        # joint, label, raw_tick
    auto_calibrate_awaiting_label = Signal(str, int, int)  # joint, low_tick, high_tick

    def __init__(self, port: str, joint_names: list[str], parent=None):
        super().__init__(parent)
        self.port = port
        self.joint_names = joint_names
        self._commands: queue.Queue = queue.Queue()
        self._running = False
        self._recording = False
        self.mins: dict[str, int] = {}
        self.maxes: dict[str, int] = {}
        self.homing_offsets: dict[str, int] = {}
        # Joints in here skip the swept min/max tracking entirely (start/stop
        # recording never touches their mins/maxes) - for a mechanism whose
        # sweep runs into a freewheel/disengage zone past its real travel
        # (confirmed on the PinionRack gripper: the wizard's own sweep keeps
        # recording a near-full-360 range that doesn't match the real,
        # hand-verified open/closed ticks), two deliberate capture_point()
        # calls at the real physical extremes are the reliable substitute -
        # same idea as reBot's B601-DM, which never sweeps a range at all and
        # just records single reference poses. Opt-in and per-joint so it
        # can't change behaviour for any joint that isn't explicitly added.
        self.manual_joints: set[str] = set()
        self.manual_points: dict[str, dict[str, int]] = {}
        # Set by "auto_calibrate" once the stall search finds both extremes,
        # consumed by "auto_calibrate_label" once the operator says which one
        # is which - see ServoBus.auto_find_range_raw's docstring for why
        # that decision is deliberately a separate step from the search.
        self._auto_cal_pending: dict[str, tuple[int, int]] = {}
        self._auto_cal_original_torque: dict[str, int] = {}
        self.bus: ServoBus | None = None

    # -- thread-safe public API ----------------------------------------------
    def request_reset(self) -> None:
        self._commands.put(("reset",))

    def request_set_middle(self) -> None:
        self._commands.put(("set_middle",))

    def request_start_recording(self) -> None:
        self._commands.put(("start_recording",))

    def request_stop_recording(self) -> None:
        self._commands.put(("stop_recording",))

    def request_finish(self) -> None:
        self._commands.put(("finish",))

    def request_set_manual_joint(self, name: str, enabled: bool) -> None:
        self._commands.put(("set_manual_joint", name, enabled))

    def request_capture_point(self, name: str, label: str) -> None:
        self._commands.put(("capture_point", name, label))

    def request_auto_calibrate(self, name: str) -> None:
        self._commands.put(("auto_calibrate", name))

    def request_auto_calibrate_label(self, name: str, low_is: str) -> None:
        self._commands.put(("auto_calibrate_label", name, low_is))

    def stop(self) -> None:
        self._running = False

    # -- worker thread body ---------------------------------------------------
    def run(self) -> None:
        self.bus = ServoBus(self.port)
        try:
            self.bus.connect()
        except ServoBusError as exc:
            self.error.emit(str(exc))
            self.connected.emit(False)
            return

        self.connected.emit(True)
        self._running = True

        while self._running:
            while True:
                try:
                    cmd = self._commands.get_nowait()
                except queue.Empty:
                    break
                self._handle(cmd)

            if self._recording:
                positions = {}
                for name in self.joint_names:
                    try:
                        pos = self.bus.read_position_raw(name)
                    except ServoBusError as exc:
                        self.error.emit(str(exc))
                        continue
                    positions[name] = pos
                    # Sweep tracking runs for EVERY joint, including the ones
                    # in manual_joints: finish() prefers their captured points
                    # when both exist, and needs the swept pair as a fallback
                    # when they don't. Skipping it here used to leave a joint
                    # whose captures were incomplete with mins == maxes == the
                    # seed - a zero-width range, which is worse than the
                    # over-wide sweep the capture was meant to replace.
                    self.mins[name] = min(self.mins.get(name, pos), pos)
                    self.maxes[name] = max(self.maxes.get(name, pos), pos)
                self.live_update.emit(positions, dict(self.mins), dict(self.maxes))

            time.sleep(POLL_INTERVAL_S)

        self.bus.port_handler.closePort()
        self.connected.emit(False)

    def _handle(self, cmd: tuple) -> None:
        command = cmd[0]
        try:
            if command == "reset":
                # Also cancels an in-progress recording pass: reset is the
                # "start over" escape hatch, and leaving _recording set would
                # have the loop keep widening the min/max it just cleared,
                # seeded from wherever the arm happens to be sitting.
                self._recording = False
                for name in self.joint_names:
                    self.bus.prepare_for_calibration(name)
                self.mins = {}
                self.maxes = {}
                self.homing_offsets = {}
                self.manual_points = {}
            elif command == "set_middle":
                self.homing_offsets = {name: self.bus.set_half_turn_homing(name) for name in self.joint_names}
                self.middle_set.emit(dict(self.homing_offsets))
            elif command == "start_recording":
                seeds = {name: self.bus.read_position_raw(name) for name in self.joint_names}
                self.mins, self.maxes = dict(seeds), dict(seeds)
                self._recording = True
            elif command == "stop_recording":
                self._recording = False
            elif command == "set_manual_joint":
                _, name, enabled = cmd
                if enabled:
                    self.manual_joints.add(name)
                else:
                    self.manual_joints.discard(name)
                    self.manual_points.pop(name, None)
            elif command == "capture_point":
                _, name, label = cmd
                raw = self.bus.read_position_raw(name)
                self.manual_points.setdefault(name, {})[label] = raw
                self.point_captured.emit(name, label, raw)
            elif command == "auto_calibrate":
                _, name = cmd
                self._auto_cal_original_torque[name] = self.bus.read_raw(
                    "Torque_Limit", self.bus.joint_id(name)
                )
                low, high = self.bus.auto_find_range_raw(name)
                self._auto_cal_pending[name] = (low, high)
                self.auto_calibrate_awaiting_label.emit(name, low, high)
            elif command == "auto_calibrate_label":
                _, name, low_is = cmd
                pending = self._auto_cal_pending.pop(name, None)
                if pending is None:
                    self.error.emit(f"No auto-calibration in progress for '{name}'")
                else:
                    low, high = pending
                    high_is = "open" if low_is == "closed" else "closed"
                    self.manual_points.setdefault(name, {})[low_is] = low
                    self.manual_points[name][high_is] = high
                    self.manual_joints.add(name)
                    # Anchor the goal to wherever it's actually sitting RIGHT
                    # NOW before restoring full torque. _find_extreme() should
                    # already leave Goal_Position matching this, but this is
                    # the difference between a servo grinding at full torque
                    # against an unreachable target and one holding still -
                    # cheap enough (one read, one write) to re-assert instead
                    # of trusting that invariant blindly.
                    mid = self.bus.joint_id(name)
                    current_pos = self.bus.read_raw("Present_Position", mid)
                    self.bus.write_raw("Goal_Position", mid, current_pos)
                    # Restore whatever Torque_Limit was set to before the
                    # search - left at AUTO_CAL_TORQUE_LIMIT otherwise, which
                    # is exactly the "gripper feels weak" problem this
                    # session already spent a long time chasing down once.
                    original = self._auto_cal_original_torque.pop(name, None)
                    if original is not None:
                        self.bus.write_raw("Torque_Limit", mid, original)
                    self.point_captured.emit(name, low_is, low)
                    self.point_captured.emit(name, high_is, high)
            elif command == "finish":
                # The GUI gates the step order, but finishing without a
                # recording pass would KeyError deep inside the loop below
                # after having already written limits to some of the servos -
                # a half-configured arm. Refuse up front instead. A joint
                # covered only by a manual capture (never swept) is fine even
                # though it has no entry in self.mins - the branch below falls
                # back to self.mins/self.maxes only when a captured pair isn't
                # available for that joint.
                missing = [
                    name for name in self.joint_names
                    if name not in self.mins
                    and not ({"closed", "open"} <= self.manual_points.get(name, {}).keys())
                ]
                if missing:
                    self.error.emit(
                        "Cannot finish: no range recorded for "
                        f"{', '.join(missing)}. Run steps 1-4 first (or capture both points)."
                    )
                    return
                for name in FULL_TURN_JOINTS:
                    if name in self.joint_names:
                        self.mins[name] = 0
                        self.maxes[name] = MAX_RES
                result = {}
                for name in self.joint_names:
                    points = self.manual_points.get(name) if name in self.manual_joints else None
                    captured = points if points and {"closed", "open"} <= points.keys() else None
                    if captured:
                        lo, hi = min(captured.values()), max(captured.values())
                    else:
                        lo, hi = self.mins[name], self.maxes[name]
                    self.bus.write_position_limits_raw(name, lo, hi)
                    entry = {
                        "id": self.bus.joint_id(name),
                        "drive_mode": 0,
                        "homing_offset": self.homing_offsets.get(name, 0),
                        "range_min": lo,
                        "range_max": hi,
                    }
                    if captured:
                        # Which tick is the CLOSED end is the one thing a swept
                        # range can never say, and it flips between runs
                        # depending on the homing pose - so record it while the
                        # operator has just told us outright, instead of
                        # leaving teleop to guess the direction later.
                        entry["closed_tick"] = captured["closed"]
                        entry["open_tick"] = captured["open"]
                    result[name] = entry
                self.finished_calibration.emit(result)
        except ServoBusError as exc:
            self.error.emit(str(exc))
