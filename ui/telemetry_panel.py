from __future__ import annotations

import time
from collections import deque

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.servo_bus import JOINT_ORDER, decode_sign_magnitude
from .style import COLORS

COLUMNS = ["Joint", "Pos (ticks)", "Vel (deg/s)*", "Load (%)", "Current (mA)", "Volt (V)", "Temp (C)"]
STATS_WINDOW = 200  # ~20 s at the 10 Hz telemetry rate

DEG_PER_TICK = 360.0 / 4096.0  # same 12-bit encoder resolution as Present_Position

# Scale factors cross-checked two ways: LeRobot's own sign-bit table (which
# encodes Present_Load's direction bit at index 10 and Present_Velocity's at
# index 15 - both match below) and a community register reference whose
# accompanying driver code (servo.py) actually implements these exact
# formulas, not just documents them. Current has no sign bit in either
# source, so it decodes as plain unsigned. Position is deliberately left as
# raw ticks here rather than converted to degrees - the Joint Control panel
# already shows the CALIBRATED degree value for the same joint, and a second,
# differently-zeroed "degrees" number next to it would only confuse the two.
#
# Velocity is the one exception: no source actually documents Present_Speed's
# unit, so deg/s here is a DERIVED estimate (same encoder resolution as
# position, applied to its rate of change) rather than a confirmed figure -
# hence the "*" in its column header. Worth confirming empirically (command a
# known Goal_Velocity, time a known angle of travel) before trusting it.
def _convert(field: str, raw: int) -> tuple[float, str]:
    if field == "current":
        return raw * 6.5, "mA"
    if field == "voltage":
        return raw * 0.1, "V"
    if field == "temperature":
        return float(raw), "°C"
    if field == "load":
        return decode_sign_magnitude(raw, 10) / 10.0, "%"
    if field == "velocity":
        return decode_sign_magnitude(raw, 15) * DEG_PER_TICK, "deg/s*"
    return float(raw), ""


def _ticks_delta(current: int, previous: int, wheel: int = 4096) -> int:
    """Shortest signed distance from `previous` to `current` on a 12-bit
    wraparound encoder - without this, a continuous-turn joint (wrist_roll)
    crossing the 0/4095 boundary would register as a ~4096-tick spike instead
    of the small step it actually was."""
    delta = current - previous
    if delta > wheel // 2:
        delta -= wheel
    elif delta < -wheel // 2:
        delta += wheel
    return delta


