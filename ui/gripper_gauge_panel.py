from __future__ import annotations

import csv
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.gripper_gauge import PRELOAD_RANGE, REPEATS_RANGE, SPEED_RANGE

MAX_HISTORY = 200

MODE_HINTS = {
    "measure": "Gentle: the gripper slows when it touches something, so the width does not depend on how hard you grip.",
    "normal": "Full speed: the gripper follows the leader as fast as the motor goes. For picking things up.",
}


class Fold(QFrame):
    """A section that opens and closes under its header, so rarely-used settings stay out of the way."""

    def __init__(self, title: str, body: QWidget, opened: bool = False, parent=None):
        super().__init__(parent)
        self.body = body
        self.header = QToolButton()
        self.header.setObjectName("foldHeader")
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.toggled.connect(self._on_toggled)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.header)
        layout.addWidget(body)
        self.set_title(title)
        self.header.setChecked(opened)
        self._on_toggled(opened)

    def set_title(self, title: str) -> None:
        self.header.setText(title)

    def set_open(self, opened: bool) -> None:
        self.header.setChecked(opened)

    def is_open(self) -> bool:
        return self.header.isChecked()

    def _on_toggled(self, opened: bool) -> None:
        self.header.setArrowType(Qt.DownArrow if opened else Qt.RightArrow)
        self.body.setVisible(opened)


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
            "teleop off. The force is capped at kp x Preload, and Stop aborts it. The big number is live: it follows the "
            "jaw whenever the gauge is calibrated, also while you drive the gripper with the leader, and it freezes "
            "(green) while the jaw holds something."
        )
        note.setObjectName("sectionCaption")
        note.setWordWrap(True)

        # -- settings -----------------------------------------------------------
        settings = QWidget()
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
        form.setContentsMargins(0, 4, 0, 0)
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
        calibration = QWidget()
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
        box.setContentsMargins(0, 4, 0, 0)
        box.addWidget(self.zero_btn)
        box.addLayout(size_row)
        box.addWidget(self.reference_list)
        box.addLayout(tail)
        box.addWidget(self.fit_label)

        # -- reading card: the live jaw opening, big -----------------------------------
        self.read_card = QFrame()
        self.read_card.setObjectName("readCard")
        self.read_card.setProperty("state", "free")
        self.read_caption = QLabel("Jaw opening")
        self.read_caption.setObjectName("readCaption")
        self.read_caption.setAlignment(Qt.AlignCenter)
        self.live_label = QLabel("-")
        self.live_label.setObjectName("readValue")
        self.live_label.setProperty("state", "free")
        self.live_label.setTextFormat(Qt.RichText)
        self.live_label.setAlignment(Qt.AlignCenter)
        self.live_label.setToolTip(
            "The jaw opening right now, from its encoder and the calibration below. It is exact at rest or "
            "touching an object; the gripper does not move for it, so it also works while you drive it by "
            "leader. Needs the gauge calibrated and the follower connected.")
        card = QVBoxLayout(self.read_card)
        card.setContentsMargins(12, 10, 12, 12)
        card.setSpacing(2)
        card.addWidget(self.read_caption)
        card.addWidget(self.live_label)

        # -- leader relay speed: Measure (gentle) or Normal (full speed) ---------------------
        self.mode_combo = QComboBox(self)   # the value other code reads and sets; the buttons below mirror it
        self.mode_combo.addItem("Measure: gentle, slows on contact", "measure")
        self.mode_combo.addItem("Normal: follow the leader at full speed", "normal")
        self.mode_combo.hide()
        self.mode_buttons: dict[str, QPushButton] = {}
        mode_group = QButtonGroup(self)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        for index, (key, text) in enumerate((("measure", "Measure"), ("normal", "Normal"))):
            button = QPushButton(text)
            button.setObjectName("segButton")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            mode_group.addButton(button)
            button.clicked.connect(lambda _=False, i=index: self.mode_combo.setCurrentIndex(i))
            self.mode_buttons[key] = button
            mode_row.addWidget(button)
        self.mode_hint = QLabel("")
        self.mode_hint.setObjectName("sectionCaption")
        self.mode_hint.setWordWrap(True)
        self.mode_combo.currentIndexChanged.connect(self._sync_mode_buttons)
        self.beep_check = QCheckBox("Beep when the gripper touches something")
        self.beep_check.setChecked(True)

        # -- measure object (the automatic creep-to-contact measurement) -------------------------
        self.measure_btn = QPushButton("Measure object")
        self.measure_btn.setObjectName("primaryButton")
        self.measure_btn.setToolTip("The jaws creep shut on the object by themselves and read its width.")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.status_label = QLabel("")
        self.status_label.setObjectName("sectionCaption")
        self.status_label.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.measure_btn, 1)
        buttons.addWidget(self.cancel_btn)

        # -- last measurement + history ----------------------------------------------------------
        self.result_label = QLabel("-")
        self.result_label.setObjectName("lastValue")
        self.result_label.setAlignment(Qt.AlignCenter)
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("sectionCaption")
        self.detail_label.setAlignment(Qt.AlignCenter)
        self.detail_label.setWordWrap(True)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #f5b544;")
        last_card = QFrame()
        last_card.setObjectName("lastCard")
        last = QVBoxLayout(last_card)
        last.setContentsMargins(12, 8, 12, 10)
        last.setSpacing(2)
        last_caption = QLabel("Last measurement")
        last_caption.setObjectName("sectionCaption")
        last.addWidget(last_caption)
        last.addWidget(self.result_label)
        last.addWidget(self.detail_label)
        last.addWidget(self.warning_label)

        self.history_list = QListWidget()
        self.history_list.setMinimumHeight(70)
        self.history_list.setMaximumHeight(110)
        self.csv_btn = QPushButton("Save CSV...")
        self.csv_btn.setToolTip("Save every measurement of this session (width, spread, angle, warnings) as a CSV file.")
        history_caption = QLabel("History")
        history_caption.setObjectName("sectionCaption")
        history_row = QHBoxLayout()
        history_row.addWidget(history_caption, 1)
        history_row.addWidget(self.csv_btn)

        # -- folds: settings and calibration stay out of the way until needed -------------------------
        self.calibration_fold = Fold("Calibration (gauge blocks)", calibration, opened=False)
        self.settings_fold = Fold("Settings", settings, opened=False)
        self.help_fold = Fold("How it works", note, opened=False)
        self._first_references = True

        # everything scrolls, so a short window never squeezes the controls over each other
        inner = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        root = QVBoxLayout(inner)
        root.setSpacing(10)
        root.addWidget(self.read_card)
        root.addWidget(QLabel("Leader relay speed"))
        root.addLayout(mode_row)
        root.addWidget(self.mode_hint)
        root.addWidget(self.beep_check)
        root.addLayout(buttons)
        root.addWidget(self.status_label)
        root.addWidget(last_card)
        root.addWidget(self.calibration_fold)   # the folds come before the history so they stay in view
        root.addWidget(self.settings_fold)
        root.addWidget(self.help_fold)
        root.addLayout(history_row)
        root.addWidget(self.history_list)
        root.addStretch(1)
        self._sync_mode_buttons()

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
    def _sync_mode_buttons(self, *_args) -> None:
        key = self.mode_combo.currentData()
        for name, button in self.mode_buttons.items():
            button.setChecked(name == key)
        self.mode_hint.setText(MODE_HINTS.get(key, ""))

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
        count = len(points)
        self.calibration_fold.set_title(
            "Calibration: not calibrated" if count < 2 else f"Calibration: {count} points")
        if self._first_references:
            self._first_references = False
            self.calibration_fold.set_open(count < 2)   # a gauge nobody calibrated yet asks for it up front

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

    def set_live(self, text: str, state: str = "free", caption: str = "Jaw opening", note: str = "") -> None:
        """The big reading. state: free (jaw moving or at rest) | grip (touching something, value latched)."""
        if not text:
            self.live_label.setText("-")
            self.read_caption.setText("Jaw opening")
            state = "free"
        else:
            small = f'<br><span style="font-size:12px;font-weight:400">{note}</span>' if note else ""
            self.live_label.setText(text + small)
            self.read_caption.setText(caption)
        for widget in (self.read_card, self.live_label):
            if widget.property("state") != state:
                widget.setProperty("state", state)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

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
