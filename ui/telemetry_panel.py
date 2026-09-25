from __future__ import annotations

import time
from collections import deque

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.servo_bus import JOINT_ORDER, decode_sign_magnitude

from .style import COLORS, JOINT_LINE_COLORS

GRAPH_FIELDS = ["current", "load", "velocity", "voltage", "temperature"]
# "torque" isn't a separate quantity on a Feetech STS3215 - Present_Load
# already IS the servo's own % of its rated torque output, so it's the one
# already in GRAPH_FIELDS above rather than a second, redundant entry.
CHART_MIN_HEIGHT = 170  # a trend line reads fine well short of the table's own height

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
#
# Public rather than private because the CSV writer in ui/main_window.py logs
# the converted value alongside the raw one, and a log whose numbers disagree
# with the table they were read off would be worse than no log at all.
DM_UNITS = {"position": "deg", "velocity": "deg/s", "load": "N*m"}


def convert_telemetry(field: str, raw: int, profile_key: str = "so101") -> tuple[float, str]:
    if profile_key == "rebot_b601_dm":
        # DamiaoBus.read_telemetry() (core/damiao_bus.py) already returns
        # final physical values (degrees, deg/s, N*m off the motor's own
        # torque estimate) - there's no Feetech-style raw-register decoding
        # to apply here, this is a completely different motor family's
        # protocol. It also doesn't report voltage/temperature/current at
        # all (confirmed directly: dm_can.py has no RID or request frame for
        # either) - those fields simply won't be present in the dict this is
        # called with, handled upstream by update_telemetry's `raw is None`
        # checks rather than by anything in here.
        return float(raw), DM_UNITS.get(field, "")
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
    most of the time only one or the other is what's actually being watched.

    Takes `joint_order` so a different robot profile's joints replace
    SO-101's six (see MainWindow._on_robot_profile_changed, which calls
    rebuild() the same way it already does for JointPanel) rather than this
    panel staying hardcoded to one arm forever."""

    log_toggled = Signal(bool)
    register_write_requested = Signal(str, int, str)  # data_name, value, joint ("" = all)

    def __init__(self, joint_order: tuple[str, ...] | None = None, parent=None):
        super().__init__("SERVO TELEMETRY", parent)
        self.joint_order: tuple[str, ...] = tuple(joint_order) if joint_order is not None else tuple(JOINT_ORDER)
        self.profile_key: str = "so101"

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        # Stretch (not resizeColumnsToContents) so the 7 columns always sum to
        # exactly the panel's width - a fixed content width would trigger a
        # horizontal scrollbar whenever the panel is narrower than that, and
        # that scrollbar eats into the SAME fixed table height set below,
        # silently pushing the last row (gripper) out of view without ever
        # showing a vertical scrollbar to hint why.
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Row height used to come from either a guessed constant
        # (verticalHeader().defaultSectionSize(), a QStyle-derived metric -
        # 30px in an offscreen test) or a measurement
        # (resizeRowsToContents() - 17px in that SAME offscreen test, i.e.
        # even those two disagreed with each other there). Confirmed on real
        # hardware: gripper's row was STILL missing after both attempts, on
        # a panel with visibly plenty of spare room around it - meaning the
        # actual failure wasn't "not enough total space" at all, it was
        # "row height assumptions don't hold on whatever style/DPI this
        # machine renders with". Fixed mode + an explicit size stops asking
        # Qt to predict or report a row height and just DICTATES one instead
        # - every row is exactly ROW_H regardless of style, font, or DPI.
        self._row_h = 26
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(self._row_h)

        table_tab = QWidget()
        table_tab_layout = QVBoxLayout(table_tab)
        table_tab_layout.setContentsMargins(0, 6, 0, 0)
        table_tab_layout.addWidget(self.table)

        self.chart, self.chart_x_axis, self.chart_y_axis = self._build_chart_shell()
        self.chart_series: dict[str, QLineSeries] = {}  # joint -> its line - see rebuild()
        chart_view = QChartView(self.chart)
        chart_view.setRenderHint(QPainter.Antialiasing)
        # NOT tied to the table's height (it used to be) - a trend line reads
        # fine well short of six fixed-height table rows, and forcing it that
        # tall was inflating this whole panel's minimum footprint, squeezing
        # the Digital Twin/Camera row above it in the process.
        chart_view.setMinimumHeight(CHART_MIN_HEIGHT)
        graph_tab = QWidget()
        graph_tab_layout = QVBoxLayout(graph_tab)
        graph_tab_layout.setContentsMargins(0, 6, 0, 0)
        graph_tab_layout.addWidget(chart_view)

        self.tabs = QTabWidget()
        self.tabs.addTab(table_tab, "Table")
        self.tabs.addTab(graph_tab, "Graph")

        # A single-line, elided caption with the full explanation in a
        # tooltip - the same idiom TwinPanel's caption already uses. The
        # unabridged text used to sit here word-wrapped across several lines,
        # which (like the graph's height above) was inflating this panel's
        # forced minimum height for no real benefit - the explanation is
        # still one hover away, just not permanently taking up several lines
        # of vertical space.
        self._caption_text = (
            "~10 Hz. Volt/Temp/Current/Load are cross-checked conversions "
            "(see convert_telemetry() for sourcing); Vel (deg/s*) is a derived estimate, "
            "not a confirmed unit. Watching velocity overlays a second "
            "'computed' trace in the stats line below, numerically differentiated from "
            "Present_Position (already confirmed) over the same interval - if it tracks "
            "the reported value, the derived formula is confirmed on THIS hardware, not "
            "just assumed from documentation. Pos is left as raw encoder ticks to avoid a "
            "second, differently-zeroed 'degrees' next to Joint Control's calibrated one."
        )
        self.caption = QLabel()
        self.caption.setObjectName("sectionCaption")
        self.caption.setToolTip(self._caption_text)
        self.caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        # -- noise tracker for one joint (the whole reason this panel exists) --
        # drives the stats line below. The Graph tab now shows every joint at
        # once for whichever field is picked here - this combo genuinely
        # selects the field for BOTH; only the stats line still needs a
        # single joint singled out.
        self.watch_combo = QComboBox()
        self.watch_combo.currentTextChanged.connect(lambda _: self._reset_stats())

        self.watch_field = QComboBox()
        self.watch_field.addItems(GRAPH_FIELDS)
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
        self.torque_target_combo = QComboBox()

        self.torque_limit_spin = QSpinBox()
        self.torque_limit_spin.setRange(0, 1000)
        self.torque_limit_spin.setValue(500)
        self.torque_limit_spin.setToolTip(
            "Torque_Limit (SRAM, reg 48). Caps the servo's own output, so the\n"
            "servo holds a roughly constant force in its internal loop instead\n"
            "of us watching Present_Current at 60 Hz and reacting ~16 ms late.\n"
            "A joint that stalls under an added payload during smooth/eased\n"
            "playback (small commanded position error near the start of a\n"
            "move, not enough torque yet to break static friction + gravity)\n"
            "usually just needs more headroom here."
        )
        self.torque_limit_btn = QPushButton("Apply")
        self.torque_limit_btn.clicked.connect(
            lambda: self.register_write_requested.emit(
                "Torque_Limit", self.torque_limit_spin.value(), self.torque_target_combo.currentText()
            )
        )

        self.deadband_spin = QSpinBox()
        self.deadband_spin.setRange(0, 32)
        self.deadband_spin.setValue(1)
        self.deadband_spin.setToolTip(
            "CW/CCW_Dead_Zone (EEPROM, regs 26/27). Inside this band the servo\n"
            "stops correcting, so it sets a hard floor on position\n"
            "repeatability. Smaller is tighter but can cause hunting/buzzing."
        )
        self.deadband_btn = QPushButton("Apply")
        self.deadband_btn.clicked.connect(self._apply_deadband)

        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Target"))
        settings_row.addWidget(self.torque_target_combo)
        settings_row.addSpacing(8)
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
        # one deque per joint, for the Graph tab's all-joints-at-once view -
        # separate from _samples above (that one is always the single
        # watch_combo joint; this is every joint, for whatever field
        # watch_field currently says)
        self._graph_samples: dict[str, deque] = {}
        self._series_start: float | None = None
        # (wall_clock_time, raw_position_ticks) from the previous sample of
        # whichever joint is currently watched - cleared on every watch
        # change/reset, so it never straddles a switch to a different joint
        self._prev_position: tuple[float, int] | None = None

        self.rebuild(self.joint_order)

    # ---------------------------------------------------------------- rebuild for a different robot
    def rebuild(self, joint_order: tuple[str, ...], profile_key: str = "so101") -> None:
        """Replace every joint-dependent row/series/combo entry for
        `joint_order` - used when switching robot profile (see JointPanel's
        own rebuild, which this mirrors). Also called once from __init__ so
        the constructor and a later profile switch share one code path.

        `profile_key` picks which unit/scaling convention convert_telemetry()
        applies - Feetech's raw-register decoding doesn't mean anything for a
        completely different motor family's protocol (see that function's
        own docstring)."""
        self.joint_order = tuple(joint_order)
        self.profile_key = profile_key

        self.table.setRowCount(len(self.joint_order))
        for row, name in enumerate(self.joint_order):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            for col in range(1, len(COLUMNS)):
                item = QTableWidgetItem("-")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)
        # See the ROW_H comment above for why this is dictated, not measured.
        # Confirmed on real hardware: without a matching MINIMUM on the panel
        # itself (not just the table), a squeezed QSplitter could still
        # compress this whole box below what its children need and the last
        # row (gripper) would silently lose its space with no scrollbar to
        # hint why - locking minimumSize to minimumSizeHint turns "always
        # show every row" into a hard floor instead of a hint the splitter
        # is free to ignore under pressure.
        self.table.setMinimumHeight(
            self.table.horizontalHeader().height()
            + len(self.joint_order) * self._row_h
            + 2 * self.table.frameWidth()
            + 20  # generous slack, not a tight fit - see the ROW_H comment above
        )

        for series in self.chart_series.values():
            self.chart.removeSeries(series)
        self.chart_series = {}
        for i, name in enumerate(self.joint_order):
            series = QLineSeries()
            series.setName(name)
            pen = QPen(QColor(JOINT_LINE_COLORS[i % len(JOINT_LINE_COLORS)]))
            pen.setWidthF(1.8)
            series.setPen(pen)
            self.chart.addSeries(series)
            series.attachAxis(self.chart_x_axis)
            series.attachAxis(self.chart_y_axis)
            self.chart_series[name] = series

        for combo in (self.watch_combo, self.torque_target_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(self.joint_order)
            # "gripper" (SO-101) is the joint this panel's own docstring is
            # about (grip-force noise) - preselect it as a convenience where
            # it exists; a robot with no such name just keeps combo index 0.
            if "gripper" in self.joint_order:
                combo.setCurrentText("gripper")
            combo.blockSignals(False)

        self._elide_caption()
        self.setMinimumHeight(self.minimumSizeHint().height())
        self._reset_stats()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide_caption()

    def _elide_caption(self) -> None:
        width = max(0, self.caption.width() - 2)
        metrics = QFontMetrics(self.caption.font())
        self.caption.setText(metrics.elidedText(self._caption_text, Qt.ElideRight, width))

    # ---------------------------------------------------------------- chart setup
    def _build_chart_shell(self):
        """The chart itself, axes and styling only - no data series yet
        (those are per-joint and built in rebuild(), since the joint list
        can change). Styled to match this app's dark theme (ui/style.py) -
        QtCharts isn't reachable through QSS, so its colors are set directly
        via the Qt Charts API instead."""
        border = QColor(COLORS["border"])
        muted = QColor(COLORS["text_muted"])

        chart = QChart()
        chart.legend().setLabelColor(muted)
        chart.legend().setVisible(True)  # one color per joint now - always needs the legend to read
        chart.setBackgroundBrush(QColor(COLORS["panel"]))
        chart.setBackgroundPen(QPen(border))
        chart.setTitleBrush(muted)
        chart.setMargins(QMargins(6, 6, 6, 6))

        x_axis = QValueAxis()
        x_axis.setLabelFormat("%.1f")
        x_axis.setTitleText("seconds ago")
        x_axis.setTitleBrush(muted)
        x_axis.setLabelsColor(muted)
        x_axis.setGridLineColor(border)
        x_axis.setLinePen(QPen(border))
        chart.addAxis(x_axis, Qt.AlignBottom)

        y_axis = QValueAxis()
        y_axis.setLabelsColor(muted)
        y_axis.setGridLineColor(border)
        y_axis.setLinePen(QPen(border))
        chart.addAxis(y_axis, Qt.AlignLeft)

        return chart, x_axis, y_axis

    def _refresh_chart(self) -> None:
        _, field = self.watched()
        _, unit = convert_telemetry(field, 0, self.profile_key)
        self.chart.setTitle(f"all joints · {field} ({unit})" if unit else f"all joints · {field}")

        all_values = []
        x_lo = 0.0
        any_samples = False
        for name, series in self.chart_series.items():
            samples = self._graph_samples.get(name)
            if not samples:
                series.clear()
                continue
            any_samples = True
            now = samples[-1][0]
            points = [(t - now, v) for t, v in samples]  # x: seconds ago, 0 = latest
            series.replace([QPointF(x, y) for x, y in points])
            all_values.extend(v for _, v in points)
            x_lo = min(x_lo, points[0][0])

        if not any_samples:
            self.chart_x_axis.setRange(-1.0, 0.0)
            self.chart_y_axis.setRange(-1.0, 1.0)
            return

        self.chart_x_axis.setRange(min(x_lo, -1.0), 0.0)
        y_lo, y_hi = min(all_values), max(all_values)
        pad = max(1.0, (y_hi - y_lo) * 0.15)
        self.chart_y_axis.setRange(y_lo - pad, y_hi + pad)

    # ---------------------------------------------------------------- internals
    def _apply_deadband(self) -> None:
        value = self.deadband_spin.value()
        target = self.torque_target_combo.currentText()
        self.register_write_requested.emit("CW_Dead_Zone", value, target)
        self.register_write_requested.emit("CCW_Dead_Zone", value, target)

    def _reset_stats(self) -> None:
        self._samples.clear()
        self._computed_samples.clear()
        self._graph_samples = {name: deque(maxlen=STATS_WINDOW) for name in self.joint_order}
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
        for row, name in enumerate(self.joint_order):
            values = telemetry.get(name)
            if not values:
                continue
            # "Pos (ticks)" header is literally correct for Feetech (raw
            # encoder ticks, see convert_telemetry()'s docstring on why it
            # stays unconverted); for reBot B601-DM this is actually already
            # degrees (DamiaoBus.read_telemetry() has no separate "ticks"
            # concept) - a known cosmetic mismatch, not a wrong VALUE, not
            # worth a per-profile column header for one number.
            pos_raw = values.get("position", "-")
            text = f"{pos_raw:.1f}" if isinstance(pos_raw, float) else str(pos_raw)
            self.table.item(row, 1).setText(text)
            for col, key in enumerate(("velocity", "load", "current", "voltage", "temperature"), start=2):
                raw = values.get(key)
                text = "-" if raw is None else f"{convert_telemetry(key, raw, self.profile_key)[0]:.1f}"
                self.table.item(row, col).setText(text)

        joint, field = self.watched()
        if self._series_start is None:
            self._series_start = time.monotonic()
        sample_t = time.monotonic() - self._series_start

        # Every joint's own sample for the Graph tab's all-joints overlay -
        # independent of which single joint the stats line below is watching.
        for name in self.joint_order:
            joint_values = telemetry.get(name)
            if not joint_values:
                continue
            raw = joint_values.get(field)
            if raw is None:
                continue
            value, _ = convert_telemetry(field, raw, self.profile_key)
            self._graph_samples.setdefault(name, deque(maxlen=STATS_WINDOW)).append((sample_t, value))
        self._refresh_chart()

        joint_telemetry = telemetry.get(joint, {})
        raw = joint_telemetry.get(field)
        if raw is None:
            return
        value, unit = convert_telemetry(field, raw, self.profile_key)
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

    def _update_computed_velocity(self, joint_telemetry: dict, sample_t: float) -> float | None:
        """Numerically differentiates the SAME joint's Present_Position (raw
        ticks, already-confirmed 360/4096 resolution) between this reading and
        the previous one - an independent, from-first-principles cross-check
        of the reported Present_Velocity value, using no assumption about
        Present_Velocity's own units at all. Text-only (in the stats line) -
        deliberately not a second line on the shared Graph tab, since that
        chart shows one line per JOINT for one field; doubling every joint's
        line for this one diagnostic would just make it noisy."""
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
