"""Sweep-based range calibration for the reBot B601-DM follower's gripper
(finger_left only) - a direct response to a real incident this session:
holding the Jog panel's gripper button past its true mechanical stop caused
sustained stall current (Damiao's POS_VEL mode has no software stall/current
protection - the motor's onboard PID just keeps applying torque toward an
unreachable target) until the power supply's fuse blew.

Root cause: core/robot_profiles.py's preview_ranges["finger_left"] is an
ASSUMED range, not a measured one - core/damiao_bus.py's write_goals_deg
clamps toward it regardless of whether it's actually reachable on this
physical unit. Same fix idea as the FashionStar leader's calibration dialog
(ui/fashionstar_calibration_dialog.py) - measure the real range by hand
instead of trusting a constant - deliberately scoped to just this one joint,
since it's the one that actually broke; the other six already passed
staged jog verification cleanly.

Only ever meaningful with Torque OFF: calibrating while the motor is
actively holding a position (torque on) means the hand is fighting the
motor, not measuring anything real - the dialog refuses outright rather than
letting that happen. No separate serial connection either: taps the
already-connected follower's own live DmRobotWorker.positions_updated
stream, exactly like the leader dialog does.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.sweep_recorder import SweepRecorder

JOINT_NAME = "finger_left"


class DmGripperCalibrationDialog(QDialog):
    """Same exec()-with-live-updates shape as FashionStarCalibrationDialog -
    Qt keeps pumping its event loop during exec(), so the follower worker's
    queued cross-thread positions_updated signal keeps arriving and the
    table keeps updating while the dialog is open."""

    def __init__(self, robot_worker, torque_enabled: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate follower gripper range")
        self.robot_worker = robot_worker
        self.torque_enabled = torque_enabled
        self.recorder = SweepRecorder([JOINT_NAME])
        self.saved_range: tuple[float, float] | None = None

        layout = QVBoxLayout(self)

        if torque_enabled:
            warning = QLabel(
                "Torque is ON - turn it OFF first. Calibrating with the "
                "motor actively holding a position means your hand is "
                "fighting the motor, not measuring the real mechanical "
                "range - this is exactly the condition that caused the "
                "stall/blown fuse this dialog exists to prevent."
            )
            warning.setObjectName("statusDanger")
            warning.setWordWrap(True)
            layout.addWidget(warning)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.reject)
            layout.addWidget(buttons)
            return

        note = QLabel(
            "Torque is OFF - move the gripper by hand SLOWLY through its "
            "full real range (fully open to fully closed) - the Min/Max "
            "columns below track the true extremes as you go. This "
            "replaces the hardcoded default range that this fuse-blowing "
            "incident showed doesn't match this unit's real mechanical "
            "travel.\n\n"
            "Read-only: this never commands the motor to move, it only "
            "watches what your hand does. Takes effect on the next "
            "Connect, not live."
        )
        note.setObjectName("sectionCaption")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.grid = QGridLayout()
        self.grid.addWidget(QLabel("Joint"), 0, 0)
        self.grid.addWidget(QLabel("Current"), 0, 1)
        self.grid.addWidget(QLabel("Min"), 0, 2)
        self.grid.addWidget(QLabel("Max"), 0, 3)
        self.grid.addWidget(QLabel(JOINT_NAME), 1, 0)
        self.current_lbl = QLabel("-")
        self.min_lbl = QLabel("-")
        self.max_lbl = QLabel("-")
        self.grid.addWidget(self.current_lbl, 1, 1)
        self.grid.addWidget(self.min_lbl, 1, 2)
        self.grid.addWidget(self.max_lbl, 1, 3)
        layout.addLayout(self.grid)

        self.reset_btn = QPushButton("Reset sweep")
        self.reset_btn.clicked.connect(self._on_reset_clicked)

        self.save_btn = QPushButton("Save")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save_clicked)

        buttons = QDialogButtonBox()
        buttons.addButton(self.reset_btn, QDialogButtonBox.ActionRole)
        buttons.addButton(self.save_btn, QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.robot_worker.positions_updated.connect(self._on_positions)

    def _on_positions(self, positions: dict[str, float]) -> None:
        if JOINT_NAME not in positions:
            return
        self.recorder.update({JOINT_NAME: positions[JOINT_NAME]})
        self.current_lbl.setText(f"{self.recorder.last[JOINT_NAME]:.1f}")
        spans = self.recorder.spans()
        if JOINT_NAME in spans:
            lo, hi = spans[JOINT_NAME]
            self.min_lbl.setText(f"{lo:.1f}")
            self.max_lbl.setText(f"{hi:.1f}")
        self.save_btn.setEnabled(self.recorder.is_complete())

    def _on_reset_clicked(self) -> None:
        self.recorder.reset()
        self.min_lbl.setText("-")
        self.max_lbl.setText("-")
        self.save_btn.setEnabled(False)

    def _on_save_clicked(self) -> None:
        self.saved_range = self.recorder.spans()[JOINT_NAME]
        self.accept()

    def closeEvent(self, event) -> None:
        if not self.torque_enabled:
            self.robot_worker.positions_updated.disconnect(self._on_positions)
        super().closeEvent(event)
