"""Commanded-vs-measured chart for one joint, plus the step-response numbers.

Purely a display: it is fed (goal, measured) samples by MainWindow and never
sends anything to the robot."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .style import COLORS

WINDOW_S = 10.0
GOAL_COLOR = "#4cc2ff"
MEASURED_COLOR = "#e0824a"
STEP_MIN_DEG = 2.0   # a goal change at least this big counts as a step


def tracking_metrics(samples: list[tuple[float, float, float]]) -> dict:
    """samples: (t seconds, goal deg, measured deg), oldest first.

    peak_error  worst |goal - measured| in the window (deg)
    settle_s    time from the latest step until the joint stays inside the band
                (max(0.5 deg, 5 % of the step)); None if it has not settled yet
    overshoot   how far past the goal it went, in % of the step; None without a step
    """
    result = {"peak_error": None, "settle_s": None, "overshoot_pct": None, "step_deg": None}
    if not samples:
        return result
    result["peak_error"] = max(abs(g - m) for _t, g, m in samples)

    step_index = None
    for i in range(len(samples) - 1, 0, -1):
        if abs(samples[i][1] - samples[i - 1][1]) >= STEP_MIN_DEG:
            step_index = i
            break
    if step_index is None:
        return result

    t0, target, _ = samples[step_index]
    step = target - samples[step_index - 1][1]
    result["step_deg"] = step
    after = samples[step_index:]
    band = max(0.5, 0.05 * abs(step))
    sign = 1.0 if step > 0 else -1.0
    result["overshoot_pct"] = max(0.0, max(sign * (m - target) for _t, _g, m in after)) / abs(step) * 100.0

    # settled = the first sample from which every later sample stays inside the band
    settle_at = None
    for j in range(len(after) - 1, -1, -1):
        if abs(after[j][2] - target) > band:
            break
        settle_at = j
    if settle_at is not None and settle_at < len(after) - 1:   # at least two trailing samples inside the band
        result["settle_s"] = after[settle_at][0] - t0
    return result


class TrackingChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self._samples: deque[tuple[float, float, float]] = deque()

    def add_sample(self, t: float, goal: float, measured: float) -> None:
        self._samples.append((t, goal, measured))
        while self._samples and t - self._samples[0][0] > WINDOW_S:
            self._samples.popleft()
        self.update()

    def clear(self) -> None:
        self._samples.clear()
        self.update()

    def samples(self) -> list[tuple[float, float, float]]:
        return list(self._samples)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(COLORS["bg"]))
        if len(self._samples) < 2:
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "no data yet - connect, torque ON, then move the joint")
            return
        data = list(self._samples)
        t_end = data[-1][0]
        values = [v for _t, g, m in data for v in (g, m)]
        lo, hi = min(values), max(values)
        pad = max(2.0, (hi - lo) * 0.15)
        lo, hi = lo - pad, hi + pad
        w, h = self.width() - 8, self.height() - 8

        def point(t: float, v: float) -> QPointF:
            return QPointF(4 + (t - (t_end - WINDOW_S)) / WINDOW_S * w, 4 + (hi - v) / (hi - lo) * h)

        grid = QPen(QColor(COLORS["border"]), 1)
        painter.setPen(grid)
        for k in range(1, 4):
            y = 4 + h * k / 4
            painter.drawLine(QPointF(4, y), QPointF(4 + w, y))

        for index, color, style in ((1, GOAL_COLOR, Qt.DashLine), (2, MEASURED_COLOR, Qt.SolidLine)):
            pen = QPen(QColor(color), 2, style)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            for a, b in zip(data, data[1:], strict=False):
                painter.drawLine(point(a[0], a[index]), point(b[0], b[index]))

        painter.setPen(QColor(COLORS["text_muted"]))
        painter.drawText(8, 16, f"{hi:.0f}")
        painter.drawText(8, self.height() - 8, f"{lo:.0f}")
