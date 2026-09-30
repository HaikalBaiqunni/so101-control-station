from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .tracking_chart import TrackingChart, tracking_metrics

BUILTIN_PRESET = "Gentle start (kp 8 / kd 0.5)"
NUDGE_DEG = 5.0


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
    presets_changed = Signal(dict)       # {name: {joint: [kp, kd]}} - user presets only
    nudge_requested = Signal(str, float) # joint, delta degrees

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

        # -- presets: named kp/kd sets (no motor command by themselves) -------------
        self._presets: dict[str, dict[str, list[float]]] = {}
        preset_box = QGroupBox("GAIN PRESETS")
        self.preset_combo = QComboBox()
        self.save_preset_btn = QPushButton("Save as...")
        self.save_preset_btn.clicked.connect(self._on_save_preset_clicked)
        self.load_preset_btn = QPushButton("Load")
        self.load_preset_btn.clicked.connect(self._on_load_preset_clicked)
        self.delete_preset_btn = QPushButton("Delete")
        self.delete_preset_btn.clicked.connect(self._on_delete_preset_clicked)
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.load_preset_btn)
        preset_row.addWidget(self.save_preset_btn)
        preset_row.addWidget(self.delete_preset_btn)
        preset_row.addStretch(1)
        preset_note = QLabel("Load fills the kp/kd boxes above; a connected MIT follower applies them live.")
        preset_note.setObjectName("sectionCaption")
        preset_note.setWordWrap(True)
        preset_layout = QVBoxLayout(preset_box)
        preset_layout.addWidget(self.preset_combo)
        preset_layout.addLayout(preset_row)
        preset_layout.addWidget(preset_note)
        self._refresh_preset_combo()

        # -- tracking: commanded vs measured for one joint ---------------------------
        track_box = QGroupBox("TRACKING")
        self.track_joint_combo = QComboBox()
        self.track_joint_combo.addItems(list(self.joint_order))
        self.track_joint_combo.currentIndexChanged.connect(lambda _i: self.tracking_chart.clear())
        self.nudge_minus_btn = QPushButton(f"-{NUDGE_DEG:.0f}\u00b0")
        self.nudge_plus_btn = QPushButton(f"+{NUDGE_DEG:.0f}\u00b0")
        self.nudge_minus_btn.clicked.connect(lambda: self._emit_nudge(-NUDGE_DEG))
        self.nudge_plus_btn.clicked.connect(lambda: self._emit_nudge(NUDGE_DEG))
        self.nudge_minus_btn.setToolTip(f"Move this joint {NUDGE_DEG:.0f} deg the other way (torque ON, Manual only)")
        self.nudge_plus_btn.setToolTip(f"Move this joint {NUDGE_DEG:.0f} deg forward (torque ON, Manual only)")
        track_top = QHBoxLayout()
        track_top.addWidget(self.track_joint_combo, 1)
        track_top.addWidget(QLabel("Nudge"))
        track_top.addWidget(self.nudge_minus_btn)
        track_top.addWidget(self.nudge_plus_btn)
        self.tracking_chart = TrackingChart()
        self.tracking_stats = QLabel("")
        self.tracking_stats.setObjectName("poseReadout")
        self.tracking_stats.setWordWrap(True)
        self.nudge_note = QLabel("")
        self.nudge_note.setObjectName("sectionCaption")
        self.nudge_note.setWordWrap(True)
        track_layout = QVBoxLayout(track_box)
        track_layout.addLayout(track_top)
        track_layout.addWidget(self.tracking_chart)
        track_layout.addWidget(self.tracking_stats)
        track_layout.addWidget(self.nudge_note)
        self._sample_count = 0
        self.set_nudge_state(False, "Connect the follower and turn torque ON.")

        # Everything scrolls: three stacked groups are taller than a 900 px window.
        content = QWidget()
        root = QVBoxLayout(content)
        root.addWidget(box)
        root.addWidget(preset_box)
        root.addWidget(track_box)
        root.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _on_control_mode_changed(self, _index: int) -> None:
        self.control_mode_changed.emit(self.control_mode_combo.currentData())

    def _on_mit_gains_edited(self, _value: float) -> None:
        self.mit_gains_changed.emit(self.mit_gains())

    def mit_gains(self) -> dict[str, tuple[float, float]]:
        return {name: (kp.value(), kd.value()) for name, (kp, kd) in self.mit_gain_spins.items()}

    # -- presets ---------------------------------------------------------------
    def set_presets(self, presets: dict) -> None:
        """Restore saved presets (from settings) without re-emitting them."""
        self._presets = {}
        if isinstance(presets, dict):
            for name, gains in presets.items():
                try:
                    self._presets[str(name)] = {j: [float(kp), float(kd)] for j, (kp, kd) in dict(gains).items()}
                except (TypeError, ValueError):
                    continue   # a damaged entry must not take the panel down
        self._refresh_preset_combo()

    def preset_names(self) -> list[str]:
        return [self.preset_combo.itemText(i) for i in range(self.preset_combo.count())]

    def _refresh_preset_combo(self, select: str | None = None) -> None:
        self.preset_combo.clear()
        self.preset_combo.addItem(BUILTIN_PRESET)
        self.preset_combo.addItems(sorted(self._presets))
        if select and self.preset_combo.findText(select) >= 0:
            self.preset_combo.setCurrentText(select)

    def save_preset(self, name: str) -> bool:
        name = name.strip()
        if not name or name == BUILTIN_PRESET:
            return False
        self._presets[name] = {j: [kp, kd] for j, (kp, kd) in self.mit_gains().items()}
        self._refresh_preset_combo(select=name)
        self.presets_changed.emit(dict(self._presets))
        return True

    def load_preset(self, name: str) -> bool:
        if name == BUILTIN_PRESET:
            gains = dict.fromkeys(self.joint_order, (8.0, 0.5))
        elif name in self._presets:
            gains = {j: (kp, kd) for j, (kp, kd) in self._presets[name].items()}
        else:
            return False
        self.set_mit_gains(gains)
        self.mit_gains_changed.emit(self.mit_gains())   # so a connected follower picks them up live
        return True

    def delete_preset(self, name: str) -> bool:
        if name not in self._presets:
            return False
        del self._presets[name]
        self._refresh_preset_combo()
        self.presets_changed.emit(dict(self._presets))
        return True

    def _on_save_preset_clicked(self) -> None:
        name, ok = QInputDialog.getText(self, "Save gain preset", "Preset name:")
        if ok and name.strip():
            if name.strip() in self._presets and QMessageBox.question(
                self, "Replace preset", f"Replace the existing preset '{name.strip()}'?"
            ) != QMessageBox.Yes:
                return
            self.save_preset(name)

    def _on_load_preset_clicked(self) -> None:
        self.load_preset(self.preset_combo.currentText())

    def _on_delete_preset_clicked(self) -> None:
        name = self.preset_combo.currentText()
        if name in self._presets and QMessageBox.question(
            self, "Delete preset", f"Delete the preset '{name}'?"
        ) == QMessageBox.Yes:
            self.delete_preset(name)

    # -- tracking / nudge --------------------------------------------------------
    def tracking_joint(self) -> str:
        return self.track_joint_combo.currentText()

    def add_tracking_sample(self, goal: float, measured: float) -> None:
        self.tracking_chart.add_sample(time.monotonic(), goal, measured)
        self._sample_count += 1
        if self._sample_count % 15 == 0:   # ~2 Hz text refresh
            self._refresh_stats()

    def _refresh_stats(self) -> None:
        m = tracking_metrics(self.tracking_chart.samples())
        if m["peak_error"] is None:
            self.tracking_stats.setText("")
            return
        parts = [f"peak error {m['peak_error']:.1f} deg"]
        if m["step_deg"] is not None:
            parts.append("settle " + (f"{m['settle_s']:.2f} s" if m["settle_s"] is not None else "not yet"))
            parts.append(f"overshoot {m['overshoot_pct']:.0f} %")
        self.tracking_stats.setText("   ".join(parts))

    def set_nudge_state(self, allowed: bool, reason: str) -> None:
        self.nudge_minus_btn.setEnabled(allowed)
        self.nudge_plus_btn.setEnabled(allowed)
        self.nudge_note.setText("" if allowed else f"Nudge is off: {reason}")

    def _emit_nudge(self, delta: float) -> None:
        self.nudge_requested.emit(self.tracking_joint(), delta)

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
