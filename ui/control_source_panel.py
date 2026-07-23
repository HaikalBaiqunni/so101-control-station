from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

SOURCES = ["manual", "gamepad", "leader"]


class ControlSourcePanel(QGroupBox):
    """Arbitrates who is allowed to command the connected (follower) arm right
    now - only one source drives it at a time, to avoid fighting inputs."""

    source_changed = Signal(str)  # "manual" | "gamepad" | "leader"
    leader_connect_requested = Signal(str, str)   # port, calibration_path
    leader_disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("CONTROL SOURCE", parent)

        self.manual_radio = QRadioButton("Manual (sliders)")
        self.gamepad_radio = QRadioButton("Gamepad")
        self.leader_radio = QRadioButton("Leader arm (teleoperate)")
        self.manual_radio.setChecked(True)

        self.group = QButtonGroup(self)
        for i, rb in enumerate([self.manual_radio, self.gamepad_radio, self.leader_radio]):
            self.group.addButton(rb, i)
        self.group.idClicked.connect(self._on_source_clicked)

        # -- leader sub-form, only relevant/visible when "leader" is selected
        self.leader_form = QWidget()
        self.leader_port_combo = QComboBox()
        self.leader_port_combo.setEditable(True)
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

        layout = QVBoxLayout(self)
        layout.addWidget(self.manual_radio)
        layout.addWidget(self.gamepad_radio)
        layout.addWidget(self.leader_radio)
        layout.addWidget(self.leader_form)

    def _refresh_leader_ports(self) -> None:
        current = self.leader_port_combo.currentText()
        self.leader_port_combo.clear()
        self.leader_port_combo.addItems([p.device for p in list_ports.comports()])
        if current:
            self.leader_port_combo.setEditText(current)

    def _browse_leader_calibration(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select leader calibration file", "", "JSON (*.json)")
        if path:
            self.leader_calib_edit.setText(path)

    def _on_source_clicked(self, source_id: int) -> None:
        source = SOURCES[source_id]
        self.leader_form.setVisible(source == "leader")
        self.source_changed.emit(source)

    def _on_leader_connect_clicked(self) -> None:
        if self.leader_connect_btn.text() == "Connect Leader":
            self.leader_connect_requested.emit(
                self.leader_port_combo.currentText(), self.leader_calib_edit.text()
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
