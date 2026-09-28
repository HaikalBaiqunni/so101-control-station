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


class FashionStarLeaderWorker(QThread):
    positions_updated = Signal(dict)          # {follower_joint_name: degrees}
    native_positions_updated = Signal(dict)   # {follower_joint_name: unclamped native degrees}
    error = Signal(str)
    connection_changed = Signal(bool)

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
            try:
                self.positions_updated.emit(self.bus.read_all_positions_deg())
                self.native_positions_updated.emit(self.bus.read_all_native_positions_deg())
            except FashionStarBusError as exc:
                self.error.emit(str(exc))
            time.sleep(POLL_INTERVAL_S)

        self.bus.disconnect()
        self.connection_changed.emit(False)
