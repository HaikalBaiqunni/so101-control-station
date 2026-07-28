from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.servo_bus import JOINT_ORDER

COLUMNS = ["Joint", "Pos", "Vel", "Load", "Current", "Volt", "Temp"]
STATS_WINDOW = 200  # ~20 s at the 10 Hz telemetry rate


class TelemetryPanel(QGroupBox):
    """Live read-out of the servos' own feedback registers, for deciding
    empirically which signal makes a usable grip-force proxy - the point is to
    watch how noisy Present_Current is against Present_Load, and whether a
    servo-side Torque_Limit holds steadier than chasing a current threshold
    from the control loop. CSV logging is here because that judgement really
    wants a plot, not a flickering table."""

    log_toggled = Signal(bool)
    register_write_requested = Signal(str, int, str)  # data_name, value, joint ("" = all)

    def __init__(self, parent=None):
        super().__init__("SERVO TELEMETRY", parent)

        self.table = QTableWidget(len(JOINT_ORDER), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, name in enumerate(JOINT_ORDER):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            for col in range(1, len(COLUMNS)):
                item = QTableWidgetItem("-")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        # tall enough for all six joints plus the header - gripper is the row
        # that actually matters here, so it must never be the one scrolled off
        self.table.setMinimumHeight(
            self.table.horizontalHeader().height()
            + len(JOINT_ORDER) * self.table.verticalHeader().defaultSectionSize()
            + 2 * self.table.frameWidth()
            + 4
        )

        self.caption = QLabel(
            "Raw registers at ~10 Hz. Volt = 0.1 V units, Temp = deg C. "
            "Load/Current encoding is unverified - compare relative stability, "
            "not absolute magnitude."
        )
        self.caption.setObjectName("sectionCaption")
        self.caption.setWordWrap(True)

        # -- noise tracker for one joint (the whole reason this panel exists) --
        self.watch_combo = QComboBox()
        self.watch_combo.addItems(JOINT_ORDER)
        self.watch_combo.setCurrentText("gripper")
        self.watch_combo.currentTextChanged.connect(lambda _: self._reset_stats())

        self.watch_field = QComboBox()
        self.watch_field.addItems(["current", "load", "velocity", "voltage", "temperature"])
        self.watch_field.currentTextChanged.connect(lambda _: self._reset_stats())

        self.stats_label = QLabel("no samples yet")
        self.stats_label.setObjectName("sectionCaption")

        self.reset_stats_btn = QPushButton("Reset")
        self.reset_stats_btn.clicked.connect(self._reset_stats)

        watch_row = QHBoxLayout()
        watch_row.addWidget(QLabel("Watch"))
        watch_row.addWidget(self.watch_combo)
        watch_row.addWidget(self.watch_field)
        watch_row.addWidget(self.reset_stats_btn)
        watch_row.addWidget(self.stats_label, 1)

        # -- CSV capture --
        self.log_btn = QPushButton("Start CSV Log")
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self._on_log_toggled)
        self.log_path_label = QLabel("")
        self.log_path_label.setObjectName("sectionCaption")

        log_row = QHBoxLayout()
        log_row.addWidget(self.log_btn)
        log_row.addWidget(self.log_path_label, 1)

        # -- servo-side settings worth experimenting with --
        self.torque_limit_spin = QSpinBox()
        self.torque_limit_spin.setRange(0, 1000)
        self.torque_limit_spin.setValue(500)
        self.torque_limit_spin.setToolTip(
            "Torque_Limit (SRAM, reg 48). Caps the servo's own output, so the\n"
            "servo holds a roughly constant force in its internal loop instead\n"
            "of us watching Present_Current at 60 Hz and reacting ~16 ms late."
        )
        self.torque_limit_btn = QPushButton("Apply to gripper")
        self.torque_limit_btn.clicked.connect(
            lambda: self.register_write_requested.emit("Torque_Limit", self.torque_limit_spin.value(), "gripper")
        )

        self.deadband_spin = QSpinBox()
        self.deadband_spin.setRange(0, 32)
        self.deadband_spin.setValue(1)
        self.deadband_spin.setToolTip(
            "CW/CCW_Dead_Zone (EEPROM, regs 26/27). Inside this band the servo\n"
            "stops correcting, so it sets a hard floor on position\n"
            "repeatability. Smaller is tighter but can cause hunting/buzzing."
        )
        self.deadband_btn = QPushButton("Apply to gripper")
        self.deadband_btn.clicked.connect(self._apply_deadband)

        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Torque_Limit"))
        settings_row.addWidget(self.torque_limit_spin)
        settings_row.addWidget(self.torque_limit_btn)
        settings_row.addSpacing(16)
        settings_row.addWidget(QLabel("Dead_Zone"))
        settings_row.addWidget(self.deadband_spin)
        settings_row.addWidget(self.deadband_btn)
        settings_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(self.caption)
        layout.addLayout(watch_row)
        layout.addLayout(log_row)
        layout.addLayout(settings_row)

        self._samples: deque = deque(maxlen=STATS_WINDOW)

    # ---------------------------------------------------------------- internals
    def _apply_deadband(self) -> None:
        value = self.deadband_spin.value()
        self.register_write_requested.emit("CW_Dead_Zone", value, "gripper")
        self.register_write_requested.emit("CCW_Dead_Zone", value, "gripper")

    def _reset_stats(self) -> None:
        self._samples.clear()
        self.stats_label.setText("no samples yet")

    def _on_log_toggled(self, on: bool) -> None:
        self.log_btn.setText("Stop CSV Log" if on else "Start CSV Log")
        self.log_toggled.emit(on)

    # ---------------------------------------------------------------- public API
    def watched(self) -> tuple[str, str]:
        return self.watch_combo.currentText(), self.watch_field.currentText()

    def set_log_path(self, text: str) -> None:
        self.log_path_label.setText(text)

    def update_telemetry(self, telemetry: dict[str, dict[str, int]]) -> None:
        for row, name in enumerate(JOINT_ORDER):
            values = telemetry.get(name)
            if not values:
                continue
            for col, key in enumerate(
                ("position", "velocity", "load", "current", "voltage", "temperature"), start=1
            ):
                self.table.item(row, col).setText(str(values.get(key, "-")))

        joint, field = self.watched()
        value = telemetry.get(joint, {}).get(field)
        if value is None:
            return
        self._samples.append(value)
        lo, hi = min(self._samples), max(self._samples)
        mean = sum(self._samples) / len(self._samples)
        self.stats_label.setText(
            f"{joint}.{field}  now {value}   min {lo}   max {hi}   "
            f"spread {hi - lo}   mean {mean:.1f}   n={len(self._samples)}"
        )
