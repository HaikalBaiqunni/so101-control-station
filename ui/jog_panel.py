from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.kinematics import AXES

from .joint_panel import JogButton, JointPanel

MODES = ("joint", "world", "tool")
MODE_LABELS = {"joint": "Joint", "world": "World", "tool": "Tool"}

# (label, size). 0 = continuous: the arm moves for as long as the button is held.
# A size means one click = one fixed move of that many degrees (or millimetres
# for a translation), at the panel's current speed.
STEP_CHOICES = (("Continuous", 0.0), ("0.5", 0.5), ("1", 1.0), ("5", 5.0), ("10", 10.0))

DEFAULT_SPEED_PERCENT = 30  # slow on purpose - the first press should never be a surprise

_AXIS_LABELS = {"x": "X", "y": "Y", "z": "Z", "rx": "Rx", "ry": "Ry", "rz": "Rz"}
_AXIS_UNITS = {"x": "mm", "y": "mm", "z": "mm", "rx": "deg", "ry": "deg", "rz": "deg"}

# An axis whose reachability falls below this is drawn as "limited": pressing it
# still does the best the arm can, but it won't be a clean move along that axis.
LIMITED_BELOW = 0.6

_FRAME_HINTS = {
    "joint": "Each button drives one joint directly.",
    "world": "WORLD frame: X/Y/Z and Rx/Ry/Rz are fixed to the robot base (axis triad at the base).",
    "tool": "TOOL frame: the same six directions, but measured from the gripper and turning with it "
            "(axis triad at the tool tip). +Z is along the approach axis.",
}


