from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .setup_panel import describe_ports
from .style import COLORS


class DmSetupPanel(QWidget):
    """First-time CAN id setup for the reBot B601-DM's Damiao motors - the
    DM equivalent of SetupPanel (SO-101/Feetech). Every DM motor ships
    answering to the same factory default id, so a fully-wired arm is seven
    motors all colliding on the bus until each is given a distinct id, one
    motor at a time.

    Deliberately narrower than SetupPanel: no baudrate step (Damiao's link
    speed is fixed - see dm_setup_worker.BAUD) and no bus-wide scan (the DM
    register protocol addresses a motor by an id you already believe it has,
    there is no broadcast-and-see-who-answers primitive) - "probe" plays
    that role instead, one guessed id at a time.
    """

    connect_requested = Signal(str)                 # port
    disconnect_requested = Signal()
    probe_requested = Signal(int)                    # current_id guess
    assign_requested = Signal(int, int, int)          # current_id, new_id, new_master_id

    def __init__(self, joint_order: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.joint_order = joint_order
        self._probed_id: int | None = None  # last id that successfully answered a probe
        self._mapping: dict[str, dict[str, int]] = {}  # joint -> {"can_id", "master_id"}

        # -- 1: connect --------------------------------------------------------
        conn_box = QGroupBox("1 - CONNECT TO THE USB-SERIAL ADAPTER")
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self._refresh_ports()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_ports)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        self.status_label = QLabel("DISCONNECTED")
        self.status_label.setObjectName("statusDanger")

        conn_hint = QLabel(
            "Same USB-serial adapter Damiao's own DM_Tools uses (921 600 baud, "
            "fixed - no baudrate step needed here)."
        )
        conn_hint.setObjectName("sectionCaption")
        conn_hint.setWordWrap(True)

        conn_layout = QGridLayout(conn_box)
        conn_layout.addWidget(QLabel("Port"), 0, 0)
        conn_layout.addWidget(self.port_combo, 0, 1)
        conn_layout.addWidget(refresh_btn, 0, 2)
        conn_layout.addWidget(self.connect_btn, 1, 0, 1, 2)
        conn_layout.addWidget(self.status_label, 1, 2)
        conn_layout.addWidget(conn_hint, 2, 0, 1, 3)
        conn_layout.setColumnStretch(1, 1)

        # -- 2: probe ------------------------------------------------------------
        probe_box = QGroupBox("2 - FIND THE CONNECTED MOTOR")
        probe_warning = QLabel(
            "Plug in exactly ONE motor for this step - Damiao motors usually "
            "ship at the same factory default id, so with several connected "
            "a probe cannot tell them apart. NO HOT-PLUGGING: cut power "
            "before connecting/disconnecting the XT30 2+2 interface, per "
            "Seeed's own reBot B601-DM safety instructions - unlike "
            "Feetech's servos, this connector is not rated for it."
        )
        probe_warning.setObjectName("sectionCaption")
        probe_warning.setWordWrap(True)

        self.current_id_edit = QLineEdit("01")
        self.current_id_edit.setMaximumWidth(60)
        self.current_id_edit.setToolTip("Hex - Damiao's usual factory default is 01")
        self.probe_btn = QPushButton("Probe")
        self.probe_btn.clicked.connect(self._on_probe_clicked)

        self.probe_result_label = QLabel("not probed yet")
        self.probe_result_label.setObjectName("sectionCaption")

        probe_row = QHBoxLayout()
        probe_row.addWidget(QLabel("Current id (hex)"))
        probe_row.addWidget(self.current_id_edit)
        probe_row.addWidget(self.probe_btn)
        probe_row.addWidget(self.probe_result_label, 1)

        probe_layout = QVBoxLayout(probe_box)
        probe_layout.addWidget(probe_warning)
        probe_layout.addLayout(probe_row)

        # -- 3: assign -------------------------------------------------------------
        assign_box = QGroupBox("3 - GIVE IT A UNIQUE ID")
        self.target_combo = QComboBox()
        self.target_combo.addItems(list(self.joint_order))

        self.new_id_edit = QLineEdit()
        self.new_id_edit.setMaximumWidth(60)
        self.new_master_edit = QLineEdit()
        self.new_master_edit.setMaximumWidth(60)
        self.target_combo.currentIndexChanged.connect(self._suggest_ids)

        self.assign_btn = QPushButton("Assign to the connected motor")
        self.assign_btn.setEnabled(False)
        self.assign_btn.clicked.connect(self._on_assign_clicked)

        assign_row = QGridLayout()
        assign_row.addWidget(QLabel("This motor is the"), 0, 0)
        assign_row.addWidget(self.target_combo, 0, 1)
        assign_row.addWidget(QLabel("New id (hex)"), 1, 0)
        assign_row.addWidget(self.new_id_edit, 1, 1)
        assign_row.addWidget(QLabel("New master id (hex)"), 2, 0)
        assign_row.addWidget(self.new_master_edit, 2, 1)
        assign_row.addWidget(self.assign_btn, 3, 0, 1, 2)

        assign_layout = QVBoxLayout(assign_box)
        assign_layout.addLayout(assign_row)

        # -- checklist ----------------------------------------------------------
        checklist_box = QGroupBox("MOTOR STATUS (all seven must be assigned before Phase 2 control)")
        self.table = QTableWidget(len(self.joint_order), 3)
        self.table.setHorizontalHeaderLabels(["Joint", "CAN id", "Master id"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, name in enumerate(self.joint_order):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem("not assigned"))
            self.table.setItem(row, 2, QTableWidgetItem(""))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

        summary_label = QLabel(
            "Wire one motor, probe, assign, unplug it, chain the next one on and "
            "repeat. This tool only touches the id registers - it never commands "
            "position/velocity/torque, so the arm cannot move because of this."
        )
        summary_label.setObjectName("sectionCaption")
        summary_label.setWordWrap(True)

        checklist_layout = QVBoxLayout(checklist_box)
        checklist_layout.addWidget(self.table)
        checklist_layout.addWidget(summary_label)

        # -- layout --------------------------------------------------------------
        left_column = QVBoxLayout()
        left_column.addWidget(conn_box)
        left_column.addWidget(probe_box)
        left_column.addWidget(assign_box)
        left_column.addStretch(1)

        root = QHBoxLayout(self)
        root.addLayout(left_column, 1)
        root.addWidget(checklist_box, 1)

        self._suggest_ids()
        self.set_connected(False)

    # ---------------------------------------------------------------- helpers
    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        for device, label in describe_ports():
            self.port_combo.addItem(label, device)
        if current:
            self.port_combo.setEditText(current)

    def selected_port(self) -> str:
        text = self.port_combo.currentText().strip()
        index = self.port_combo.findText(text)
        if index >= 0:
            return self.port_combo.itemData(index)
        return text.split(" - ", 1)[0].strip()

    def _parse_hex(self, edit: QLineEdit, label: str) -> int | None:
        text = edit.text().strip()
        try:
            return int(text, 16)
        except ValueError:
            QMessageBox.warning(self, "Not a valid id", f"{label} must be a hex number, e.g. 01.")
            return None

    def _suggest_ids(self) -> None:
        joint = self.target_combo.currentText()
        existing = self._mapping.get(joint)
        if existing:
            new_id = existing["can_id"]
        else:
            # Seeed's own published scheme for this exact robot (reBot
            # B601-DM Quick Start wiki): CAN id = the motor's 1-based
            # position in the arm (motors 1-3 are J4340P at 0x01-0x03,
            # motors 4-7 are J4310 at 0x04-0x07) - joint_order here is
            # already in that same physical order (joint1..joint6,
            # finger_left), so this is a direct index lookup, not a guess.
            new_id = self.joint_order.index(joint) + 1
        self.new_id_edit.setText(f"{new_id:02x}")
        self.new_master_edit.setText(f"{(new_id + 0x10):02x}")

    def _on_connect_clicked(self) -> None:
        if self.connect_btn.text() == "Connect":
            self.connect_requested.emit(self.selected_port())
        else:
            self.disconnect_requested.emit()

    def _on_probe_clicked(self) -> None:
        current_id = self._parse_hex(self.current_id_edit, "Current id")
        if current_id is None:
            return
        self.probe_result_label.setText("probing...")
        self.probe_btn.setEnabled(False)
        self.probe_requested.emit(current_id)

    def _on_assign_clicked(self) -> None:
        if self._probed_id is None:
            QMessageBox.warning(
                self, "Probe first",
                "Probe the connected motor successfully before assigning it a new id.",
            )
            return
        new_id = self._parse_hex(self.new_id_edit, "New id")
        new_master = self._parse_hex(self.new_master_edit, "New master id")
        if new_id is None or new_master is None:
            return
        if new_id == 0:
            QMessageBox.warning(self, "Invalid id", "CAN id 0 is reserved/invalid.")
            return
        if new_master == 0 or new_master == new_id:
            QMessageBox.warning(
                self, "Invalid master id", "Master id must be non-zero and different from the CAN id."
            )
            return
        joint = self.target_combo.currentText()
        taken_by = next(
            (j for j, e in self._mapping.items() if e["can_id"] == new_id and j != joint), None
        )
        if taken_by:
            QMessageBox.warning(
                self, "Id already used", f"CAN id {new_id:#04x} is already assigned to '{taken_by}'."
            )
            return
        confirm = QMessageBox.question(
            self, "Confirm id change",
            f"Change the connected motor from id {self._probed_id:#04x} to id {new_id:#04x} "
            f"(master {new_master:#04x}), as '{joint}'?\n\n"
            "This writes the motor's flash and is permanent until changed again.",
        )
        if confirm == QMessageBox.Yes:
            self.assign_requested.emit(self._probed_id, new_id, new_master)

    # -- called by MainWindow in response to worker signals --------------------
    def set_connected(self, connected: bool) -> None:
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.status_label.setText("CONNECTED" if connected else "DISCONNECTED")
        self.status_label.setObjectName("statusGood" if connected else "statusDanger")
        self.status_label.setStyleSheet("")
        self.probe_btn.setEnabled(connected)
        if not connected:
            self._probed_id = None
            self.assign_btn.setEnabled(False)
            self.probe_result_label.setText("not probed yet")

    def set_progress(self, text: str) -> None:
        pass  # surfaced via MainWindow's status bar - no dedicated widget here, unlike SetupPanel

    def set_probe_result(self, found: bool, esc_id: int) -> None:
        self.probe_btn.setEnabled(True)
        if found:
            self._probed_id = esc_id
            self.probe_result_label.setText(f"found - responds at id {esc_id:#04x}")
            self.probe_result_label.setStyleSheet(f"color: {COLORS['good']}")
        else:
            self._probed_id = None
            self.probe_result_label.setText("no response - check connection/id")
            self.probe_result_label.setStyleSheet(f"color: {COLORS['danger']}")
        self.assign_btn.setEnabled(found)

    def set_mapping(self, mapping: dict[str, dict[str, int]]) -> None:
        """Full joint->{can_id,master_id} mapping, e.g. loaded from settings
        or refreshed after a successful assign - drives both the checklist
        table and the assign step's auto-suggested next free id."""
        self._mapping = dict(mapping)
        for row, name in enumerate(self.joint_order):
            entry = self._mapping.get(name)
            id_item = self.table.item(row, 1)
            master_item = self.table.item(row, 2)
            if entry:
                id_item.setText(f"{entry['can_id']:#04x}")
                id_item.setForeground(QColor(COLORS["good"]))
                master_item.setText(f"{entry['master_id']:#04x}")
            else:
                id_item.setText("not assigned")
                id_item.setForeground(QColor(COLORS["text_muted"]))
                master_item.setText("")
        self._suggest_ids()
