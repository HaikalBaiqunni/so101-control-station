from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.servo_bus import JOINT_ORDER

from .setup_panel import configure_port_combo, fill_port_combo

SOURCES = ["manual", "gamepad", "leader", "keyboard"]


class ControlSourcePanel(QGroupBox):
    """Arbitrates who is allowed to command the connected (follower) arm right
    now - only one source drives it at a time, to avoid fighting inputs."""

    source_changed = Signal(str)  # one of SOURCES
    leader_connect_requested = Signal(str, str)   # port, calibration_path
    leader_disconnect_requested = Signal()
    gripper_invert_toggled = Signal(bool)
    relay_trim_changed = Signal(str, float)   # joint, degrees

    def __init__(self, parent=None):
        super().__init__("CONTROL SOURCE", parent)

        self.manual_radio = QRadioButton("Manual (jog panel)")
        self.gamepad_radio = QRadioButton("Gamepad")
        self.leader_radio = QRadioButton("Leader arm (teleoperate)")
        self.keyboard_radio = QRadioButton("Keyboard jog")
        self.manual_radio.setChecked(True)

        self.group = QButtonGroup(self)
        for i, rb in enumerate([self.manual_radio, self.gamepad_radio, self.leader_radio, self.keyboard_radio]):
            self.group.addButton(rb, i)
        self.group.idClicked.connect(self._on_source_clicked)

        # -- leader sub-form, only relevant/visible when "leader" is selected
        self.leader_form = QWidget()
        self.leader_port_combo = QComboBox()
        self.leader_port_combo.setEditable(True)
        configure_port_combo(self.leader_port_combo)
        self._refresh_leader_ports()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_leader_ports)

        self.leader_calib_edit = QLineEdit()
        self.leader_calib_edit.setPlaceholderText("leader's calibration .json")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_leader_calibration)

        self.leader_connect_btn = QPushButton("Connect Leader")
        self.leader_connect_btn.clicked.connect(self._on_leader_connect_clicked)

        self.leader_status = QLabel("leader disconnected")
        self.leader_status.setObjectName("statusDanger")

        form_layout = QGridLayout(self.leader_form)
        form_layout.setContentsMargins(20, 4, 0, 0)
        form_layout.addWidget(QLabel("Port"), 0, 0)
        form_layout.addWidget(self.leader_port_combo, 0, 1)
        form_layout.addWidget(refresh_btn, 0, 2)
        form_layout.addWidget(QLabel("Calibration"), 1, 0)
        form_layout.addWidget(self.leader_calib_edit, 1, 1)
        form_layout.addWidget(browse_btn, 1, 2)
        form_layout.addWidget(self.leader_connect_btn, 2, 0, 1, 2)
        form_layout.addWidget(self.leader_status, 2, 2)
        self.leader_form.setVisible(False)

        # Which encoder direction opens the jaw can't be told from a
        # calibration file, and asking the operator to label it during
        # calibration proved unreliable (the leader's handle hangs open at
        # rest, so homing it untouched records "open" as closed). One switch,
        # flipped once after seeing which way it actually went, is both
        # simpler and harder to get wrong - and it survives recalibration.
        self.gripper_invert_check = QCheckBox("Gripper moves the wrong way (invert)")
        self.gripper_invert_check.setToolTip(
            "Tick this if squeezing the leader OPENS the follower's gripper.\n"
            "Saved to gui_settings.json, so it persists across restarts and\n"
            "is not lost when either arm is recalibrated."
        )
        self.gripper_invert_check.toggled.connect(self.gripper_invert_toggled)

        self.trim_btn = QPushButton("Relay trim...")
        self.trim_btn.setToolTip(
            "Constant per-joint correction for teleoperation.\n"
            "Needed when the leader and follower disagree about where a joint's\n"
            "zero is - unavoidable for wrist_roll, whose calibrated range is\n"
            "forced to a full turn, so its zero is wherever the arm was held\n"
            "during 'Set middle' and nothing physical pins it down."
        )
        self.trim_btn.clicked.connect(self._open_trim_dialog)
        self._trim_values: dict[str, float] = {}

        invert_row = QHBoxLayout()
        invert_row.addWidget(self.gripper_invert_check, 1)
        invert_row.addWidget(self.trim_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.manual_radio)
        layout.addWidget(self.gamepad_radio)
        layout.addWidget(self.leader_radio)
        layout.addWidget(self.leader_form)
        layout.addWidget(self.keyboard_radio)
        layout.addLayout(invert_row)

    def set_relay_trim(self, values: dict) -> None:
        self._trim_values = dict(values)

    def _open_trim_dialog(self) -> None:
        """Spinboxes emit live rather than on OK: the only way to judge a trim
        is to watch the arms track while turning it, so the dialog stays open
        and the change takes effect immediately."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Relay trim (leader -> follower)")
        layout = QVBoxLayout(dialog)

        note = QLabel(
            "Degrees added to the relayed target for each joint. Move the leader "
            "while adjusting - changes apply immediately and are saved.\n"
            "wrist_roll usually needs this: its zero comes from the pose held at "
            "'Set middle', not from anything mechanical."
        )
        note.setObjectName("sectionCaption")
        note.setWordWrap(True)
        layout.addWidget(note)

        grid = QGridLayout()
        for row, name in enumerate(JOINT_ORDER):
            spin = QDoubleSpinBox()
            spin.setRange(-45.0, 45.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setSuffix(" deg")
            spin.setValue(self._trim_values.get(name, 0.0))
            spin.valueChanged.connect(
                lambda value, joint=name: self._on_trim_spin(joint, value)
            )
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(spin, row, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.accept)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _on_trim_spin(self, joint: str, value: float) -> None:
        self._trim_values[joint] = value
        self.relay_trim_changed.emit(joint, value)

    def set_gripper_invert(self, enabled: bool) -> None:
        """Reflect the persisted value without re-emitting it back out."""
        self.gripper_invert_check.blockSignals(True)
        self.gripper_invert_check.setChecked(enabled)
        self.gripper_invert_check.blockSignals(False)

    def _refresh_leader_ports(self) -> None:
        current = self.leader_port_combo.currentText()
        self.leader_port_combo.clear()
        fill_port_combo(self.leader_port_combo)
        if current:
            self.leader_port_combo.setEditText(current)

    def selected_leader_port(self) -> str:
        """The combo shows "COM5 - USB-SERIAL CH340"; the SDK needs "COM5"."""
        text = self.leader_port_combo.currentText().strip()
        index = self.leader_port_combo.findText(text)
        if index >= 0:
            return self.leader_port_combo.itemData(index)
        return text.split(" - ", 1)[0].strip()

    def _browse_leader_calibration(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select leader calibration file", "", "JSON (*.json)")
        if path:
            self.leader_calib_edit.setText(path)

    def _on_source_clicked(self, source_id: int) -> None:
        source = SOURCES[source_id]
        self.leader_form.setVisible(source == "leader")
        self.source_changed.emit(source)

    def force_manual(self) -> None:
        """Switch to Manual as if the user clicked it - same leader_form
        visibility update and source_changed emission a real click would
        cause, so every listener (MainWindow's held-key clearing, status bar,
        session log) reacts consistently rather than this being a special
        case. QButtonGroup doesn't emit idClicked for a programmatic
        setChecked, so _on_source_clicked has to be called explicitly."""
        self.manual_radio.setChecked(True)
        self._on_source_clicked(SOURCES.index("manual"))

    def _on_leader_connect_clicked(self) -> None:
        if self.leader_connect_btn.text() == "Connect Leader":
            self.leader_connect_requested.emit(
                self.selected_leader_port(), self.leader_calib_edit.text()
            )
        else:
            self.leader_disconnect_requested.emit()

    def set_leader_connected(self, connected: bool) -> None:
        self.leader_connect_btn.setText("Disconnect Leader" if connected else "Connect Leader")
        self.leader_status.setText("leader connected" if connected else "leader disconnected")
        self.leader_status.setObjectName("statusGood" if connected else "statusDanger")
        self.leader_status.setStyleSheet("")

    def current_source(self) -> str:
        return SOURCES[self.group.checkedId()]
