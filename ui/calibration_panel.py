from __future__ import annotations

import json
import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

from core.servo_bus import JOINT_ORDER

DEFAULT_CALIBRATION_ROOT = os.path.expanduser("~/.cache/huggingface/lerobot/calibration")


class CalibrationPanel(QWidget):
    """Runs the exact same reset -> half-turn-homing -> record-range ->
    write-limits sequence as `lerobot-calibrate`, step by step from the GUI.
    Works on either role - Follower or Leader - selected up top."""

    connect_requested = Signal(str)              # port
    disconnect_requested = Signal()
    reset_requested = Signal()
    set_middle_requested = Signal()
    start_recording_requested = Signal()
    stop_recording_requested = Signal()
    finish_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # -- connection row -------------------------------------------------
        conn_box = QGroupBox("1 - CONNECT")
        self.role_combo = QComboBox()
        self.role_combo.addItems(["Follower", "Leader"])
        self.role_combo.currentTextChanged.connect(self._on_role_changed)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self._refresh_ports()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_ports)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        self.status_label = QLabel("DISCONNECTED")
        self.status_label.setObjectName("statusDanger")

        conn_layout = QGridLayout(conn_box)
        conn_layout.addWidget(QLabel("Role"), 0, 0)
        conn_layout.addWidget(self.role_combo, 0, 1)
        conn_layout.addWidget(QLabel("Port"), 1, 0)
        conn_layout.addWidget(self.port_combo, 1, 1)
        conn_layout.addWidget(refresh_btn, 1, 2)
        conn_layout.addWidget(self.connect_btn, 2, 0, 1, 2)
        conn_layout.addWidget(self.status_label, 2, 2)

        # -- step buttons -----------------------------------------------------
        steps_box = QGroupBox("2 - CALIBRATION STEPS (run in order)")
        self.reset_btn = QPushButton("1. Reset motors")
        self.reset_btn.clicked.connect(self.reset_requested)
        self.middle_btn = QPushButton("2. Set middle (arm parked at centre now)")
        self.middle_btn.clicked.connect(self.set_middle_requested)
        self.start_btn = QPushButton("3. Start recording range of motion")
        self.start_btn.clicked.connect(self.start_recording_requested)
        self.stop_btn = QPushButton("4. Stop recording")
        self.stop_btn.clicked.connect(self.stop_recording_requested)
        self.finish_btn = QPushButton("5. Finish && Save...")
        self.finish_btn.clicked.connect(self.finish_requested)

        hint = QLabel(
            "Step 2: move all joints (by hand) to roughly the middle of their travel, then click.\n"
            "Step 3-4: slowly move every joint through its FULL range except wrist_roll "
            "(that one is left as a full continuous turn automatically)."
        )
        hint.setObjectName("sectionCaption")
        hint.setWordWrap(True)

        steps_layout = QVBoxLayout(steps_box)
        for w in (self.reset_btn, self.middle_btn, self.start_btn, self.stop_btn, self.finish_btn, hint):
            steps_layout.addWidget(w)

        # -- live table -------------------------------------------------------
        table_box = QGroupBox("LIVE POSITIONS (raw ticks, 0-4095)")
        self.table = QTableWidget(len(JOINT_ORDER), 4)
        self.table.setHorizontalHeaderLabels(["Joint", "Min", "Pos", "Max"])
        for row, name in enumerate(JOINT_ORDER):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            for col in range(1, 4):
                self.table.setItem(row, col, QTableWidgetItem("-"))
        self.table.resizeColumnsToContents()
        table_layout = QVBoxLayout(table_box)
        table_layout.addWidget(self.table)

        self._set_step_buttons_enabled(False)

        root = QVBoxLayout(self)
        top_row = QHBoxLayout()
        top_row.addWidget(conn_box)
        top_row.addWidget(steps_box)
        root.addLayout(top_row)
        root.addWidget(table_box)

    # ---------------------------------------------------------------- helpers
    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItems([p.device for p in list_ports.comports()])
        if current:
            self.port_combo.setEditText(current)

    def _on_role_changed(self, _role: str) -> None:
        pass  # role only affects the suggested save path, handled by MainWindow

    def _on_connect_clicked(self) -> None:
        if self.connect_btn.text() == "Connect":
            self.connect_requested.emit(self.port_combo.currentText())
        else:
            self.disconnect_requested.emit()

    def _set_step_buttons_enabled(self, enabled: bool) -> None:
        for w in (self.reset_btn, self.middle_btn, self.start_btn, self.stop_btn, self.finish_btn):
            w.setEnabled(enabled)

    def role(self) -> str:
        return self.role_combo.currentText().lower()  # "follower" | "leader"

    def default_save_path(self, robot_id: str) -> str:
        sub = "robots/so_follower" if self.role() == "follower" else "teleoperators/so_leader"
        return os.path.join(DEFAULT_CALIBRATION_ROOT, sub, f"{robot_id}.json")

    # -- called by MainWindow in response to worker signals ------------------
    def set_connected(self, connected: bool) -> None:
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.status_label.setText("CONNECTED" if connected else "DISCONNECTED")
        self.status_label.setObjectName("statusGood" if connected else "statusDanger")
        self.status_label.setStyleSheet("")
        self._set_step_buttons_enabled(connected)

    def update_live_table(self, positions: dict, mins: dict, maxes: dict) -> None:
        for row, name in enumerate(JOINT_ORDER):
            if name not in positions:
                continue
            self.table.setItem(row, 1, QTableWidgetItem(str(mins.get(name, "-"))))
            self.table.setItem(row, 2, QTableWidgetItem(str(positions.get(name, "-"))))
            self.table.setItem(row, 3, QTableWidgetItem(str(maxes.get(name, "-"))))

    def prompt_save(self, calibration_dict: dict, robot_id_hint: str = "my_arm") -> None:
        default_path = self.default_save_path(robot_id_hint)
        os.makedirs(os.path.dirname(default_path), exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save calibration", default_path, "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(calibration_dict, f, indent=4)
        QMessageBox.information(self, "Saved", f"Calibration saved to:\n{path}")
