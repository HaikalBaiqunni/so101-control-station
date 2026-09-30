from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionCaption")
    label.setWordWrap(True)
    return label


class DmCalibrationPage(QWidget):
    """Calibration tools for the reBot B601-DM. Each card only launches an
    EXISTING dialog owned by MainWindow (read-only sweeps: you move the arm by
    hand, the app just watches) - no calibration logic lives here."""

    gripper_calibrate_requested = Signal()
    leader_calibrate_requested = Signal()
    open_ids_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        gripper = QGroupBox("FOLLOWER GRIPPER RANGE")
        self.gripper_summary = QLabel("not measured yet")
        self.gripper_summary.setObjectName("poseReadout")
        self.gripper_btn = QPushButton("Measure gripper range...")
        self.gripper_btn.clicked.connect(self.gripper_calibrate_requested)
        box = QVBoxLayout(gripper)
        box.addWidget(_note(
            "Sweep the jaw open to closed by hand with torque OFF. Stops the app from "
            "ever driving the gripper past its real end stop. Needs the follower connected."
        ))
        box.addWidget(self.gripper_summary)
        box.addWidget(self.gripper_btn)

        leader = QGroupBox("LEADER - STAR ARM 102")
        self.leader_summary = QLabel("using vendor default ranges")
        self.leader_summary.setObjectName("poseReadout")
        self.leader_summary.setWordWrap(True)
        self.leader_btn = QPushButton("Sweep leader joints...")
        self.leader_btn.clicked.connect(self.leader_calibrate_requested)
        box = QVBoxLayout(leader)
        box.addWidget(_note(
            "Sweep every leader joint through its full travel. Replaces the vendor "
            "defaults, which were off by up to 1.75x on the unit this was built with. "
            "Needs the leader connected."
        ))
        box.addWidget(self.leader_summary)
        box.addWidget(self.leader_btn)

        zero = QGroupBox("ZERO POSITION")
        self.ids_btn = QPushButton("Open Motors and ids")
        self.ids_btn.clicked.connect(self.open_ids_requested)
        box = QVBoxLayout(zero)
        box.addWidget(_note(
            "Set zero saves the arm's current pose as zero for one motor, written to "
            "the motor's flash. It works on the motor found by Probe, so it lives in "
            "Motors and ids, section 4 (Motor state)."
        ))
        box.addWidget(self.ids_btn)

        root = QVBoxLayout(self)
        root.addWidget(gripper)
        root.addWidget(leader)
        root.addWidget(zero)
        root.addStretch(1)

    def set_gripper_summary(self, text: str) -> None:
        self.gripper_summary.setText(text)

    def set_leader_summary(self, text: str) -> None:
        self.leader_summary.setText(text)
