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

from core.servo_bus import JOINT_ORDER

from .setup_panel import describe_ports

DEFAULT_CALIBRATION_ROOT = os.path.expanduser("~/.cache/huggingface/lerobot/calibration")

# The five steps, in the only order they are valid in. Each one is enabled
# only once its predecessor has run: the sequence is stateful on the servos
# themselves (homing offsets must be cleared before "middle" means anything,
# and "finish" writes whatever min/max the recording pass happened to collect,
# which is garbage if no recording ever ran). Nothing in the protocol stops a
# user clicking 5 straight after connecting - the result is an arm whose
# limits are a single point, which then refuses to move at all and looks like
# a hardware fault. Gating the buttons is what makes the order self-evident.
STEP_SEQUENCE = ["reset", "middle", "start", "stop", "finish"]

STEP_NEXT_HINT = {
    "reset": "Next: step 1 - clear any previous calibration off the servos.",
    "middle": "Next: park every joint at the middle of its travel by hand, then step 2.",
    "start": "Next: step 3, then move each joint through its full range.",
    "stop": "Recording. Move every joint except wrist_roll through its FULL range, then step 4.",
    "finish": "Next: step 5 to write the limits and save the file.",
    None: "Done - calibration saved. Re-run from step 1 any time.",
}


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
        self.reset_btn.clicked.connect(lambda: self._on_step("reset"))
        self.middle_btn = QPushButton("2. Set middle (arm parked at centre now)")
        self.middle_btn.clicked.connect(lambda: self._on_step("middle"))
        self.start_btn = QPushButton("3. Start recording range of motion")
        self.start_btn.clicked.connect(lambda: self._on_step("start"))
        self.stop_btn = QPushButton("4. Stop recording")
        self.stop_btn.clicked.connect(lambda: self._on_step("stop"))
        self.finish_btn = QPushButton("5. Finish && Save...")
        self.finish_btn.clicked.connect(lambda: self._on_step("finish"))

        self._step_buttons = {
            "reset": self.reset_btn, "middle": self.middle_btn, "start": self.start_btn,
            "stop": self.stop_btn, "finish": self.finish_btn,
        }
        self._step_signals = {
            "reset": self.reset_requested, "middle": self.set_middle_requested,
            "start": self.start_recording_requested, "stop": self.stop_recording_requested,
            "finish": self.finish_requested,
        }
        self._next_step: str | None = "reset"

        self.step_hint = QLabel("")
        self.step_hint.setObjectName("sectionCaption")
        self.step_hint.setWordWrap(True)

        hint = QLabel(
            "Step 2: move all joints (by hand) to roughly the middle of their travel, then click.\n"
            "Step 3-4: slowly move every joint through its FULL range except wrist_roll "
            "(that one is left as a full continuous turn automatically)."
        )
        hint.setObjectName("sectionCaption")
        hint.setWordWrap(True)

        steps_layout = QVBoxLayout(steps_box)
        for w in (self.reset_btn, self.middle_btn, self.start_btn, self.stop_btn,
                  self.finish_btn, self.step_hint, hint):
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
        for device, label in describe_ports():
            self.port_combo.addItem(label, device)
        if current:
            self.port_combo.setEditText(current)

    def selected_port(self) -> str:
        """The combo shows "COM5 - USB-SERIAL CH340"; the SDK needs "COM5"."""
        text = self.port_combo.currentText().strip()
        index = self.port_combo.findText(text)
        if index >= 0:
            return self.port_combo.itemData(index)
        return text.split(" - ", 1)[0].strip()

    def _on_role_changed(self, _role: str) -> None:
        pass  # role only affects the suggested save path, handled by MainWindow

    def _on_connect_clicked(self) -> None:
        if self.connect_btn.text() == "Connect":
            self.connect_requested.emit(self.selected_port())
        else:
            self.disconnect_requested.emit()

    def _on_step(self, step: str) -> None:
        """Emit the step's signal and advance the gate. `finish` is the one
        step that can legitimately be repeated (the save dialog can be
        cancelled), so it leaves itself enabled until MainWindow confirms a
        file was actually written."""
        self._step_signals[step].emit()
        if step != "finish":
            self._next_step = STEP_SEQUENCE[STEP_SEQUENCE.index(step) + 1]
            self._apply_step_gate()

    def _apply_step_gate(self) -> None:
        for name, button in self._step_buttons.items():
            # Step 1 stays live throughout: starting over is always valid, and
            # is the only way out if the user realises mid-recording that they
            # parked the arm somewhere wrong.
            button.setEnabled(name == self._next_step or name == "reset")
        self.step_hint.setText(STEP_NEXT_HINT.get(self._next_step, ""))

    def _set_step_buttons_enabled(self, enabled: bool) -> None:
        if enabled:
            self._next_step = "reset"
            self._apply_step_gate()
        else:
            for button in self._step_buttons.values():
                button.setEnabled(False)
            self.step_hint.setText("Connect to begin.")

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
        self._next_step = None
        self._apply_step_gate()
        QMessageBox.information(
            self, "Saved",
            f"Calibration saved to:\n{path}\n\n"
            "Point the Control tab's Calibration field at this file to start moving the arm.",
        )
