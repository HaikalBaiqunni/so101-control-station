"""Background QThread reading Seeed's Star Arm 102 leader via
core/fashionstar_bus.py - the FashionStar-UART equivalent of
core/dm_robot_worker.py's DmRobotWorker, with the SAME public signal/method
surface ui/main_window.py's leader-handling code already expects
(positions_updated/error/connection_changed, request_torque/stop) so it can
hold either worker type in self.leader_worker without needing to know which.

Uses the _stop_requested flag pattern (see core/dm_robot_worker.py's own
comment for the exact stop()-during-connect() race this avoids).
"""
from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from .fashionstar_bus import FashionStarBus, FashionStarBusError

POLL_INTERVAL_S = 1 / 30  # one batched sync_monitor() call per cycle - see FashionStarBus

# Leader gripper hold (experimental feedback test). Hard limits, not settings.
HOLD_MAX_S = 5.0          # never holds longer than this
HOLD_PERIOD_S = 0.2       # a strength below 100% pulses: held for duty x period, free for the rest
HOLD_STILL_DEG = 2.0      # the handle must be still (two readings this close) before a hold starts
HOLD_ESCAPE_DEG = 8.0     # pushed this far from the held angle: let go


class FashionStarLeaderWorker(QThread):
    positions_updated = Signal(dict)          # {follower_joint_name: degrees}
    native_positions_updated = Signal(dict)   # {follower_joint_name: unclamped native degrees}
    error = Signal(str)
    connection_changed = Signal(bool)
    hold_changed = Signal(bool, str)          # (holding, message) for the gripper hold test

    def __init__(
        self,
        port: str,
        ranges_deg: dict[str, tuple[float, float]] | None = None,
        native_ranges_deg: dict[str, tuple[float, float]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.port = port
        self.ranges_deg = ranges_deg
        self.native_ranges_deg = native_ranges_deg
        self._stop_requested = False
        self.bus: FashionStarBus | None = None
        self._hold_request: tuple[float, float] | None = None
        self._release_requested = False
        self._hold: dict | None = None

    # -- thread-safe public API (call from GUI thread) ------------------------
    def request_torque(self, enabled: bool, name: str | None = None) -> None:
        # Deliberate no-op, not "unimplemented": this leader is always
        # free-spinning by hand (matches RebotArm102Leader.send_feedback()
        # raising NotImplementedError upstream), but
        # MainWindow._on_leader_connection_changed calls this unconditionally
        # once after every leader connect regardless of bus type, so it must
        # exist and safely do nothing rather than raise.
        pass

    def stop(self) -> None:
        self._stop_requested = True

    def request_hold(self, seconds: float, duty: float) -> None:
        """Hold the leader's gripper handle where it is for `seconds` (at most HOLD_MAX_S) at `duty` (0.1..1,
        the share of each 0.2 s the servo is actually locked). It lets go by itself and on request_release."""
        self._hold_request = (min(max(seconds, 0.5), HOLD_MAX_S), min(max(duty, 0.1), 1.0))

    def request_release(self) -> None:
        self._hold_request = None
        self._release_requested = True

    def _end_hold(self, message: str) -> None:
        was_holding = self._hold is not None
        self._hold = None
        try:
            if self.bus is not None:
                self.bus.release_gripper()
        except Exception as exc:   # noqa: BLE001 - a failed release must be loud, never silent
            self.error.emit(f"leader gripper release failed: {exc}")
        if was_holding or message:
            self.hold_changed.emit(False, message)

    def _begin_hold(self, seconds: float, duty: float, now: float) -> None:
        first = self.bus.read_gripper_angle()
        time.sleep(0.05)
        second = self.bus.read_gripper_angle()
        if abs(first - second) > HOLD_STILL_DEG:
            self.hold_changed.emit(False, "not started: the handle was moving - keep it still and try again")
            return
        self._hold = {"angle": second, "end": now + seconds, "start": now, "duty": duty, "on": False}
        self.hold_changed.emit(True, f"holding the leader gripper at {second:.1f} deg")

    def _service_hold(self, now: float) -> None:
        """Called once per poll cycle on the worker thread, which owns the serial port."""
        try:
            if self._release_requested:
                self._release_requested = False
                if self._hold is not None:
                    self._end_hold("released")
            request, self._hold_request = self._hold_request, None
            if request is not None and self._hold is None:
                self._begin_hold(*request, now)
            hold = self._hold
            if hold is None:
                return
            if now >= hold["end"]:
                self._end_hold("done")
                return
            if abs(self.bus.read_gripper_angle() - hold["angle"]) > HOLD_ESCAPE_DEG:
                self._end_hold(f"released: the handle was pushed more than {HOLD_ESCAPE_DEG:g} deg away")
                return
            want_on = hold["duty"] >= 1.0 or ((now - hold["start"]) % HOLD_PERIOD_S) < hold["duty"] * HOLD_PERIOD_S
            if want_on and not hold["on"]:
                self.bus.hold_gripper(hold["angle"])
                hold["on"] = True
            elif not want_on and hold["on"]:
                self.bus.release_gripper()
                hold["on"] = False
        except Exception as exc:   # noqa: BLE001 - whatever goes wrong, the handle must end up free
            self.error.emit(f"leader gripper hold: {exc}")
            self._end_hold("released after an error")

    # -- worker thread body ----------------------------------------------------
    def run(self) -> None:
        self.bus = FashionStarBus(self.port, self.ranges_deg, self.native_ranges_deg)
        try:
            self.bus.connect()
        except FashionStarBusError as exc:
            self.error.emit(str(exc))
            self.connection_changed.emit(False)
            return

        self.connection_changed.emit(True)

        while not self._stop_requested:
            self._service_hold(time.monotonic())
            try:
                self.positions_updated.emit(self.bus.read_all_positions_deg())
                self.native_positions_updated.emit(self.bus.read_all_native_positions_deg())
            except FashionStarBusError as exc:
                self.error.emit(str(exc))
            time.sleep(POLL_INTERVAL_S)

        if self._hold is not None:
            self._end_hold("released: leader disconnected")
        self.bus.disconnect()
        self.connection_changed.emit(False)
