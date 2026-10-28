from __future__ import annotations

import csv
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.gripper_gauge import PRELOAD_RANGE, REPEATS_RANGE, SPEED_RANGE

MAX_HISTORY = 200


class GripperGaugePanel(QWidget):
    """Gripper as a rough caliper. MainWindow owns the measurement and the calibration data;
    this is the form, the reference list, the result and the history."""

    measure_requested = Signal(str, float)   # "zero" | "reference" | "object", size in mm (0 for zero / object)
    cancel_requested = Signal()
    settings_changed = Signal()              # direction, speed, preload or repeats edited
    delete_reference = Signal(int)
    clear_references = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[dict] = []

        note = QLabel(
            "Closes the gripper gently on an object and converts the jaw angle to a width. "
            "Calibrate once with gauge blocks, then measure. Start every measurement with the jaws open and the object loose between them. Only with torque ON, MIT mode and "
            "teleop off. The force is capped at kp x Preload, and Stop aborts it."
        )
        note.setObjectName("sectionCaption")
        note.setWordWrap(True)

        # -- settings -----------------------------------------------------------
        settings = QGroupBox("SETTINGS")
        self.direction_combo = QComboBox()
        self.direction_combo.addItem("angle increases when closing", 1)
        self.direction_combo.addItem("angle decreases when closing", -1)
        self.direction_combo.setToolTip(
            "Which way the gripper's angle moves when the jaws close. Check it on the Jog panel's "
            "gripper reading. Changing it clears the references.")
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(*SPEED_RANGE)
        self.speed_spin.setDecimals(0)
        self.speed_spin.setValue(12)
        self.speed_spin.setSuffix(" deg/s")
        self.speed_spin.setToolTip("How fast the jaws creep toward closed while looking for contact.")
        self.preload_spin = QDoubleSpinBox()
        self.preload_spin.setRange(*PRELOAD_RANGE)
        self.preload_spin.setDecimals(1)
        self.preload_spin.setSingleStep(0.5)
        self.preload_spin.setValue(3.0)
        self.preload_spin.setSuffix(" deg")
        self.preload_spin.setToolTip(
            "How far the target may run ahead of the jaw. In MIT mode the push is kp x this angle, "
            "so it is the force limit. Too low and friction wins; too high squeezes harder.")
        self.max_push_spin = QDoubleSpinBox()
        self.max_push_spin.setRange(*PRELOAD_RANGE)
        self.max_push_spin.setDecimals(1)
        self.max_push_spin.setSingleStep(0.5)
        self.max_push_spin.setValue(8.0)
        self.max_push_spin.setSuffix(" deg")
        self.max_push_spin.setToolTip(
            "Friction can hold the jaw still under the gentle Preload push. A stop is only believed after "
            "the push was raised stepwise to this (never above the tool's N*m ceiling). It is also the most "
            "the leader can squeeze the follower's gripper: the target never runs further ahead of the jaw. "
            "Raise it if a measurement says the jaw did not move; set it equal to Preload to never push harder.")
        self.repeats_spin = QSpinBox()
        self.repeats_spin.setRange(*REPEATS_RANGE)
        self.repeats_spin.setValue(3)
        self.repeats_spin.setToolTip("Passes per measurement. One pass has no consistency check.")
        form = QFormLayout(settings)
        form.addRow("Closing", self.direction_combo)
        form.addRow("Speed", self.speed_spin)
        form.addRow("Preload", self.preload_spin)
        form.addRow("Break-away push", self.max_push_spin)
        form.addRow("Passes", self.repeats_spin)
        self.force_label = QLabel("")
        self.force_label.setObjectName("sectionCaption")
        self.force_label.setWordWrap(True)
        form.addRow(self.force_label)

        # -- calibration ----------------------------------------------------------
        calibration = QGroupBox("CALIBRATION (gauge blocks)")
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(0.1, 300.0)
        self.size_spin.setDecimals(2)
        self.size_spin.setSingleStep(0.5)
        self.size_spin.setValue(10.0)
        self.size_spin.setSuffix(" mm")
        self.zero_btn = QPushButton("Measure zero (jaws open a little, nothing between)")
        self.reference_btn = QPushButton("Measure this block")
        self.reference_btn.setToolTip(
            "Put a gauge block of the size above loosely between the OPEN jaws (not touching), then press: "
            "the gripper closes on it by itself.")
        self.delete_btn = QPushButton("Delete selected")
        self.clear_btn = QPushButton("Clear all")
        self.reference_list = QListWidget()
        self.reference_list.setMinimumHeight(110)
        self.fit_label = QLabel("")
        self.fit_label.setObjectName("sectionCaption")
        self.fit_label.setWordWrap(True)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Block size"))
        size_row.addWidget(self.size_spin, 1)
        size_row.addWidget(self.reference_btn)
        tail = QHBoxLayout()
        tail.addWidget(self.delete_btn)
        tail.addWidget(self.clear_btn)
        box = QVBoxLayout(calibration)
        box.addWidget(self.zero_btn)
        box.addLayout(size_row)
        box.addWidget(self.reference_list)
        box.addLayout(tail)
        box.addWidget(self.fit_label)

        # -- measure --------------------------------------------------------------
        measure = QGroupBox("MEASURE")
        self.live_label = QLabel("")
        self.live_label.setObjectName("poseReadout")
        self.live_label.setAlignment(Qt.AlignCenter)
        self.live_label.setToolTip(
            "The jaw opening right now, from its encoder and the calibration below. It is exact at rest or "
            "touching an object; the gripper does not move for it, so it also works while you drive it by "
            "leader. Needs the gauge calibrated and the follower connected.")
        self.result_label = QLabel("-")
        self.result_label.setObjectName("poseReadout")
        self.result_label.setAlignment(Qt.AlignCenter)
        self.result_label.setStyleSheet("font-size: 30px; font-weight: 700;")   # the number is the point of the panel
        self.result_label.setMinimumHeight(52)
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("sectionCaption")
        self.detail_label.setAlignment(Qt.AlignCenter)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #f5b544;")
        self.measure_btn = QPushButton("Measure object")
        self.measure_btn.setObjectName("primaryButton")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.history_list = QListWidget()
        self.history_list.setMinimumHeight(110)
        self.csv_btn = QPushButton("Save history as CSV...")
        buttons = QHBoxLayout()
        buttons.addWidget(self.measure_btn, 1)
        buttons.addWidget(self.cancel_btn)
        box2 = QVBoxLayout(measure)
        box2.addWidget(QLabel("Live jaw opening"))
        box2.addWidget(self.live_label)
        box2.addWidget(self.result_label)
        box2.addWidget(self.detail_label)
        box2.addWidget(self.warning_label)
        box2.addLayout(buttons)
        box2.addWidget(self.status_label)
        box2.addWidget(self.history_list)
        box2.addWidget(self.csv_btn)

        # everything scrolls, so a short window never squeezes the controls over each other
        inner = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        root = QVBoxLayout(inner)
        root.addWidget(note)
        root.addWidget(settings)
        root.addWidget(calibration)
        root.addWidget(measure)
        root.addStretch(1)

        self.zero_btn.clicked.connect(lambda: self.measure_requested.emit("zero", 0.0))
        self.reference_btn.clicked.connect(lambda: self.measure_requested.emit("reference", self.size_spin.value()))
        self.measure_btn.clicked.connect(lambda: self.measure_requested.emit("object", 0.0))
        self.cancel_btn.clicked.connect(self.cancel_requested)
        self.delete_btn.clicked.connect(self._on_delete)
        self.clear_btn.clicked.connect(self.clear_references)
        self.csv_btn.clicked.connect(self._save_csv)
        for signal in (self.direction_combo.currentIndexChanged, self.speed_spin.valueChanged,
                       self.preload_spin.valueChanged, self.max_push_spin.valueChanged,
                       self.repeats_spin.valueChanged):
            signal.connect(lambda *_: self.settings_changed.emit())
        self._running = False

    # -- values ---------------------------------------------------------------
    def close_sign(self) -> int:
        return int(self.direction_combo.currentData())

    def set_values(self, close_sign: int, speed: float, preload: float, repeats: int, max_push: float = 8.0) -> None:
        """Load saved settings without firing settings_changed."""
        for widget in (self.direction_combo, self.speed_spin, self.preload_spin, self.max_push_spin, self.repeats_spin):
            widget.blockSignals(True)
        self.direction_combo.setCurrentIndex(0 if close_sign >= 0 else 1)
        self.speed_spin.setValue(speed)
        self.preload_spin.setValue(preload)
        self.max_push_spin.setValue(max_push)
        self.repeats_spin.setValue(repeats)
        for widget in (self.direction_combo, self.speed_spin, self.preload_spin, self.max_push_spin, self.repeats_spin):
            widget.blockSignals(False)

    # -- driven by MainWindow -------------------------------------------------
    def set_running(self, running: bool) -> None:
        self._running = running
        self.cancel_btn.setEnabled(running)
        for widget in (self.measure_btn, self.zero_btn, self.reference_btn, self.delete_btn, self.clear_btn,
                       self.direction_combo, self.speed_spin, self.preload_spin, self.max_push_spin, self.repeats_spin,
                       self.size_spin, self.reference_list):
            widget.setEnabled(not running)

    def is_running(self) -> bool:
        return self._running

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_force_hint(self, text: str, ok: bool) -> None:
        self.force_label.setText(text)
        self.force_label.setStyleSheet("" if ok else "color: #ff6b6b;")

    def set_references(self, points: list[tuple[float, float]], fit_text: str, fit_ok: bool) -> None:
        self.reference_list.clear()
        for angle, size in points:
            self.reference_list.addItem(f"{size:g} mm  at  {angle:.2f} deg")
        self.fit_label.setText(fit_text)
        self.fit_label.setStyleSheet("" if fit_ok else "color: #f5b544;")

    def show_result(self, width_mm: float, spread_mm: float, angle: float, passes: int, warnings: list[str]) -> None:
        self.result_label.setText(f"{width_mm:.2f} mm")
        self.detail_label.setText(f"+/- {spread_mm:.2f} mm between {passes} pass(es)  -  jaw at {angle:.2f} deg")
        self.warning_label.setText("\n".join(warnings))
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "width_mm": round(width_mm, 3),
               "spread_mm": round(spread_mm, 3), "angle_deg": round(angle, 3), "passes": passes,
               "warnings": "; ".join(warnings)}
        self._history.append(row)
        del self._history[:-MAX_HISTORY]
        self.history_list.addItem(f"{row['time'][11:]}   {width_mm:.2f} mm  +/- {spread_mm:.2f}")
        self.history_list.scrollToBottom()

    def set_live(self, text: str) -> None:
        self.live_label.setText(text or "-")

    def clear_result(self) -> None:
        self.result_label.setText("-")
        self.detail_label.setText("")
        self.warning_label.setText("")

    # -- internals ------------------------------------------------------------
    def _on_delete(self) -> None:
        row = self.reference_list.currentRow()
        if row >= 0:
            self.delete_reference.emit(row)

    def _save_csv(self) -> None:
        if not self._history:
            self.set_status("Nothing to save yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save measurements", "gripper_measurements.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(self._history[0]))
                writer.writeheader()
                writer.writerows(self._history)
        except OSError as exc:
            self.set_status(f"Could not save: {exc}")
            return
        self.set_status(f"Saved {len(self._history)} measurements to {path}")

    def history_rows(self) -> list[dict]:
        return list(self._history)
