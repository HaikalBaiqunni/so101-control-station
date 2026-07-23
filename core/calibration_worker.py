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
                self._handle(cmd[0])

            if self._recording:
                positions = {}
                for name in self.joint_names:
                    try:
                        pos = self.bus.read_position_raw(name)
                    except ServoBusError as exc:
                        self.error.emit(str(exc))
                        continue
                    positions[name] = pos
                    self.mins[name] = min(self.mins.get(name, pos), pos)
                    self.maxes[name] = max(self.maxes.get(name, pos), pos)
                self.live_update.emit(positions, dict(self.mins), dict(self.maxes))

            time.sleep(POLL_INTERVAL_S)

        self.bus.port_handler.closePort()
        self.connected.emit(False)

    def _handle(self, command: str) -> None:
        try:
            if command == "reset":
                for name in self.joint_names:
                    self.bus.prepare_for_calibration(name)
                self.mins = {}
                self.maxes = {}
                self.homing_offsets = {}
            elif command == "set_middle":
                self.homing_offsets = {name: self.bus.set_half_turn_homing(name) for name in self.joint_names}
                self.middle_set.emit(dict(self.homing_offsets))
            elif command == "start_recording":
                seeds = {name: self.bus.read_position_raw(name) for name in self.joint_names}
                self.mins, self.maxes = dict(seeds), dict(seeds)
                self._recording = True
            elif command == "stop_recording":
                self._recording = False
            elif command == "finish":
                for name in FULL_TURN_JOINTS:
                    if name in self.joint_names:
                        self.mins[name] = 0
                        self.maxes[name] = MAX_RES
                result = {}
                for name in self.joint_names:
                    self.bus.write_position_limits_raw(name, self.mins[name], self.maxes[name])
                    result[name] = {
                        "id": self.bus.joint_id(name),
                        "drive_mode": 0,
                        "homing_offset": self.homing_offsets.get(name, 0),
                        "range_min": self.mins[name],
                        "range_max": self.maxes[name],
                    }
                self.finished_calibration.emit(result)
        except ServoBusError as exc:
            self.error.emit(str(exc))
