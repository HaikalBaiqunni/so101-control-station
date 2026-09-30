from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class DmTunePanel(QWidget):
    """Motion mode and MIT gains for the reBot B601-DM follower (Damiao).

    Lives in the Tune drawer so it can be adjusted with the arm on screen: a
    connected follower in MIT mode picks new kp/kd up on its next command (see
    MainWindow._on_dm_setup_mit_gains_changed). Switching between POS_VEL and
    MIT still needs a reconnect: it is a switchControlMode() call made only
    during connect().

    These are NOT the motors' onboard KP/KI registers (those stay with the id
    workflow in Setup, because they need the probed motor); kp/kd here are
    per-command only.
    """

    control_mode_changed = Signal(str)   # "pos_vel" | "mit"
    mit_gains_changed = Signal(dict)     # {joint: (kp, kd)}

    def __init__(self, joint_order: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.joint_order = joint_order

        box = QGroupBox("MOTION MODE AND MIT GAINS")
        note = QLabel(
            "POS_VEL (default) relies on the motor's own onboard position/"
            "velocity loop - the safe, already-tested mode. MIT sends a fresh "
            "stiffness/damping (kp/kd) with every command instead. Start low "
            "and tune up gradually while watching the real arm. The mode is "
            "applied on the next Connect; kp/kd apply live."
        )
        note.setObjectName("sectionCaption")
        note.setWordWrap(True)

        self.control_mode_combo = QComboBox()
        self.control_mode_combo.addItem("POS_VEL (safe default)", "pos_vel")
        self.control_mode_combo.addItem("MIT (needs live tuning)", "mit")
        self.control_mode_combo.currentIndexChanged.connect(self._on_control_mode_changed)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Control mode"))
        mode_row.addWidget(self.control_mode_combo, 1)

        self.mit_gain_spins: dict[str, tuple[QDoubleSpinBox, QDoubleSpinBox]] = {}
        grid = QGridLayout()
        grid.addWidget(QLabel("Joint"), 0, 0)
        grid.addWidget(QLabel("kp"), 0, 1)
        grid.addWidget(QLabel("kd"), 0, 2)
        for row, name in enumerate(self.joint_order, start=1):
            grid.addWidget(QLabel(name), row, 0)
            kp_spin = QDoubleSpinBox()
            kp_spin.setRange(0.0, 500.0)
            kp_spin.setDecimals(2)
            kp_spin.setSingleStep(0.5)
            kp_spin.setValue(8.0)
            kp_spin.setToolTip("MIT per-command stiffness gain, 0-500. Very gentle to start: 5-10.")
            kd_spin = QDoubleSpinBox()
            kd_spin.setRange(0.0, 5.0)
            kd_spin.setDecimals(2)
            kd_spin.setSingleStep(0.05)
            kd_spin.setValue(0.5)
            kd_spin.setToolTip("MIT per-command damping gain, 0-5. Very gentle to start: 0.3-0.8.")
            kp_spin.valueChanged.connect(self._on_mit_gains_edited)
            kd_spin.valueChanged.connect(self._on_mit_gains_edited)
            grid.addWidget(kp_spin, row, 1)
            grid.addWidget(kd_spin, row, 2)
            self.mit_gain_spins[name] = (kp_spin, kd_spin)
        grid.setColumnStretch(0, 1)

        box_layout = QVBoxLayout(box)
        box_layout.addWidget(note)
        box_layout.addLayout(mode_row)
        box_layout.addLayout(grid)

        root = QVBoxLayout(self)
        root.addWidget(box)
        root.addStretch(1)

    def _on_control_mode_changed(self, _index: int) -> None:
        self.control_mode_changed.emit(self.control_mode_combo.currentData())

    def _on_mit_gains_edited(self, _value: float) -> None:
        self.mit_gains_changed.emit(self.mit_gains())

    def mit_gains(self) -> dict[str, tuple[float, float]]:
        return {name: (kp.value(), kd.value()) for name, (kp, kd) in self.mit_gain_spins.items()}

    def set_control_mode(self, mode: str) -> None:
        index = self.control_mode_combo.findData(mode)
        if index >= 0:
            self.control_mode_combo.blockSignals(True)
            self.control_mode_combo.setCurrentIndex(index)
            self.control_mode_combo.blockSignals(False)

    def set_mit_gains(self, gains: dict[str, tuple[float, float]]) -> None:
        for name, (kp_spin, kd_spin) in self.mit_gain_spins.items():
            kp, kd = gains.get(name, (kp_spin.value(), kd_spin.value()))
            kp_spin.blockSignals(True)
            kd_spin.blockSignals(True)
            kp_spin.setValue(kp)
            kd_spin.setValue(kd)
            kp_spin.blockSignals(False)
            kd_spin.blockSignals(False)
