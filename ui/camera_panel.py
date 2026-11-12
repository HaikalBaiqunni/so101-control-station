from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.camera_enum import list_camera_sources

MAX_CAMERAS = 3


class CameraSlot(QWidget):
    """One camera: its device picker, Start / Stop and its own view."""

    start_requested = Signal(object)   # camera index (int) or "basler:<serial>"
    stop_requested = Signal()
    maximize_toggled = Signal(bool)

    def __init__(self, number: int, parent=None):
        super().__init__(parent)
        self.number = number
        self.running_source: int | str | None = None
        self._running = False

        self.title_label = QLabel(f"Camera {number}")
        self.device_combo = QComboBox()
        # A long device name must not set the slot's minimum width: the picker shrinks and elides instead.
        self.device_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.device_combo.setMinimumContentsLength(10)

        self.toggle_btn = QPushButton("Start")
        self.toggle_btn.clicked.connect(self._on_toggle)

        self.max_btn = QToolButton()
        self.max_btn.setObjectName("tileButton")
        self.max_btn.setText("Expand")
        self.max_btn.setCheckable(True)
        self.max_btn.setToolTip("Show only this camera, as large as the area allows (Esc to restore)")
        self.max_btn.toggled.connect(self._on_max)
        self.max_btn.hide()   # only the Workspace layout has room to maximise into

        self.status_label = QLabel("")
        self.status_label.setObjectName("sectionCaption")

        self.view = QLabel("no camera feed")
        self.view.setMinimumSize(200, 140)
        # Same hint-follows-pixmap ratchet TwinPanel.view had: show_frame()
        # scales each frame to the label's current size, which then becomes
        # the label's sizeHint, which the splitter grants, which makes the
        # next frame bigger. Ignored on both axes keeps the box at whatever
        # the splitter gave it, feed running or not.
        self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background-color: #0d1014; border: 1px solid #2a303a; border-radius: 10px;")

        controls = QHBoxLayout()
        controls.addWidget(self.title_label)
        controls.addWidget(self.device_combo, 1)
        controls.addWidget(self.toggle_btn)
        controls.addWidget(self.max_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        # With an Ignored policy the view has no height opinion of its own,
        # so it takes the leftover room and no stretch item competes for it.
        layout.addWidget(self.view, 1)

    @property
    def running(self) -> bool:
        return self._running

    def _on_max(self, on: bool) -> None:
        self.max_btn.setText("Restore" if on else "Expand")
        self.maximize_toggled.emit(on)

    def set_devices(self, devices: list[tuple], prefer=None) -> None:
        """Fill the picker; keep the current device (or `prefer`) selected when it is still listed."""
        keep = prefer if prefer is not None else self.selected_index()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        if not devices:
            self.device_combo.addItem("no camera found", -1)
        for source, label in devices:
            self.device_combo.addItem(label, source)
        if keep is not None:
            for i in range(self.device_combo.count()):
                if self.device_combo.itemData(i) == keep:
                    self.device_combo.setCurrentIndex(i)
                    break
        self.device_combo.blockSignals(False)

    def selected_index(self) -> int | str | None:
        """The chosen camera: an OpenCV index, or "basler:<serial>" for a Basler camera."""
        data = self.device_combo.currentData()
        if isinstance(data, str) and data:
            return data
        return data if isinstance(data, int) and data >= 0 else None

    def _on_toggle(self) -> None:
        if not self._running:
            index = self.selected_index()
            if index is None:
                return
            self.start_requested.emit(index)
        else:
            self.mark_stopped()
            self.stop_requested.emit()

    def mark_running(self, source) -> None:
        self._running = True
        self.running_source = source
        self.toggle_btn.setText("Stop")
        # locked while running: releasing one physical camera and opening
        # a genuinely different one in the same process reliably crashed
        # this OpenCV/Windows combo (reproduced many ways - it's not a
        # sequencing bug this app can paper over). Stop -> pick a
        # different device -> Start again is two clicks instead of an
        # instant swap, but it doesn't crash. Most camera apps require
        # this anyway.
        self.device_combo.setEnabled(False)

    def mark_stopped(self) -> None:
        self._running = False
        self.running_source = None
        self.toggle_btn.setText("Start")
        self.view.clear()
        self.view.setText("no camera feed")
        self.device_combo.setEnabled(True)

    def set_busy(self, busy: bool, message: str = "") -> None:
        """Give visible feedback the instant a start/stop is requested,
        instead of the UI just sitting there looking frozen while a camera
        driver takes its time opening/closing in the background.

        Deliberately does NOT touch device_combo - that's locked/unlocked
        solely by mark_running / mark_stopped, not busy state (it must
        stay locked for the whole time a camera is running, not just during
        the start/stop transition moment)."""
        self.status_label.setText(message)
        self.toggle_btn.setEnabled(not busy)

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
        self._running = False
        self.running_source = None
        self.toggle_btn.setText("Start")


class CameraPanel(QGroupBox):
    """Up to MAX_CAMERAS cameras at once (e.g. one on the robot and one watching it), each its own slot.
    Slot 0 keeps the single-camera names (device_combo, toggle_btn, ...) so older code still reads naturally."""

    start_requested = Signal(object, int)   # source, slot
    stop_requested = Signal(int)            # slot
    slots_changed = Signal(int)             # how many slots exist now
    maximize_toggled = Signal(bool)         # one camera wants (or gives back) the whole area

    def __init__(self, parent=None):
        super().__init__("CAMERAS (optional)", parent)
        self._devices: list[tuple] = []
        self.slots: list[CameraSlot] = []
        self._maximize_available = False
        self._columns = 2

        self.add_btn = QPushButton("+ Add camera")
        self.add_btn.setToolTip(f"Show another camera next to this one (up to {MAX_CAMERAS}). Each camera is "
                                "chosen and started on its own.")
        self.add_btn.clicked.connect(self.add_slot)
        self.refresh_btn = QPushButton("Refresh")   # one scan fills every camera's picker
        self.refresh_btn.setToolTip("Re-scan cameras. Basler cameras need pypylon (pip install pypylon); RealSense cameras (with depth) need pyrealsense2 (pip install pyrealsense2); the L515 needs the helper env (see README).")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.remove_btn = QPushButton("- Remove last")
        self.remove_btn.setToolTip("Remove the last extra camera (stop it first).")
        self.remove_btn.clicked.connect(self.remove_last_slot)

        self._grid = QGridLayout()
        self._grid.setSpacing(8)
        buttons = QHBoxLayout()
        buttons.addWidget(self.refresh_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.remove_btn)
        buttons.addWidget(self.add_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(self._grid, 1)
        layout.addLayout(buttons)

        self._devices = list_camera_sources()
        self.add_slot()

    # ---------------------------------------------------------------- slots
    def add_slot(self) -> CameraSlot | None:
        if len(self.slots) >= MAX_CAMERAS:
            return None
        slot = CameraSlot(len(self.slots) + 1)
        index = len(self.slots)
        # Offer a camera no other slot has picked, so a second slot does not default to the first one's device.
        taken = {s.selected_index() for s in self.slots}
        free = next((src for src, _label in self._devices if src not in taken), None)
        slot.set_devices(self._devices, prefer=free)
        slot.start_requested.connect(lambda source, i=index: self._on_slot_start(i, source))
        slot.stop_requested.connect(lambda i=index: self.stop_requested.emit(i))
        slot.maximize_toggled.connect(lambda on, s=slot: self._on_slot_max(s, on))
        slot.max_btn.setVisible(self._maximize_available)
        self.restore()   # a maximised camera would hide the new one
        self.slots.append(slot)
        self._relayout()
        self.slots_changed.emit(len(self.slots))
        return slot

    def remove_last_slot(self) -> None:
        if len(self.slots) <= 1 or self.slots[-1].running:
            return
        self.restore()
        slot = self.slots.pop()
        self._grid.removeWidget(slot)
        slot.setParent(None)
        slot.deleteLater()
        self._relayout()
        self.slots_changed.emit(len(self.slots))

    def set_columns(self, columns: int) -> None:
        """Cameras per row: 2 for the wide Classic card, 1 for the Workspace's column next to the twin."""
        self._columns = max(1, columns)
        self._relayout()

    def _relayout(self) -> None:
        per_row = self._columns
        for i, slot in enumerate(self.slots):
            self._grid.addWidget(slot, i // per_row, i % per_row)
        columns = min(len(self.slots), per_row)
        rows = (len(self.slots) + per_row - 1) // per_row
        for c in range(MAX_CAMERAS):
            self._grid.setColumnStretch(c, 1 if c < columns else 0)
        for r in range(MAX_CAMERAS):
            self._grid.setRowStretch(r, 1 if r < rows else 0)
        self.add_btn.setEnabled(len(self.slots) < MAX_CAMERAS)
        self.remove_btn.setEnabled(len(self.slots) > 1)
        for slot in self.slots:
            slot.title_label.setVisible(len(self.slots) > 1)

    # ---------------------------------------------------------------- maximise
    def set_maximize_available(self, available: bool) -> None:
        self._maximize_available = available
        for slot in self.slots:
            slot.max_btn.setVisible(available)

    def _on_slot_max(self, chosen: CameraSlot, on: bool) -> None:
        for slot in self.slots:
            if slot is not chosen:
                slot.max_btn.blockSignals(True)
                slot.max_btn.setChecked(False)
                slot.max_btn.setText("Expand")
                slot.max_btn.blockSignals(False)
                slot.setVisible(not on)
        chosen.show()
        self.maximize_toggled.emit(on)

    def restore(self) -> None:
        """Back to every camera side by side."""
        for slot in self.slots:
            if slot.max_btn.isChecked():
                slot.max_btn.setChecked(False)   # runs _on_slot_max(slot, False)

    def slot(self, number: int) -> CameraSlot:
        return self.slots[number]

    def _on_slot_start(self, number: int, source) -> None:
        for other_number, other in enumerate(self.slots):
            if other_number != number and other.running_source == source:
                # One physical camera cannot be opened twice (a Basler is exclusive; USB webcams misbehave).
                self.slots[number].show_error(f"Already shown in camera {other.number}: pick a different device")
                return
        self.slots[number].mark_running(source)
        self.start_requested.emit(source, number)

    def refresh_devices(self) -> None:
        self._devices = list_camera_sources()
        for slot in self.slots:
            if not slot.running:
                slot.set_devices(self._devices)

    # ---------------------------------------------------------------- slot 0 (the original single-camera names)
    @property
    def device_combo(self) -> QComboBox:
        return self.slots[0].device_combo

    @property
    def toggle_btn(self) -> QPushButton:
        return self.slots[0].toggle_btn

    @property
    def view(self) -> QLabel:
        return self.slots[0].view

    @property
    def status_label(self) -> QLabel:
        return self.slots[0].status_label

    def selected_index(self) -> int | str | None:
        return self.slots[0].selected_index()

    def set_busy(self, busy: bool, message: str = "", number: int = 0) -> None:
        self.slots[number].set_busy(busy, message)

    def show_frame(self, frame_rgb: np.ndarray, number: int = 0) -> None:
        self.slots[number].show_frame(frame_rgb)

    def show_error(self, message: str, number: int = 0) -> None:
        self.slots[number].show_error(message)
