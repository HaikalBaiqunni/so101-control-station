"""
Runs the MuJoCo digital twin's render loop on its own QThread, decoupled from
both the control loop (RobotWorker) and the main GUI thread.

Measured on this machine: DigitalTwin.render() alone costs 40-90ms per call -
that's fine for a "nice to look at" visualization, but is *catastrophic* if it
runs on the main thread, since it blocks Qt's event loop and delays delivery
of the 60Hz position_updated signals from the robot workers, making the whole
UI (sliders included) feel laggy even though the actual servo control loop is
running at full speed in its own thread the entire time.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, Signal

from .digital_twin import DigitalTwin, JOINT_NAMES

RENDER_INTERVAL_S = 1 / 15  # visualization only - 15fps is plenty and leaves headroom


class TwinWorker(QThread):
    frame_ready = Signal(object)  # np.ndarray, RGB uint8 HxWx3
    load_failed = Signal(str)
    neutral_pose_ready = Signal(object)  # one-off snapshot at all-joints-zero, see request_neutral_snapshot

    def __init__(self, mjcf_path: str, parent=None):
        super().__init__(parent)
        self.mjcf_path = mjcf_path
        self._fractions: dict[str, float] = {}
        self._neutral_requested = False
        self._lock = threading.Lock()
        self._running = False

    def set_fractions(self, fractions: dict[str, float]) -> None:
        """`fractions` values are 0.0-1.0 - see DigitalTwin.set_joint_fraction
        for why this, and not raw degrees, is what the twin actually wants."""
        with self._lock:
            self._fractions = dict(fractions)

    def request_neutral_snapshot(self) -> None:
        """Ask for one render of this MJCF's own designed zero pose (every
        joint at 0 degrees) - used as a visual "aim for this" reference when
        the user is about to do the calibration half-turn-homing step by
        hand. Delivered once via neutral_pose_ready, then the loop resumes
        its normal live rendering - doesn't disturb the ongoing display."""
        with self._lock:
            self._neutral_requested = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        try:
            twin = DigitalTwin(self.mjcf_path)
        except Exception as exc:  # mujoco raises plain Exception/ValueError on bad XML
            self.load_failed.emit(str(exc))
            return

        self._running = True
        while self._running:
            t0 = time.perf_counter()
            with self._lock:
                fractions = dict(self._fractions)
                neutral_requested = self._neutral_requested
                self._neutral_requested = False

            if neutral_requested:
                twin.set_all_deg(dict.fromkeys(JOINT_NAMES, 0.0))
                self.neutral_pose_ready.emit(twin.render())
                continue  # skip this cycle's regular frame - next loop iteration resumes it

            twin.set_all_fractions(fractions)
            frame = twin.render()
            self.frame_ready.emit(frame)
            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, RENDER_INTERVAL_S - elapsed))
