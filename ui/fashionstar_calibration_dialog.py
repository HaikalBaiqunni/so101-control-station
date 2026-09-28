"""Sweep-based range calibration for Seeed's Star Arm 102 leader
(core/fashionstar_bus.py) - deliberately much simpler than SO-101's
CalibrationWorker/CalibrationPanel (no homing, no stall-based auto-find, no
manual point capture): the leader's zero point is already handled by the
existing `lerobot-calibrate` (its servos' own set_origin_point(), absolute
12-bit magnetic encoders, no homing ambiguity). This only needs to measure
each joint's real SPAN, because core/fashionstar_bus.py's hardcoded
JOINT_RANGES defaults (copied from lerobot's own unverified config) were
confirmed wrong on real hardware this session - by up to ~1.75x for
wrist_roll.

No separate serial connection: this dialog taps the already-connected
leader's own live FashionStarLeaderWorker.native_positions_updated stream.
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

from core.fashionstar_bus import FASHIONSTAR_NAME_BY_FOLLOWER_NAME, FOLLOWER_NAME_BY_FASHIONSTAR_NAME
from core.sweep_recorder import SweepRecorder

FOLLOWER_JOINT_NAMES: tuple[str, ...] = tuple(FOLLOWER_NAME_BY_FASHIONSTAR_NAME.values())


class FashionStarCalibrationDialog(QDialog):
    """Not modal-vs-nonmodal by design choice alone: uses exec() (like
    ControlSourcePanel's existing "Relay trim..." dialog) because Qt keeps
    pumping its event loop during exec(), so the leader worker's queued
    cross-thread signal keeps arriving and the live table keeps updating
    while the dialog is open."""

    def __init__(self, leader_worker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate leader (Star Arm 102) joint ranges")
        self.leader_worker = leader_worker
        self.recorder = SweepRecorder(FOLLOWER_JOINT_NAMES)
        self.saved_native_ranges: dict[str, tuple[float, float]] | None = None

        note = QLabel(
            "Move every leader joint SLOWLY through its full real range of "
            "motion (one at a time or all together) - the Min/Max columns "
            "below track the true extremes as you go. This replaces a "
            "hardcoded default known to be wrong for some joints (confirmed "
            "on real hardware - up to ~1.75x off for wrist_roll).\n\n"
            "Read-only: this never commands the leader to move, it only "
            "watches what your hand does. Takes effect on the next Leader "
            "Connect, not live."
        )
        note.setObjectName("sectionCaption")
        note.setWordWrap(True)

        self.grid = QGridLayout()
        self.grid.addWidget(QLabel("Joint"), 0, 0)
        self.grid.addWidget(QLabel("Current"), 0, 1)
        self.grid.addWidget(QLabel("Min"), 0, 2)
        self.grid.addWidget(QLabel("Max"), 0, 3)
        self._value_labels: dict[str, tuple[QLabel, QLabel, QLabel]] = {}
        for row, name in enumerate(FOLLOWER_JOINT_NAMES, start=1):
            self.grid.addWidget(QLabel(name), row, 0)
            current_lbl = QLabel("-")
            min_lbl = QLabel("-")
            max_lbl = QLabel("-")
            self.grid.addWidget(current_lbl, row, 1)
            self.grid.addWidget(min_lbl, row, 2)
            self.grid.addWidget(max_lbl, row, 3)
            self._value_labels[name] = (current_lbl, min_lbl, max_lbl)

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

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(self.grid)
        layout.addWidget(buttons)

        self.leader_worker.native_positions_updated.connect(self._on_native_positions)

    def _on_native_positions(self, positions: dict[str, float]) -> None:
        self.recorder.update(positions)
        for name, (current_lbl, min_lbl, max_lbl) in self._value_labels.items():
            if name in self.recorder.last:
                current_lbl.setText(f"{self.recorder.last[name]:.1f}")
            spans = self.recorder.spans()
            if name in spans:
                lo, hi = spans[name]
                min_lbl.setText(f"{lo:.1f}")
                max_lbl.setText(f"{hi:.1f}")
        self.save_btn.setEnabled(self.recorder.is_complete())

    def _on_reset_clicked(self) -> None:
        self.recorder.reset()
        for _current_lbl, min_lbl, max_lbl in self._value_labels.values():
            min_lbl.setText("-")
            max_lbl.setText("-")
        self.save_btn.setEnabled(False)

    def _on_save_clicked(self) -> None:
        self.saved_native_ranges = {
            FASHIONSTAR_NAME_BY_FOLLOWER_NAME[name]: span
            for name, span in self.recorder.spans().items()
        }
        self.accept()

    def closeEvent(self, event) -> None:
        self.leader_worker.native_positions_updated.disconnect(self._on_native_positions)
        super().closeEvent(event)
