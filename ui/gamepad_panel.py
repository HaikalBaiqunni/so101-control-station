from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout

# default axis -> joint mapping for a standard Xbox-style pad.
# left stick = shoulder, right stick = elbow/wrist, triggers/bumpers = roll+gripper.
DEFAULT_AXIS_MAP = {
    0: ("shoulder_pan", 1.0),
    1: ("shoulder_lift", -1.0),
    3: ("wrist_flex", 1.0),
    4: ("elbow_flex", -1.0),
}
DEFAULT_BUTTON_MAP = {
    4: ("wrist_roll", -5.0),   # left bumper: jog wrist_roll -5 deg per press
    5: ("wrist_roll", 5.0),    # right bumper: jog wrist_roll +5 deg per press
    2: ("gripper", -5.0),      # X: close a bit
    1: ("gripper", 5.0),       # B: open a bit
}


class GamepadPanel(QGroupBox):
    enable_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__("GAMEPAD (optional)", parent)

        self.status_label = QLabel("no gamepad detected")
        self.status_label.setObjectName("sectionCaption")

        self.toggle_btn = QPushButton("Enable gamepad")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.toggled.connect(self.enable_toggled)

        legend = QLabel(
            "Left stick: shoulder pan/lift  ·  Right stick: elbow/wrist flex\n"
            "Bumpers: wrist roll  ·  X/B: gripper close/open"
        )
        legend.setObjectName("sectionCaption")

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.toggle_btn)
        layout.addWidget(legend)

    def set_connected(self, connected: bool) -> None:
        self.status_label.setText("gamepad connected" if connected else "no gamepad detected")
