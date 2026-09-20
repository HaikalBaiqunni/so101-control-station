from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from core.servo_bus import JOINT_ORDER


class JogButton(QPushButton):
    """A press-and-hold jog button: it emits pressed() when the mouse goes down
    on it and released() when it comes up - and, importantly, ALSO when the
    cursor is dragged off the button while still held (Qt's own behaviour for
    QAbstractButton), so sliding off a button stops the motion instead of
    leaving it running with no way to un-press it.

    Never takes keyboard focus: clicking a jog button must not steal focus from
    the main window, which is where the keyboard-jog key handling lives."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("jogButton")
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoRepeat(False)
        self.setFixedSize(36, 28)


class JointRow(QWidget):
    """One joint: name, a held [-] / [+] pair that jogs it, and a live degree
    readout that doubles as exact entry (type a value, press Enter)."""

    goal_changed = Signal(str, float)  # (joint_name, degrees) - typed entry only
    jog_pressed = Signal(str, int)     # (joint_name, +1 / -1)
    jog_released = Signal(str, int)

    def __init__(self, name: str, limits: tuple[float, float] = (-180.0, 180.0), parent=None):
        super().__init__(parent)
        self.name = name
        self._suppress_feedback = False

        lo, hi = limits
        self.minus_btn = JogButton("−")
        self.plus_btn = JogButton("+")
        self.minus_btn.pressed.connect(lambda: self.jog_pressed.emit(self.name, -1))
        self.minus_btn.released.connect(lambda: self.jog_released.emit(self.name, -1))
        self.plus_btn.pressed.connect(lambda: self.jog_pressed.emit(self.name, 1))
        self.plus_btn.released.connect(lambda: self.jog_released.emit(self.name, 1))

        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(1)
        self.spin.setRange(lo, hi)
        self.spin.setSuffix(" deg")
        self.spin.setAlignment(Qt.AlignRight)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        # Without this every keystroke fires valueChanged: typing "120" would
        # command the arm to 1, then 12, then 120 on the way there. Only
        # Enter / focus-out should be an instruction to move.
        self.spin.setKeyboardTracking(False)
        self.spin.valueChanged.connect(self._on_spin_edited)
        self._update_tooltip(lo, hi)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        label = QLabel(name.replace("_", " ").title())
        label.setMinimumWidth(84)
        layout.addWidget(label, 0, 0)
        layout.addWidget(self.minus_btn, 0, 1)
        layout.addWidget(self.spin, 0, 2)
        layout.addWidget(self.plus_btn, 0, 3)
        layout.setColumnStretch(2, 1)

    def _update_tooltip(self, lo: float, hi: float) -> None:
        self.spin.setToolTip(f"Range {lo:.1f} to {hi:.1f} deg. Type a value and press Enter to move there.")

    def set_limits(self, lo: float, hi: float) -> None:
        self.spin.setRange(lo, hi)
        self._update_tooltip(lo, hi)

    def set_input_enabled(self, enabled: bool) -> None:
        for widget in (self.minus_btn, self.plus_btn, self.spin):
            widget.setEnabled(enabled)

    def set_feedback_deg(self, degrees: float) -> None:
        """Update the displayed value from live hardware feedback, WITHOUT
        re-emitting goal_changed (that would create a write-loop)."""
        # Never overwrite a value somebody is halfway through typing - feedback
        # arrives ~30 times a second and would erase it under their fingers.
        line_edit = self.spin.lineEdit()
        if line_edit is not None and line_edit.hasFocus():
            return
        self._suppress_feedback = True
        self.spin.setValue(degrees)
        self._suppress_feedback = False

    def _on_spin_edited(self, degrees: float) -> None:
        if not self._suppress_feedback:
            self.goal_changed.emit(self.name, degrees)


class JointPanel(QWidget):
    """The joint-space jog page: a row per joint. Lives inside the JogPanel
    (ui/jog_panel.py), next to the Cartesian page."""

    goal_changed = Signal(str, float)
    jog_pressed = Signal(str, int)
    jog_released = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: dict[str, JointRow] = {}
        self._input_enabled = True
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
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
            row.jog_pressed.connect(self.jog_pressed)
            row.jog_released.connect(self.jog_released)
            row.set_input_enabled(self._input_enabled)
            self.rows[name] = row
            self._layout.addWidget(row, i, 0)

    def set_input_enabled(self, enabled: bool) -> None:
        self._input_enabled = enabled
        for row in self.rows.values():
            row.set_input_enabled(enabled)

    def set_limits(self, name: str, lo: float, hi: float) -> None:
        if name in self.rows:
            self.rows[name].set_limits(lo, hi)

    def update_feedback(self, positions: dict[str, float]) -> None:
        for name, deg in positions.items():
            if name in self.rows:
                self.rows[name].set_feedback_deg(deg)