class TelemetryPanel(QGroupBox):
    """Live read-out of the servos' own feedback registers, for deciding
    empirically which signal makes a usable grip-force proxy - the point is to
    watch how noisy Present_Current is against Present_Load, and whether a
    servo-side Torque_Limit holds steadier than chasing a current threshold
    from the control loop. CSV logging is here because that judgement really
    wants a plot, not a flickering table.

    Table and Graph are separate tabs rather than both always on screen - a
    live chart repainting has more visual weight than a row of numbers, and
    most of the time only one or the other is what's actually being watched."""

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
        table_tab = QWidget()
        table_tab_layout = QVBoxLayout(table_tab)
        table_tab_layout.setContentsMargins(0, 6, 0, 0)
        table_tab_layout.addWidget(self.table)

        (
            self.chart, self.chart_series, self.computed_series,
            self.chart_x_axis, self.chart_y_axis,
        ) = self._build_chart()
        chart_view = QChartView(self.chart)
        chart_view.setRenderHint(QPainter.Antialiasing)
        chart_view.setMinimumHeight(self.table.minimumHeight())
        graph_tab = QWidget()
        graph_tab_layout = QVBoxLayout(graph_tab)
        graph_tab_layout.setContentsMargins(0, 6, 0, 0)
        graph_tab_layout.addWidget(chart_view)

        self.tabs = QTabWidget()
        self.tabs.addTab(table_tab, "Table")
        self.tabs.addTab(graph_tab, "Graph")

        self.caption = QLabel(
            "~10 Hz. Volt/Temp/Current/Load are cross-checked conversions "
            "(see _convert() for sourcing); Vel (deg/s*) is a derived estimate, "
            "not a confirmed unit. Watching velocity overlays a second "
            "'computed' trace, numerically differentiated from Present_Position "
            "(already confirmed) over the same interval - if it tracks the "
            "reported trace, the derived formula is confirmed on THIS hardware, "
            "not just assumed from documentation. Pos is left as raw encoder "
            "ticks to avoid a second, differently-zeroed 'degrees' next to "
            "Joint Control's calibrated one."
        )
        self.caption.setObjectName("sectionCaption")
        self.caption.setWordWrap(True)

        # -- noise tracker for one joint (the whole reason this panel exists) --
        # drives BOTH the stats line below and the Graph tab - one signal
        # picked at a time, so the two views never disagree about what
        # they're showing.
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
        layout.addWidget(self.tabs)
        layout.addWidget(self.caption)
        layout.addLayout(watch_row)
        layout.addLayout(log_row)
        layout.addLayout(settings_row)

        # (elapsed_seconds, value) pairs for the currently watched signal -
        # backs both the stats line and the chart, so they can never disagree
        self._samples: deque = deque(maxlen=STATS_WINDOW)
        # same, but for the numerically-differentiated cross-check - only
        # populated while watching "velocity"
        self._computed_samples: deque = deque(maxlen=STATS_WINDOW)
        self._series_start: float | None = None
        # (wall_clock_time, raw_position_ticks) from the previous sample of
        # whichever joint is currently watched - cleared on every watch
        # change/reset, so it never straddles a switch to a different joint
        self._prev_position: tuple[float, int] | None = None

    # ---------------------------------------------------------------- chart setup
    def _build_chart(self):
        """A QLineSeries styled to match this app's dark theme (ui/style.py) -
        QtCharts isn't reachable through QSS, so its colors are set directly
        via the Qt Charts API instead."""
        border = QColor(COLORS["border"])
        muted = QColor(COLORS["text_muted"])

        chart = QChart()
        chart.legend().setLabelColor(muted)
        chart.legend().hide()  # shown only while watching velocity - see _refresh_chart
        chart.setBackgroundBrush(QColor(COLORS["panel"]))
        chart.setBackgroundPen(QPen(border))
        chart.setTitleBrush(muted)
        chart.setMargins(QMargins(6, 6, 6, 6))

        series = QLineSeries()
        series.setName("reported")
        pen = QPen(QColor(COLORS["accent"]))
        pen.setWidthF(1.8)
        series.setPen(pen)
        chart.addSeries(series)

        # only ever populated while watching "velocity" - the numerically-
        # differentiated cross-check against Present_Position, see update_telemetry()
        computed_series = QLineSeries()
        computed_series.setName("computed")
        computed_pen = QPen(QColor(COLORS["warn"]))
        computed_pen.setWidthF(1.8)
        computed_pen.setStyle(Qt.DashLine)
        computed_series.setPen(computed_pen)
        chart.addSeries(computed_series)

        x_axis = QValueAxis()
        x_axis.setLabelFormat("%.1f")
        x_axis.setTitleText("seconds ago")
        x_axis.setTitleBrush(muted)
        x_axis.setLabelsColor(muted)
        x_axis.setGridLineColor(border)
        x_axis.setLinePen(QPen(border))
        chart.addAxis(x_axis, Qt.AlignBottom)
        series.attachAxis(x_axis)
        computed_series.attachAxis(x_axis)

        y_axis = QValueAxis()
        y_axis.setLabelsColor(muted)
        y_axis.setGridLineColor(border)
        y_axis.setLinePen(QPen(border))
        chart.addAxis(y_axis, Qt.AlignLeft)
        series.attachAxis(y_axis)
        computed_series.attachAxis(y_axis)

        return chart, series, computed_series, x_axis, y_axis

    def _refresh_chart(self) -> None:
        joint, field = self.watched()
        _, unit = _convert(field, 0)
        self.chart.setTitle(f"{joint}.{field} ({unit})" if unit else f"{joint}.{field}")

        is_velocity = field == "velocity"
        self.chart.legend().setVisible(is_velocity)

        if not self._samples:
            self.chart_series.clear()
            self.computed_series.clear()
            return

        now = self._samples[-1][0]
        points = [(t - now, v) for t, v in self._samples]  # x: seconds ago, 0 = latest
        self.chart_series.replace([QPointF(x, y) for x, y in points])
        all_values = [v for _, v in points]
        x_lo = points[0][0]

        if is_velocity and self._computed_samples:
            comp_points = [(t - now, v) for t, v in self._computed_samples]
            self.computed_series.replace([QPointF(x, y) for x, y in comp_points])
            all_values += [v for _, v in comp_points]
            x_lo = min(x_lo, comp_points[0][0])
        else:
            self.computed_series.clear()

        self.chart_x_axis.setRange(min(x_lo, -1.0), 0.0)
        y_lo, y_hi = min(all_values), max(all_values)
        pad = max(1.0, (y_hi - y_lo) * 0.15)
        self.chart_y_axis.setRange(y_lo - pad, y_hi + pad)

    # ---------------------------------------------------------------- internals
    def _apply_deadband(self) -> None:
        value = self.deadband_spin.value()
        self.register_write_requested.emit("CW_Dead_Zone", value, "gripper")
        self.register_write_requested.emit("CCW_Dead_Zone", value, "gripper")

    def _reset_stats(self) -> None:
        self._samples.clear()
        self._computed_samples.clear()
        self._series_start = None
        self._prev_position = None
        self.stats_label.setText("no samples yet")
        self._refresh_chart()

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
            self.table.item(row, 1).setText(str(values.get("position", "-")))  # raw ticks, see _convert() docstring
            for col, key in enumerate(("velocity", "load", "current", "voltage", "temperature"), start=2):
                raw = values.get(key)
                text = "-" if raw is None else f"{_convert(key, raw)[0]:.1f}"
                self.table.item(row, col).setText(text)

        joint, field = self.watched()
        joint_telemetry = telemetry.get(joint, {})
        raw = joint_telemetry.get(field)
        if raw is None:
            return
        value, unit = _convert(field, raw)

        if self._series_start is None:
            self._series_start = time.monotonic()
        sample_t = time.monotonic() - self._series_start
        self._samples.append((sample_t, value))

        computed_now = None
        if field == "velocity":
            computed_now = self._update_computed_velocity(joint_telemetry, sample_t)

        values = [v for _, v in self._samples]
        lo, hi = min(values), max(values)
        mean = sum(values) / len(values)
        stats_text = (
            f"{joint}.{field} ({unit})  now {value:.1f}   min {lo:.1f}   max {hi:.1f}   "
            f"spread {hi - lo:.1f}   mean {mean:.1f}   n={len(values)}"
        )
        if field == "velocity" and self._computed_samples:
            comp_values = [v for _, v in self._computed_samples]
            comp_mean = sum(comp_values) / len(comp_values)
            ratio = f"{value / computed_now:.2f}" if computed_now else "-"
            stats_text += (
                f"   |   computed now {computed_now:.1f} deg/s"
                if computed_now is not None else "   |   computed: waiting for 2nd sample"
            )
            stats_text += f"   mean {comp_mean:.1f}   ratio(reported/computed) {ratio}"
        self.stats_label.setText(stats_text)
        self._refresh_chart()

    def _update_computed_velocity(self, joint_telemetry: dict, sample_t: float) -> float | None:
        """Numerically differentiates the SAME joint's Present_Position (raw
        ticks, already-confirmed 360/4096 resolution) between this reading and
        the previous one - an independent, from-first-principles cross-check
        of the reported Present_Velocity value, using no assumption about
        Present_Velocity's own units at all."""
        pos_raw = joint_telemetry.get("position")
        if pos_raw is None:
            return None
        now_wall = time.monotonic()
        computed = None
        if self._prev_position is not None:
            prev_wall, prev_pos = self._prev_position
            dt = now_wall - prev_wall
            if dt > 0:
                computed = _ticks_delta(pos_raw, prev_pos) * DEG_PER_TICK / dt
                self._computed_samples.append((sample_t, computed))
        self._prev_position = (now_wall, pos_raw)
        return computed
