"""Top bar: robot picker, connection chips, drawer/page buttons and Stop.

It floats over the page below it (see ShellHost) as two rounded groups and a
Stop button; the strip between them is masked out, so the digital twin under
it still receives mouse orbit / zoom there."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from .icons import icon
from .style import COLORS

CHIP_STATES = ("off", "good", "warn")
BAR_H = 72   # height reserved at the top of every page so nothing sits under the bar


class TopBar(QWidget):
    """Pure view: it owns no robot state. MainWindow feeds it through
    set_chip() and listens to nav_toggled / stop_requested."""

    nav_toggled = Signal(str, bool)   # "waypoints" | "telemetry" | "tune" | "setup", checked
    stop_requested = Signal()
    palette_requested = Signal()
    about_requested = Signal()

    NAV = (("waypoints", "Waypoints"), ("telemetry", "Telemetry"), ("tune", "Tune"), ("setup", "Setup"))
    BAR_H = BAR_H

    def __init__(self, robot_combo: QComboBox):
        super().__init__()
        self.setObjectName("topBar")
        self.robot_combo = robot_combo

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(10)

        self.left_pill = QFrame()
        self.left_pill.setObjectName("topPill")
        left = QHBoxLayout(self.left_pill)
        left.setContentsMargins(14, 4, 14, 4)
        left.setSpacing(8)
        left.addWidget(QLabel("Robot"))
        left.addWidget(robot_combo)
        self.chips: dict[str, QLabel] = {}
        for key, text in (("follower", "Follower"), ("leader", "Leader"),
                          ("torque", "Torque"), ("source", "Manual")):
            chip = QLabel(text)
            chip.setObjectName("chip")
            chip.setProperty("state", "off")
            self.chips[key] = chip
            left.addWidget(chip)
        layout.addWidget(self.left_pill)
        layout.addStretch(1)

        self.right_pill = QFrame()
        self.right_pill.setObjectName("topPill")
        right = QHBoxLayout(self.right_pill)
        right.setContentsMargins(8, 5, 8, 5)
        right.setSpacing(4)
        self.search_btn = QPushButton("")
        self.search_btn.setObjectName("segButton")
        self.search_btn.setFocusPolicy(Qt.NoFocus)
        self.search_btn.setIcon(icon("search", COLORS["text_muted"]))
        self.search_btn.setIconSize(QSize(18, 18))
        self.search_btn.setToolTip("Command palette (Ctrl+K)")
        self.search_btn.clicked.connect(self.palette_requested)
        right.addWidget(self.search_btn)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text in self.NAV:
            button = QPushButton(text)
            button.setObjectName("segButton")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)   # keep arrow/space keys for keyboard jog
            button.setIcon(icon(key, COLORS["text_muted"], "#06121f"))
            button.setIconSize(QSize(18, 18))
            button.toggled.connect(lambda checked, k=key: self.nav_toggled.emit(k, checked))
            self.nav_buttons[key] = button
            right.addWidget(button)
        self.about_btn = QPushButton("")
        self.about_btn.setObjectName("segButton")
        self.about_btn.setFocusPolicy(Qt.NoFocus)
        self.about_btn.setIcon(icon("info", COLORS["text_muted"]))
        self.about_btn.setIconSize(QSize(18, 18))
        self.about_btn.setToolTip("About this app")
        self.about_btn.clicked.connect(self.about_requested)
        right.addWidget(self.about_btn)
        layout.addWidget(self.right_pill)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.setFocusPolicy(Qt.NoFocus)
        self.stop_btn.setIcon(icon("stop", "#2a0a0a"))
        self.stop_btn.setIconSize(QSize(18, 18))
        self.stop_btn.setToolTip("Torque OFF on the follower and switch control back to Manual")
        self.stop_btn.clicked.connect(self.stop_requested)
        layout.addWidget(self.stop_btn)

        for widget in (self.left_pill, self.right_pill, self.stop_btn):
            widget.installEventFilter(self)   # their size changes when chip text / buttons change

    # -- input passthrough: only the pills and Stop take the mouse -------------
    def _update_mask(self) -> None:
        region = QRegion()
        for widget in (self.left_pill, self.right_pill, self.stop_btn):
            if not widget.geometry().isEmpty():
                region = region.united(QRegion(widget.geometry()))
        self.setMask(region)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_mask()

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show):
            self._update_mask()
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_mask()

    def set_chip(self, key: str, text: str, state: str) -> None:
        chip = self.chips[key]
        chip.setText(text)
        chip.setProperty("state", state if state in CHIP_STATES else "off")
        chip.style().unpolish(chip)   # dynamic property: force the QSS to re-evaluate
        chip.style().polish(chip)

    def set_nav_checked(self, key: str, checked: bool) -> None:
        """Programmatic check without re-emitting nav_toggled."""
        button = self.nav_buttons[key]
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)


class ShellHost(QWidget):
    """Holds the page stack full-size with the top bar floating over it."""

    def __init__(self, content: QWidget, overlay: QWidget):
        super().__init__()
        self.content = content
        self.overlay = overlay
        content.setParent(self)
        overlay.setParent(self)
        overlay.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.content.setGeometry(0, 0, self.width(), self.height())
        self.overlay.setGeometry(0, 0, self.width(), BAR_H)
        self.overlay.raise_()
