from __future__ import annotations

import numpy as np
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from core.camera_enum import list_cameras


class CameraPanel(QGroupBox):
    start_requested = Signal(int)   # camera index
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("CAMERA (optional)", parent)

        self.device_combo = QComboBox()
        self._refresh_devices()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_devices)

        self.toggle_btn = QPushButton("Start")
        self.toggle_btn.clicked.connect(self._on_toggle)

        self._running = False

        self.status_label = QLabel("")
        self.status_label.setObjectName("sectionCaption")

        self.view = QLabel("no camera feed")
        self.view.setMinimumSize(320, 240)
        self.view.setMaximumHeight(480)  # a placeholder/preview box, not "however tall the window happens to be"
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background-color: #101215; border: 1px solid #3a4048;")

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Device"))
        controls.addWidget(self.device_combo, 1)
        controls.addWidget(self.refresh_btn)
        controls.addWidget(self.toggle_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        layout.addWidget(self.view)
        layout.addStretch(1)  # leftover vertical room goes here, not into the view

    def _refresh_devices(self) -> None:
        current_index = self.selected_index()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        devices = list_cameras()
        if not devices:
            self.device_combo.addItem("no camera found", -1)
        for index, name in devices:
            self.device_combo.addItem(f"{name} (#{index})", index)
        # try to keep the same physical device selected across a refresh
        if current_index is not None:
            for i in range(self.device_combo.count()):
                if self.device_combo.itemData(i) == current_index:
                    self.device_combo.setCurrentIndex(i)
                    break
        self.device_combo.blockSignals(False)

    def selected_index(self) -> int | None:
        data = self.device_combo.currentData()
        return data if isinstance(data, int) and data >= 0 else None

    def _on_toggle(self) -> None:
        if not self._running:
            index = self.selected_index()
            if index is None:
                return
            self.start_requested.emit(index)
            self._running = True
            self.toggle_btn.setText("Stop")
            # locked while running: releasing one physical camera and opening
            # a genuinely different one in the same process reliably crashed
            # this OpenCV/Windows combo (reproduced many ways - it's not a
            # sequencing bug this app can paper over). Stop -> pick a
            # different device -> Start again is two clicks instead of an
            # instant swap, but it doesn't crash. Most camera apps require
            # this anyway.
            self.device_combo.setEnabled(False)
        else:
            self.stop_requested.emit()
            self._running = False
            self.toggle_btn.setText("Start")
            self.view.setText("no camera feed")
            self.device_combo.setEnabled(True)

    def set_busy(self, busy: bool, message: str = "") -> None:
        """Give visible feedback the instant a start/stop is requested,
        instead of the UI just sitting there looking frozen while a camera
        driver takes its time opening/closing in the background.

        Deliberately does NOT touch device_combo - that's locked/unlocked
        solely by _on_toggle based on running state, not busy state (it must
        stay locked for the whole time a camera is running, not just during
        the start/stop transition moment)."""
        self.status_label.setText(message)
        self.toggle_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)

    def show_frame(self, frame_rgb: np.ndarray) -> None:
        h, w, _ = frame_rgb.shape
        image = QImage(frame_rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        self.view.setPixmap(QPixmap.fromImage(image).scaled(
            self.view.width(), self.view.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def show_error(self, message: str) -> None:
        self.view.setText(message)
        self.status_label.setText("")
        self.toggle_btn.setEnabled(True)
        self.device_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self._running = False
        self.toggle_btn.setText("Start")
