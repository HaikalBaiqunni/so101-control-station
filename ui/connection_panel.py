from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
)
from serial.tools import list_ports


class ConnectionPanel(QGroupBox):
    connect_requested = Signal(str, str)     # (port, calibration_path)
    disconnect_requested = Signal()
    torque_requested = Signal(bool)          # True = enable, False = disable

    def __init__(self, parent=None):
        super().__init__("CONNECTION", parent)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self._refresh_ports()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_ports)

        self.calib_edit = QLineEdit()
        self.calib_edit.setPlaceholderText("path to LeRobot calibration .json")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_calibration)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        self.torque_on_btn = QPushButton("Torque ON")
        self.torque_on_btn.clicked.connect(lambda: self.torque_requested.emit(True))
        self.torque_off_btn = QPushButton("Torque OFF")
        self.torque_off_btn.setObjectName("dangerButton")
        self.torque_off_btn.clicked.connect(lambda: self.torque_requested.emit(False))

        self.status_label = QLabel("DISCONNECTED")
        self.status_label.setObjectName("statusDanger")

        self.set_connected(False)

        layout = QGridLayout(self)
        layout.addWidget(QLabel("Port"), 0, 0)
        layout.addWidget(self.port_combo, 0, 1)
        layout.addWidget(refresh_btn, 0, 2)
        layout.addWidget(QLabel("Calibration"), 1, 0)
        layout.addWidget(self.calib_edit, 1, 1)
        layout.addWidget(browse_btn, 1, 2)
        layout.addWidget(self.connect_btn, 2, 0, 1, 2)
        layout.addWidget(self.status_label, 2, 2)
        layout.addWidget(self.torque_on_btn, 3, 0)
        layout.addWidget(self.torque_off_btn, 3, 1)

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItems([p.device for p in list_ports.comports()])
        if current:
            self.port_combo.setEditText(current)

    def _browse_calibration(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select calibration file", "", "JSON (*.json)")
        if path:
            self.calib_edit.setText(path)

    def _on_connect_clicked(self) -> None:
        if self.connect_btn.text() == "Connect":
            self.connect_requested.emit(self.port_combo.currentText(), self.calib_edit.text())
        else:
            self.disconnect_requested.emit()

    def set_connected(self, connected: bool) -> None:
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.torque_on_btn.setEnabled(connected)
        self.torque_off_btn.setEnabled(connected)
        self.status_label.setText("CONNECTED" if connected else "DISCONNECTED")
        self.status_label.setObjectName("statusGood" if connected else "statusDanger")
        self.status_label.setStyleSheet("")  # force style re-poll
