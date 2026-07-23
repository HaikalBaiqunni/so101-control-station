"""
Background QThread workers so blocking hardware I/O (serial, camera, joystick)
never freezes the Qt UI thread. Each worker only talks to the outside world
through Qt signals / a thread-safe command queue - never touch a worker's
internal objects directly from the GUI thread.
"""
from __future__ import annotations

import queue
import sys
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from .servo_bus import ServoBus, ServoBusError

POLL_INTERVAL_S = 1 / 60  # ~60 Hz - matches lerobot-teleoperate's own loop rate now that
                          # reads/writes are batched (GroupSyncRead/Write); at 20Hz + one
                          # round trip per joint this used to be the visible lag vs the CLI.


class RobotWorker(QThread):
    positions_updated = Signal(dict)   # {joint_name: degrees}
    error = Signal(str)
    connection_changed = Signal(bool)

    def __init__(self, port: str, calibration_path: str, parent=None):
        super().__init__(parent)
        self.port = port
        self.calibration_path = calibration_path
        # goal commands are coalesced (latest-value-wins per joint) so a fast
        # slider drag can't flood the bus with dozens of superseded writes -
        # torque commands are rare/discrete and go through a plain queue.
        self._pending_goals: dict[str, float] = {}
        self._goals_lock = threading.Lock()
        self._torque_commands: queue.Queue = queue.Queue()
        self._running = False
        self.bus: ServoBus | None = None

    # -- thread-safe public API (call from GUI thread) ----------------------
    def request_goal(self, name: str, degrees: float) -> None:
        with self._goals_lock:
            self._pending_goals[name] = degrees

    def request_torque(self, enabled: bool, name: str | None = None) -> None:
        self._torque_commands.put((enabled, name))

    def stop(self) -> None:
        self._running = False

    # -- worker thread body ---------------------------------------------------
    def run(self) -> None:
        self.bus = ServoBus(self.port)
        try:
            self.bus.connect()
            self.bus.load_calibration(self.calibration_path)
            self.bus.apply_homing_offsets()
        except (ServoBusError, OSError, FileNotFoundError) as exc:
            self.error.emit(str(exc))
            self.connection_changed.emit(False)
            return

        self.connection_changed.emit(True)
        self._running = True

        while self._running:
            with self._goals_lock:
                goals, self._pending_goals = self._pending_goals, {}
            if goals:
                try:
                    self.bus.write_goals_deg(goals)  # one bus transaction for every joint that changed
                except ServoBusError as exc:
                    self.error.emit(str(exc))

            while True:
                try:
                    enabled, name = self._torque_commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    (self.bus.enable_torque if enabled else self.bus.disable_torque)(name)
                except ServoBusError as exc:
                    self.error.emit(str(exc))

            try:
                positions = self.bus.read_all_positions_deg()
                self.positions_updated.emit(positions)
            except ServoBusError as exc:
                self.error.emit(str(exc))

            time.sleep(POLL_INTERVAL_S)

        self.bus.disconnect()
        self.connection_changed.emit(False)


class CameraWorker(QThread):
    frame_ready = Signal(np.ndarray)  # RGB uint8 HxWx3
    error = Signal(str)
    started_ok = Signal()  # opened successfully, about to start streaming frames

    def __init__(self, index: int, fps: int = 20, parent=None):
        super().__init__(parent)
        self.index = index
        self.fps = fps
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        # On Windows, OpenCV's default backend (Media Foundation) can take
        # 10-15+ seconds to open some USB webcams; DirectShow opens the same
        # device in under a second. DirectShow is reliable for (re)opening
        # the SAME device repeatedly (the actual common case).
        #
        # Deliberately does NOT call cap.set(CAP_PROP_FRAME_WIDTH/HEIGHT) -
        # forcing a resolution change is what was actually crashing this
        # camera on reopen (confirmed by direct A/B testing: identical code
        # minus these two calls reopens the same device fine, indefinitely;
        # with them, it reliably crashes the whole process). Not COM, not
        # the backend choice, not Qt threading - just this. The camera opens
        # at its own default/native resolution instead, which every USB
        # webcam supports by definition.
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            self.error.emit(f"Could not open camera index {self.index}")
            return

        self.started_ok.emit()
        self._running = True
        period = 1.0 / max(self.fps, 1)
        while self._running:
            ok, frame_bgr = cap.read()
            if ok:
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                self.frame_ready.emit(frame_rgb)
            time.sleep(period)
        cap.release()


class GamepadWorker(QThread):
    axes_updated = Signal(dict)   # {axis_index: value in [-1, 1]}
    button_pressed = Signal(int)
    no_gamepad = Signal()

    def __init__(self, poll_hz: int = 30, parent=None):
        super().__init__(parent)
        self.poll_hz = poll_hz
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        import pygame

        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            self.no_gamepad.emit()
            pygame.quit()
            return

        joy = pygame.joystick.Joystick(0)
        joy.init()
        prev_buttons = [0] * joy.get_numbuttons()

        self._running = True
        period = 1.0 / max(self.poll_hz, 1)
        while self._running:
            pygame.event.pump()
            axes = {i: joy.get_axis(i) for i in range(joy.get_numaxes())}
            self.axes_updated.emit(axes)
            for i in range(joy.get_numbuttons()):
                pressed = joy.get_button(i)
                if pressed and not prev_buttons[i]:
                    self.button_pressed.emit(i)
                prev_buttons[i] = pressed
            time.sleep(period)

        pygame.quit()
