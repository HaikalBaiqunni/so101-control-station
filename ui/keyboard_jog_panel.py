from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout

# QWERTY top row jogs a joint positive, the row below jogs it negative -
# left-to-right in the same order as Joint Control's own slider stack, so the
# physical key layout visually lines up with the panel above it.
KEY_JOG_MAP = {
    Qt.Key_Q: ("shoulder_pan", 1.0), Qt.Key_A: ("shoulder_pan", -1.0),
    Qt.Key_W: ("shoulder_lift", 1.0), Qt.Key_S: ("shoulder_lift", -1.0),
    Qt.Key_E: ("elbow_flex", 1.0), Qt.Key_D: ("elbow_flex", -1.0),
    Qt.Key_R: ("wrist_flex", 1.0), Qt.Key_F: ("wrist_flex", -1.0),
    Qt.Key_T: ("wrist_roll", 1.0), Qt.Key_G: ("wrist_roll", -1.0),
    Qt.Key_Y: ("gripper", 1.0), Qt.Key_H: ("gripper", -1.0),
}

_SHORT_NAMES = {
    "shoulder_pan": "pan", "shoulder_lift": "lift", "elbow_flex": "elbow",
    "wrist_flex": "w.flex", "wrist_roll": "roll", "gripper": "gripper",
}

_TOP_ROW = [(Qt.Key_Q, "Q"), (Qt.Key_W, "W"), (Qt.Key_E, "E"), (Qt.Key_R, "R"), (Qt.Key_T, "T"), (Qt.Key_Y, "Y")]
_BOTTOM_ROW = [(Qt.Key_A, "A"), (Qt.Key_S, "S"), (Qt.Key_D, "D"), (Qt.Key_F, "F"), (Qt.Key_G, "G"), (Qt.Key_H, "H")]


class _KeyCap(QFrame):
    """One physical-looking key. `active` is a dynamic property, not a
    hardcoded style, so ui/style.py's QSS can react to it with
    `QFrame#keycap[active="true"]` without this widget knowing any colors."""

    def __init__(self, letter: str, joint: str, symbol: str, parent=None):
        super().__init__(parent)
        self.setObjectName("keycap")
        self.setProperty("active", False)
        self.setFixedSize(76, 60)

        letter_label = QLabel(letter)
        letter_label.setObjectName("keycapLetter")
        letter_label.setAlignment(Qt.AlignCenter)

        joint_label = QLabel(f"{symbol} {_SHORT_NAMES.get(joint, joint)}")
        joint_label.setObjectName("keycapJoint")
        joint_label.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(2)
        layout.addWidget(letter_label)
        layout.addWidget(joint_label)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)


class KeyboardJogPanel(QGroupBox):
    """Visual key-cap layout mirroring KEY_JOG_MAP, lighting up live while a
    key is actually held - the point is the panel always shows the truth of
    what MainWindow's key-tracking state believes is currently pressed."""

    def __init__(self, parent=None):
        super().__init__("KEYBOARD JOG", parent)

        self._caps: dict[int, _KeyCap] = {}

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.addSpacing(22)  # stagger, like a real keyboard
        for key, letter in _TOP_ROW:
            joint, _sign = KEY_JOG_MAP[key]
            cap = _KeyCap(letter, joint, "▲")
            self._caps[key] = cap
            top_row.addWidget(cap)
        top_row.addStretch(1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        for key, letter in _BOTTOM_ROW:
            joint, _sign = KEY_JOG_MAP[key]
            cap = _KeyCap(letter, joint, "▼")
            self._caps[key] = cap
            bottom_row.addWidget(cap)
        bottom_row.addStretch(1)

        hint = QLabel("Click the window to focus it, then hold keys to jog - multiple at once.")
        hint.setObjectName("sectionCaption")
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addLayout(bottom_row)
        layout.addWidget(hint)

    def set_key_active(self, key: int, active: bool) -> None:
        cap = self._caps.get(key)
        if cap:
            cap.set_active(active)

    def clear_all(self) -> None:
        for cap in self._caps.values():
            cap.set_active(False)
