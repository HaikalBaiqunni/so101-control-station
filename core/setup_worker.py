"""
Background worker for FIRST-TIME motor setup - the step that has to happen
before calibration, and therefore before anything else in this app works.

Every STS3215 leaves the factory answering to ID 1. Six of them wired onto one
bus are electrically fine but logically indistinguishable: a read addressed to
id 1 gets six simultaneous replies that collide into garbage. So the very
first thing anyone with a new SO-101 has to do is plug in ONE servo at a time
and give it its own address, 1 through 6, in joint order.

That is what `lerobot-setup-motors` does from a terminal. Doing it here means a
beginner never has to leave the GUI (or install the full lerobot + torch stack)
just to get to the point where the arm can be calibrated at all.

Runs on its own QThread for the same reason every other bus access does: a
full baudrate sweep is several seconds of blocking serial I/O, and that has no
business happening on the Qt event loop.
"""
from __future__ import annotations

import queue
import time

from PySide6.QtCore import QThread, Signal

from .servo_bus import (
    BAUDRATE_TABLE,
    DEFAULT_SCAN_MAX_ID,
    MAX_SERVO_ID,
    ServoBus,
    ServoBusError,
)

IDLE_SLEEP_S = 0.05


class SetupWorker(QThread):
    """Owns one ServoBus for the lifetime of the Setup tab's connection.

    Deliberately does NOT load a calibration file (there isn't one yet - that
    is the whole point of this stage) and never writes Goal_Position, so it
    cannot move the arm. The only registers it touches are ID, Baud_Rate and
    the Lock/Torque_Enable pair that bracket them.
    """

    connected = Signal(bool)
    error = Signal(str)
    progress = Signal(str)                 # human-readable status line
    scan_finished = Signal(dict)           # {baudrate: [ids]}
    id_assigned = Signal(int, int)         # (old_id, new_id)

    def __init__(self, port: str, parent=None):
        super().__init__(parent)
        self.port = port
        self._commands: queue.Queue = queue.Queue()
        self._running = False
        self.bus: ServoBus | None = None

    # -- thread-safe public API (call from the GUI thread) ------------------
    def request_scan(self, all_baudrates: bool = True) -> None:
        self._commands.put(("scan", all_baudrates))

    def request_deep_scan(self) -> None:
        """Every address 0..253, at the CURRENT baudrate only - the escape
        hatch for a servo somebody previously set to an id outside the normal
        range. Slow enough (~5s) that it stays opt-in rather than the default."""
        self._commands.put(("deep_scan", None))

    def request_assign_id(self, current_id: int, new_id: int) -> None:
        self._commands.put(("assign_id", (current_id, new_id)))

    def request_set_baudrate(self, motor_id: int, baudrate: int) -> None:
        self._commands.put(("set_baudrate", (motor_id, baudrate)))

    def stop(self) -> None:
        self._running = False

    # -- worker thread body --------------------------------------------------
    def run(self) -> None:
        self.bus = ServoBus(self.port)
        try:
            self.bus.connect()
        except (ServoBusError, OSError) as exc:
            self.error.emit(str(exc))
            self.connected.emit(False)
            return

        self.connected.emit(True)
        self._running = True

        while self._running:
            try:
                command, payload = self._commands.get(timeout=IDLE_SLEEP_S)
            except queue.Empty:
                continue
            try:
                self._handle(command, payload)
            except ServoBusError as exc:
                self.error.emit(str(exc))

        self.bus.disconnect()
        self.connected.emit(False)

    def _handle(self, command: str, payload) -> None:
        if command == "scan":
            rates = list(BAUDRATE_TABLE.values()) if payload else [self.bus.baudrate]
            self.progress.emit(
                f"scanning ids 0-{DEFAULT_SCAN_MAX_ID} at "
                + (f"{len(rates)} baudrates..." if len(rates) > 1 else f"{rates[0]} baud...")
            )
            found = self.bus.scan_bus(baudrates=rates)
            self._report_scan(found)

        elif command == "deep_scan":
            self.progress.emit(f"deep scan: ids 0-{MAX_SERVO_ID} at {self.bus.baudrate} baud (slow)...")
            ids = self.bus.scan_ids(max_id=MAX_SERVO_ID)
            self._report_scan({self.bus.baudrate: ids} if ids else {})

        elif command == "assign_id":
            current_id, new_id = payload
            self.progress.emit(f"assigning id {current_id} -> {new_id}...")
            self.bus.assign_id(current_id, new_id)
            self.id_assigned.emit(current_id, new_id)
            self.progress.emit(f"servo {current_id} is now id {new_id}")
            # Re-scan so the panel's found-list reflects reality rather than
            # what the user hopes just happened.
            time.sleep(0.05)
            self._report_scan({self.bus.baudrate: self.bus.scan_ids()})

        elif command == "set_baudrate":
            motor_id, baudrate = payload
            self.progress.emit(f"moving servo {motor_id} to {baudrate} baud...")
            self.bus.set_servo_baudrate(motor_id, baudrate)
            self._report_scan({self.bus.baudrate: self.bus.scan_ids()})

    def _report_scan(self, found: dict[int, list[int]]) -> None:
        total = sum(len(ids) for ids in found.values())
        self.scan_finished.emit(found)
        self.progress.emit(
            "no servos answered - check power and the USB cable" if not total
            else f"found {total} servo(s)"
        )
