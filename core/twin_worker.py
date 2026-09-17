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

from .digital_twin import JOINT_NAMES, DigitalTwin

RENDER_INTERVAL_S = 1 / 15  # visualization only - 15fps is plenty and leaves headroom


class TwinWorker(QThread):
    frame_ready = Signal(object)  # np.ndarray, RGB uint8 HxWx3
    load_failed = Signal(str)
    neutral_pose_ready = Signal(object)  # one-off snapshot at all-joints-zero, see request_neutral_snapshot

    def __init__(self, mjcf_path: str, joint_names: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.mjcf_path = mjcf_path
        # Passed straight through to DigitalTwin - None keeps its own
        # SO-101 default, so every pre-existing caller is unaffected. See
        # core/robot_profiles.py for where a non-default list comes from.
        self.joint_names = list(joint_names) if joint_names is not None else list(JOINT_NAMES)
        self._fractions: dict[str, float] = {}
        self._neutral_requested = False
        # Queued (not applied directly here) because DigitalTwin.orbit/pan/
        # zoom mutate mujoco.MjvCamera fields, which aren't thread-safe to
        # touch from the GUI thread while render() (this worker's own thread)
        # might be mid-read of the same camera. A list of small deltas - one
        # per mouse-move event the GUI thread saw - rather than a single
        # "latest camera state" is what lets a fast drag still feel
        # continuous even though this worker only drains the queue once per
        # ~67ms (15fps) render tick: nothing is dropped/coalesced away.
        self._camera_ops: list[tuple[str, float, float]] = []
        self._reset_camera_requested = False
        self._lock = threading.Lock()
        # A plain flag that stop() sets and run()'s loop condition checks -
        # deliberately never written back to False->True anywhere else (see
        # stop()/run() below for why that matters).
        self._stop_requested = False

    def set_fractions(self, fractions: dict[str, float]) -> None:
        """`fractions` values are 0.0-1.0 - see DigitalTwin.set_joint_fraction
        for why this, and not raw degrees, is what the twin actually wants."""
        with self._lock:
            self._fractions = dict(fractions)

    def request_orbit(self, dx: float, dy: float) -> None:
        with self._lock:
            self._camera_ops.append(("orbit", dx, dy))

    def request_pan(self, dx: float, dy: float) -> None:
        with self._lock:
            self._camera_ops.append(("pan", dx, dy))

    def request_zoom(self, dy: float) -> None:
        with self._lock:
            self._camera_ops.append(("zoom", 0.0, dy))

    def request_reset_camera(self) -> None:
        with self._lock:
            self._reset_camera_requested = True

    def request_neutral_snapshot(self) -> None:
        """Ask for one render of this MJCF's own designed zero pose (every
        joint at 0 degrees) - used as a visual "aim for this" reference when
        the user is about to do the calibration half-turn-homing step by
        hand. Delivered once via neutral_pose_ready, then the loop resumes
        its normal live rendering - doesn't disturb the ongoing display."""
        with self._lock:
            self._neutral_requested = True

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            twin = DigitalTwin(self.mjcf_path, joint_names=self.joint_names)
        except Exception as exc:  # mujoco raises plain Exception/ValueError on bad XML
            self.load_failed.emit(str(exc))
            return

        # DigitalTwin construction above (MuJoCo XML compile + Renderer/GL
        # context setup) can easily take longer than the gap between
        # QThread.start() and this OS thread actually getting a slice of the
        # interpreter - confirmed directly: stop() was observed landing (and
        # setting _stop_requested) *while still inside* that construction,
        # before this method ever reached this line. The loop condition
        # below reads _stop_requested fresh on every pass and nothing here
        # ever writes it back to False, so a stop() that arrived before the
        # loop even started is honored immediately instead of being
        # silently overwritten by an unconditional "start running" flag.
        while not self._stop_requested:
            t0 = time.perf_counter()
            with self._lock:
                fractions = dict(self._fractions)
                neutral_requested = self._neutral_requested
                self._neutral_requested = False
                camera_ops = self._camera_ops
                self._camera_ops = []
                reset_camera = self._reset_camera_requested
                self._reset_camera_requested = False

            if reset_camera:
                twin.reset_camera()
            for op, dx, dy in camera_ops:
                if op == "orbit":
                    twin.orbit(dx, dy)
                elif op == "pan":
                    twin.pan(dx, dy)
                elif op == "zoom":
                    twin.zoom(dy)

            if neutral_requested:
                twin.set_all_deg(dict.fromkeys(self.joint_names, 0.0))
                self.neutral_pose_ready.emit(twin.render())
                continue  # skip this cycle's regular frame - next loop iteration resumes it

            twin.set_all_fractions(fractions)
            frame = twin.render()
            self.frame_ready.emit(frame)
            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, RENDER_INTERVAL_S - elapsed))
