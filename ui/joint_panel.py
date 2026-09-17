from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QSlider,
    QWidget,
)

from core.servo_bus import JOINT_ORDER

SLIDER_SCALE = 10  # slider works in int ticks of 0.1 deg for smoothness


class JointRow(QWidget):
    """One joint: name, slider, live degree readout / manual entry."""

    goal_changed = Signal(str, float)  # (joint_name, degrees) - user-initiated only

    def __init__(self, name: str, limits: tuple[float, float] = (-180.0, 180.0), parent=None):
        super().__init__(parent)
        self.name = name
        self._suppress_feedback = False

        lo, hi = limits
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(int(lo * SLIDER_SCALE))
        self.slider.setMaximum(int(hi * SLIDER_SCALE))
        self.slider.setValue(0)

        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(1)
        self.spin.setRange(lo, hi)
        self.spin.setSuffix(" deg")

        self.slider.valueChanged.connect(self._on_slider_moved)
        self.spin.valueChanged.connect(self._on_spin_edited)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.addWidget(QLabel(name.replace("_", " ").title()), 0, 0)
        layout.addWidget(self.slider, 0, 1)
        layout.addWidget(self.spin, 0, 2)
        layout.setColumnStretch(1, 1)

    def set_limits(self, lo: float, hi: float) -> None:
        self.slider.setMinimum(int(lo * SLIDER_SCALE))
        self.slider.setMaximum(int(hi * SLIDER_SCALE))
        self.spin.setRange(lo, hi)

    def set_feedback_deg(self, degrees: float) -> None:
        """Update the displayed value from live hardware feedback, WITHOUT
        re-emitting goal_changed (that would create a write-loop)."""
        self._suppress_feedback = True
        self.slider.setValue(int(degrees * SLIDER_SCALE))
        self.spin.setValue(degrees)
        self._suppress_feedback = False

    def _on_slider_moved(self, ticks: int) -> None:
        degrees = ticks / SLIDER_SCALE
        if not self._suppress_feedback:
            self.spin.blockSignals(True)
            self.spin.setValue(degrees)
            self.spin.blockSignals(False)
            self.goal_changed.emit(self.name, degrees)

    def _on_spin_edited(self, degrees: float) -> None:
        if not self._suppress_feedback:
            self.slider.blockSignals(True)
            self.slider.setValue(int(degrees * SLIDER_SCALE))
            self.slider.blockSignals(False)
            self.goal_changed.emit(self.name, degrees)


class JointPanel(QGroupBox):
    goal_changed = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__("JOINT CONTROL", parent)
        self.rows: dict[str, JointRow] = {}
        self._layout = QGridLayout(self)
        self.rebuild(JOINT_ORDER)

    def rebuild(self, joint_names: list[str], limits: dict[str, tuple[float, float]] | None = None) -> None:
        """Replace every row with a fresh set for `joint_names` - used when
        switching robot profile (see MainWindow's robot selector), since a
        different robot has different joints entirely, not just different
        limits on the same six SO-101 names. The constructor calls this once
        with JOINT_ORDER, so the default panel is unaffected by this existing
        at all - only an explicit later call with a different list changes
        anything."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # takeAt() only stops the LAYOUT from positioning this widget -
                # the widget itself stays visible at its last geometry, still
                # a child of this panel, until it's actually destroyed.
                # deleteLater() alone left exactly that stale-visible window
                # open (confirmed on a real run: the old and new rows
                # rendered overlapping each other) since deferred deletion
                # doesn't necessarily run before the next paint. hide() makes
                # it stop rendering immediately; deleteLater() still does the
                # actual cleanup once the event loop gets to it.
                widget.hide()
                widget.deleteLater()
        self.rows = {}
        limits = limits or {}
        for i, name in enumerate(joint_names):
            row = JointRow(name, limits.get(name, (-180.0, 180.0)))
            row.goal_changed.connect(self.goal_changed)
            self.rows[name] = row
            self._layout.addWidget(row, i, 0)

    def set_limits(self, name: str, lo: float, hi: float) -> None:
        if name in self.rows:
            self.rows[name].set_limits(lo, hi)

    def update_feedback(self, positions: dict[str, float]) -> None:
        for name, deg in positions.items():
            if name in self.rows:
                self.rows[name].set_feedback_deg(deg)
