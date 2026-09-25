"""
Background QThread driving the reBot B601-DM's real Damiao motors via
core/damiao_bus.py - the DM equivalent of core/workers.py's RobotWorker
(Feetech), with the SAME public signal/method surface
(positions_updated/telemetry_updated/error/connection_changed,
request_goal/request_torque/stop) so ui/main_window.py's existing
jog/telemetry code can hold either worker type in self.robot_worker without
needing to know which - that's what lets Joint AND Cartesian (World/Tool)
jogging both work against this robot for free: every jog path already
funnels through _drive_joint_programmatically -> robot_worker.request_goal,
never anything Feetech-specific.

Uses the _stop_requested flag pattern (core/dm_setup_worker.py's fix),
NOT the self._running = True unconditional-after-connect pattern RobotWorker
itself still had until this same session's Phase 2 pass fixed it too - see
that file's own comment for the exact race this avoids.
"""
from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QThread, Signal

from .damiao_bus import DamiaoBus, DamiaoBusError
from .dm_can import Control_Type

POLL_INTERVAL_S = 1 / 20  # see module docstring below for why this isn't 1/60 like RobotWorker
TELEMETRY_EVERY_N_CYCLES = 4  # -> 5 Hz, plenty for a diagnostics graph

# Damiao's protocol has no batched multi-motor read the way Feetech's
# GroupSyncRead gives ServoBus - core/dm_can.py's refresh_motor_status() is
# one request+reply round trip PER motor (confirmed directly this session).
# Seven of those every cycle is meaningfully slower than Feetech's single
# batched transaction, so this starts at a more conservative poll rate for
# the first real-hardware pass rather than assuming 60Hz is safe to copy
# over unchanged - worth re-measuring against the real bus and raising once
# actual round-trip timing on this hardware is known.


class DmRobotWorker(QThread):
    positions_updated = Signal(dict)   # {joint_name: degrees}
    telemetry_updated = Signal(dict)   # {joint_name: {position, velocity, load}}
    error = Signal(str)
    connection_changed = Signal(bool)

    def __init__(
        self,
        port: str,
        id_master_by_joint: dict[str, tuple[int, int]],
        ranges_deg: dict[str, tuple[float, float]],
        control_mode: Control_Type = Control_Type.POS_VEL,
        mit_gains: dict[str, tuple[float, float]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.port = port
        self.id_master_by_joint = id_master_by_joint
        self.ranges_deg = ranges_deg
        self.control_mode = control_mode
        self.mit_gains = mit_gains
        self._pending_goals: dict[str, float] = {}
        self.last_goals: dict[str, float] = {}
        self._goals_lock = threading.Lock()
        self._torque_commands: queue.Queue = queue.Queue()
        self._stop_requested = False
        self.bus: DamiaoBus | None = None

    # -- thread-safe public API (call from GUI thread) ------------------------
    def request_goal(self, name: str, degrees: float) -> None:
        with self._goals_lock:
            self._pending_goals[name] = degrees
            self.last_goals[name] = degrees

    def request_torque(self, enabled: bool, name: str | None = None) -> None:
        self._torque_commands.put((enabled, name))

    def stop(self) -> None:
        self._stop_requested = True

    # -- worker thread body ----------------------------------------------------
    def run(self) -> None:
        self.bus = DamiaoBus(
            self.port,
            self.id_master_by_joint,
            self.ranges_deg,
            control_mode=self.control_mode,
            mit_gains=self.mit_gains,
        )
        try:
            self.bus.connect()
        except DamiaoBusError as exc:
            self.error.emit(str(exc))
            self.connection_changed.emit(False)
            return

        self.connection_changed.emit(True)
        cycle = 0

        while not self._stop_requested:
            cycle += 1
            with self._goals_lock:
                goals, self._pending_goals = self._pending_goals, {}
            if goals:
                try:
                    self.bus.write_goals_deg(goals)
                except DamiaoBusError as exc:
                    self.error.emit(str(exc))

            while True:
                try:
                    enabled, name = self._torque_commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    (self.bus.enable_torque if enabled else self.bus.disable_torque)(name)
                except DamiaoBusError as exc:
                    self.error.emit(str(exc))

            try:
                positions = self.bus.read_all_positions_deg()
                self.positions_updated.emit(positions)
            except DamiaoBusError as exc:
                self.error.emit(str(exc))

            if cycle % TELEMETRY_EVERY_N_CYCLES == 0:
                try:
                    self.telemetry_updated.emit(self.bus.read_telemetry())
                except DamiaoBusError as exc:
                    self.error.emit(str(exc))

            time.sleep(POLL_INTERVAL_S)

        self.bus.disconnect()
        self.connection_changed.emit(False)
