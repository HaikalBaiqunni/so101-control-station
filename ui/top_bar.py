"""Always-visible top bar: robot picker, connection chips, drawer/page buttons and Stop."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

CHIP_STATES = ("off", "good", "warn")


class TopBar(QWidget):
    """Pure view: it owns no robot state. MainWindow feeds it through
    set_chip() and listens to nav_toggled / stop_requested."""

    nav_toggled = Signal(str, bool)   # "waypoints" | "telemetry" | "setup", checked
    stop_requested = Signal()

    NAV = (("waypoints", "Waypoints"), ("telemetry", "Telemetry"), ("setup", "Setup"))

    def __init__(self, robot_combo: QComboBox):
        super().__init__()
        self.setObjectName("topBar")
        self.robot_combo = robot_combo

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Robot"))
        layout.addWidget(robot_combo)

        self.chips: dict[str, QLabel] = {}
        for key, text in (("follower", "Follower"), ("leader", "Leader"),
                          ("torque", "Torque"), ("source", "Manual")):
            chip = QLabel(text)
            chip.setObjectName("chip")
            chip.setProperty("state", "off")
            self.chips[key] = chip
            layout.addWidget(chip)
        layout.addStretch(1)

        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text in self.NAV:
            button = QPushButton(text)
            button.setObjectName("segButton")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)   # keep arrow/space keys for keyboard jog
            button.toggled.connect(lambda checked, k=key: self.nav_toggled.emit(k, checked))
            self.nav_buttons[key] = button
            layout.addWidget(button)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.setFocusPolicy(Qt.NoFocus)
        self.stop_btn.setToolTip("Torque OFF on the follower and switch control back to Manual")
        self.stop_btn.clicked.connect(self.stop_requested)
        layout.addWidget(self.stop_btn)

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
