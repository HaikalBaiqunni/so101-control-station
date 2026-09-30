from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCheckBox

from .style import COLORS


class ToggleSwitch(QCheckBox):
    """A QCheckBox drawn as a label with a sliding switch on the right.

    It IS a QCheckBox (isChecked / setChecked / toggled / setToolTip all work
    unchanged), only the painting differs, so it can replace a checkbox
    without touching the code that reads it."""

    TRACK_W = 36
    TRACK_H = 20

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + self.TRACK_W + 16,
                     max(self.TRACK_H + 8, metrics.height() + 8))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:   # the whole row is clickable, not just the switch
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        checked = self.isChecked()

        painter.setPen(QColor(COLORS["text"] if enabled else COLORS["text_muted"]))
        text_rect = QRectF(0, 0, self.width() - self.TRACK_W - 10, self.height())
        painter.drawText(text_rect, int(Qt.AlignVCenter | Qt.AlignLeft), self.text())

        x = self.width() - self.TRACK_W - 1
        y = (self.height() - self.TRACK_H) / 2
        track = QRectF(x, y, self.TRACK_W, self.TRACK_H)
        if checked:
            track_color = QColor(COLORS["accent"] if enabled else COLORS["border"])
        else:
            track_color = QColor(COLORS["border"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)

        knob = self.TRACK_H - 6
        knob_x = track.right() - 3 - knob if checked else track.x() + 3
        painter.setBrush(QColor("#ffffff" if checked else COLORS["text_muted"]))
        painter.drawEllipse(QRectF(knob_x, y + 3, knob, knob))