def _set_property(widget: QWidget, name: str, value) -> None:
    """Change a dynamic property AND make Qt re-evaluate the stylesheet for it -
    without the unpolish/polish a [property="..."] QSS selector never notices."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class CartesianRow(QWidget):
    jog_pressed = Signal(str, int)     # (axis, +1 / -1)
    jog_released = Signal(str, int)

    def __init__(self, axis: str, parent=None):
        super().__init__(parent)
        self.axis = axis
        self.minus_btn = JogButton("−")
        self.plus_btn = JogButton("+")
        self.minus_btn.pressed.connect(lambda: self.jog_pressed.emit(self.axis, -1))
        self.minus_btn.released.connect(lambda: self.jog_released.emit(self.axis, -1))
        self.plus_btn.pressed.connect(lambda: self.jog_pressed.emit(self.axis, 1))
        self.plus_btn.released.connect(lambda: self.jog_released.emit(self.axis, 1))

        self.readout = QLabel("-")
        self.readout.setObjectName("poseReadout")
        self.readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        label = QLabel(_AXIS_LABELS[axis])
        label.setMinimumWidth(30)
        layout.addWidget(label, 0, 0)
        layout.addWidget(self.minus_btn, 0, 1)
        layout.addWidget(self.readout, 0, 2)
        layout.addWidget(self.plus_btn, 0, 3)
        layout.setColumnStretch(2, 1)

    def set_value(self, value: float) -> None:
        self.readout.setText(f"{value:+9.1f} {_AXIS_UNITS[self.axis]}")

    def set_reach(self, reach: float) -> None:
        limited = reach < LIMITED_BELOW
        for button in (self.minus_btn, self.plus_btn):
            _set_property(button, "limited", limited)
            button.setToolTip(
                f"This arm can only produce {reach * 100:.0f}% of a pure {_AXIS_LABELS[self.axis]} "
                "move from its current pose - it will do what it can, but not a clean one."
                if limited else ""
            )

    def set_input_enabled(self, enabled: bool) -> None:
        self.minus_btn.setEnabled(enabled)
        self.plus_btn.setEnabled(enabled)


class CartesianPage(QWidget):
    jog_pressed = Signal(str, int)
    jog_released = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: dict[str, CartesianRow] = {}
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for i, axis in enumerate(AXES):
            row = CartesianRow(axis)
            row.jog_pressed.connect(self.jog_pressed)
            row.jog_released.connect(self.jog_released)
            self.rows[axis] = row
            layout.addWidget(row, i, 0)

    def set_pose(self, position_m, rpy_deg) -> None:
        """The TCP pose in the WORLD frame, whichever frame is being jogged in -
        a readout that silently changed meaning with the mode would make two
        screenshots of the same pose look like two different poses."""
        values = [*(1000.0 * c for c in position_m), *rpy_deg]
        for axis, value in zip(AXES, values, strict=True):
            self.rows[axis].set_value(value)

    def set_reach(self, reach) -> None:
        for axis, value in zip(AXES, reach, strict=True):
            self.rows[axis].set_reach(float(value))

    def set_input_enabled(self, enabled: bool) -> None:
        for row in self.rows.values():
            row.set_input_enabled(enabled)


class JogPanel(QGroupBox):
    """JAKA-style manual movement: pick Joint / World / Tool, then hold a
    button to move. Replaces the old per-joint sliders.

    Emits (mode, axis, direction) triples - `axis` is a joint name in Joint mode
    and one of core.kinematics.AXES otherwise - and knows nothing about robots,
    kinematics or hardware. MainWindow turns them into motion."""

    jog_pressed = Signal(str, str, int)
    jog_released = Signal(str, str, int)
    mode_changed = Signal(str)
    goal_changed = Signal(str, float)   # typed joint entry, passed straight through

    def __init__(self, joint_panel: JointPanel | None = None, parent=None):
        super().__init__("JOG", parent)
        self.joint_page = joint_panel or JointPanel()
        self.cartesian_page = CartesianPage()
        self._cartesian_available = False
        self._unavailable_note = ""   # why World/Tool are greyed out, when they are
        self._tcp_note = ""           # which point the Cartesian readout refers to
        self._mode = "joint"

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        for i, mode in enumerate(MODES):
            button = QPushButton(MODE_LABELS[mode])
            button.setObjectName("segButton")
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
            self.mode_group.addButton(button, i)
            self.mode_buttons[mode] = button
            mode_row.addWidget(button)
        self.mode_buttons["joint"].setChecked(True)
        self.mode_group.idClicked.connect(lambda i: self._set_mode(MODES[i]))

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(1, 100)
        self.speed_slider.setValue(DEFAULT_SPEED_PERCENT)
        self.speed_slider.setFocusPolicy(Qt.NoFocus)
        self.speed_label = QLabel()
        self.speed_label.setMinimumWidth(44)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        self._on_speed_changed(self.speed_slider.value())

        self.step_combo = QComboBox()
        self.step_combo.setFocusPolicy(Qt.NoFocus)
        for label, size in STEP_CHOICES:
            self.step_combo.addItem(label, size)
        self.step_combo.setToolTip(
            "Continuous: move while the button is held.\n"
            "A number: each press is one fixed move of that many degrees\n"
            "(millimetres for X/Y/Z), at the speed set here."
        )

        # Two rows, not one: speed + step on a single line is wider than the
        # 360px control column, which then grows a horizontal scrollbar and hides
        # the right-hand buttons.
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed"))
        speed_row.addWidget(self.speed_slider, 1)
        speed_row.addWidget(self.speed_label)
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step"))
        step_row.addWidget(self.step_combo, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.joint_page)
        self.stack.addWidget(self.cartesian_page)

        self.hint_label = QLabel()
        self.hint_label.setObjectName("sectionCaption")
        self.hint_label.setWordWrap(True)

        self.status_label = QLabel("")
        self.status_label.setObjectName("sectionCaption")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_row)
        layout.addLayout(speed_row)
        layout.addLayout(step_row)
        layout.addWidget(self.stack)
        layout.addWidget(self.hint_label)
        layout.addWidget(self.status_label)

        self.joint_page.goal_changed.connect(self.goal_changed)
        self.joint_page.jog_pressed.connect(lambda name, d: self.jog_pressed.emit("joint", name, d))
        self.joint_page.jog_released.connect(lambda name, d: self.jog_released.emit("joint", name, d))
        self.cartesian_page.jog_pressed.connect(lambda axis, d: self.jog_pressed.emit(self._mode, axis, d))
        self.cartesian_page.jog_released.connect(lambda axis, d: self.jog_released.emit(self._mode, axis, d))

        self.set_cartesian_available(False, "Load a digital twin to enable World/Tool jogging.")
        self._refresh_hint()

    # ---------------------------------------------------------------- state
    def mode(self) -> str:
        return self._mode

    def speed_fraction(self) -> float:
        return self.speed_slider.value() / 100.0

    def step_size(self) -> float:
        """0 = continuous; otherwise degrees (millimetres for a translation)."""
        return float(self.step_combo.currentData())

    def _on_speed_changed(self, value: int) -> None:
        self.speed_label.setText(f"{value} %")

    def _set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        if mode != "joint" and not self._cartesian_available:
            self.mode_buttons["joint"].setChecked(True)
            return
        self._mode = mode
        self.stack.setCurrentIndex(0 if mode == "joint" else 1)
        self._refresh_hint()
        self.mode_changed.emit(mode)

    def _refresh_hint(self) -> None:
        if not self._cartesian_available and self._unavailable_note:
            self.hint_label.setText(self._unavailable_note)
        else:
            self.hint_label.setText(_FRAME_HINTS[self._mode])

    # ---------------------------------------------------------------- MainWindow API
    def set_cartesian_available(self, available: bool, note: str = "") -> None:
        """Enable or disable the World/Tool modes. When unavailable (no twin
        loaded, or its MJCF has none of the arm's joints) the Cartesian modes
        are greyed out and the reason is shown, rather than the buttons
        silently doing nothing."""
        self._cartesian_available = available
        self._unavailable_note = "" if available else note
        for mode in ("world", "tool"):
            self.mode_buttons[mode].setEnabled(available)
            self.mode_buttons[mode].setToolTip("" if available else note)
        if not available and self._mode != "joint":
            self.mode_buttons["joint"].setChecked(True)
            self._mode = "joint"
            self.stack.setCurrentIndex(0)
            self.mode_changed.emit("joint")
        self._refresh_hint()

    def set_tcp_note(self, note: str) -> None:
        """Which point the X/Y/Z readout and the jog directions refer to (the
        TCP), shown under the buttons in the Cartesian modes."""
        self._tcp_note = note
        self.status_label.setText(note)

    def set_status(self, text: str) -> None:
        """A transient message (a joint hit its limit, speed was capped);
        empty text goes back to the standing TCP note."""
        self.status_label.setText(text or self._tcp_note)

    def set_input_enabled(self, enabled: bool) -> None:
        self.joint_page.set_input_enabled(enabled)
        self.cartesian_page.set_input_enabled(enabled)

    def set_pose(self, position_m, rpy_deg) -> None:
        self.cartesian_page.set_pose(position_m, rpy_deg)

    def set_reach(self, reach) -> None:
        self.cartesian_page.set_reach(reach)
