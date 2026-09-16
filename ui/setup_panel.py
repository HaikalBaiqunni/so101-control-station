from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
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

from core.servo_bus import BAUDRATE_TABLE, DEFAULT_JOINT_IDS, JOINT_ORDER

from .style import COLORS

WORKING_BAUDRATE = 1_000_000  # what the rest of this app talks at


def describe_ports() -> list[tuple[str, str]]:
    """(device, human label) for every serial port.

    "COM5" alone tells a first-timer nothing - the USB descriptor ("USB-
    Enhanced-SERIAL CH343") is what actually distinguishes the arm's adapter
    from a Bluetooth stack or a printer, and picking the wrong port is the
    single most common reason a new user's first Connect just times out.
    """
    described = []
    for port in list_ports.comports():
        detail = port.description or ""
        if detail and detail != "n/a":
            described.append((port.device, f"{port.device} - {detail}"))
        else:
            described.append((port.device, port.device))
    return described


class SetupPanel(QWidget):
    """First-time motor setup: find the servos, then give each one its own id.

    Every STS3215 ships as id 1, so a fully-wired arm is six servos all
    answering to the same address and colliding on the bus. Nothing else in
    this app - not calibration, not jogging - can work until each has been
    given a distinct id, one servo at a time. This tab is the in-GUI
    equivalent of `lerobot-setup-motors`.
    """

    connect_requested = Signal(str)         # port
    disconnect_requested = Signal()
    scan_requested = Signal(bool)           # True = sweep all baudrates
    deep_scan_requested = Signal()
    assign_id_requested = Signal(int, int)  # current_id, new_id
    set_baudrate_requested = Signal(int, int)  # motor_id, baudrate

    def __init__(self, parent=None):
        super().__init__(parent)

        self._found: dict[int, list[int]] = {}

        # -- 1: connect --------------------------------------------------------
        conn_box = QGroupBox("1 - CONNECT TO THE BUS")
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
            "No calibration file needed here - this stage runs before one exists. "
            "Pick the port whose description mentions a USB serial adapter "
            "(CH340/CH343/CP210x/FTDI); that is the arm."
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

        # -- 2: scan -----------------------------------------------------------
        scan_box = QGroupBox("2 - SCAN FOR SERVOS")
        self.scan_btn = QPushButton("Scan bus (all baudrates)")
        self.scan_btn.clicked.connect(lambda: self.scan_requested.emit(True))
        self.quick_scan_btn = QPushButton("Quick rescan (1 Mbps)")
        self.quick_scan_btn.clicked.connect(lambda: self.scan_requested.emit(False))
        self.deep_scan_btn = QPushButton("Deep scan (ids 0-253)")
        self.deep_scan_btn.setToolTip(
            "Pings every possible address at the current baudrate. Slow (~5s),\n"
            "and only needed if a servo was previously given an id above 20."
        )
        self.deep_scan_btn.clicked.connect(self.deep_scan_requested)

        self.progress_label = QLabel("not scanned yet")
        self.progress_label.setObjectName("sectionCaption")
        self.progress_label.setWordWrap(True)

        scan_row = QHBoxLayout()
        scan_row.addWidget(self.scan_btn)
        scan_row.addWidget(self.quick_scan_btn)
        scan_row.addWidget(self.deep_scan_btn)
        scan_row.addStretch(1)

        scan_layout = QVBoxLayout(scan_box)
        scan_layout.addLayout(scan_row)
        scan_layout.addWidget(self.progress_label)

        # -- 3: assign ---------------------------------------------------------
        assign_box = QGroupBox("3 - GIVE THE CONNECTED SERVO ITS ID")
        assign_warning = QLabel(
            "Plug in exactly ONE servo for this step. Every servo answers to "
            "id 1 out of the box, so with several on the bus they would all "
            "take the new id at once and stay indistinguishable. Assign is "
            "only enabled while the scan sees a single servo."
        )
        assign_warning.setObjectName("sectionCaption")
        assign_warning.setWordWrap(True)

        self.target_combo = QComboBox()
        for name in JOINT_ORDER:
            self.target_combo.addItem(f"{name}  (id {DEFAULT_JOINT_IDS[name]})", DEFAULT_JOINT_IDS[name])

        self.assign_btn = QPushButton("Assign this id to the connected servo")
        self.assign_btn.clicked.connect(self._on_assign_clicked)

        self.baud_combo = QComboBox()
        for rate in sorted(BAUDRATE_TABLE.values(), reverse=True):
            self.baud_combo.addItem(f"{rate:,} baud".replace(",", " "), rate)
        self.baud_btn = QPushButton("Set servo baudrate")
        self.baud_btn.setToolTip(
            "Only needed if the scan found a servo at something other than\n"
            "1 Mbps. The rest of this app talks at 1 Mbps exclusively."
        )
        self.baud_btn.clicked.connect(self._on_set_baudrate_clicked)

        assign_row = QHBoxLayout()
        assign_row.addWidget(QLabel("This servo is the"))
        assign_row.addWidget(self.target_combo)
        assign_row.addWidget(self.assign_btn)
        assign_row.addStretch(1)

        baud_row = QHBoxLayout()
        baud_row.addWidget(QLabel("Move it to"))
        baud_row.addWidget(self.baud_combo)
        baud_row.addWidget(self.baud_btn)
        baud_row.addStretch(1)

        assign_layout = QVBoxLayout(assign_box)
        assign_layout.addWidget(assign_warning)
        assign_layout.addLayout(assign_row)
        assign_layout.addLayout(baud_row)

        # -- checklist ----------------------------------------------------------
        checklist_box = QGroupBox("ARM STATUS (all six must be present before calibrating)")
        self.table = QTableWidget(len(JOINT_ORDER), 3)
        self.table.setHorizontalHeaderLabels(["Joint", "Expected id", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, name in enumerate(JOINT_ORDER):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(str(DEFAULT_JOINT_IDS[name])))
            self.table.setItem(row, 2, QTableWidgetItem("not scanned"))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

        self.summary_label = QLabel(
            "Order matters only in that each joint needs the id listed above - "
            "shoulder_pan is 1, gripper is 6. Wire one servo, scan, assign, "
            "then chain the next one on and repeat."
        )
        self.summary_label.setObjectName("sectionCaption")
        self.summary_label.setWordWrap(True)

        checklist_layout = QVBoxLayout(checklist_box)
        checklist_layout.addWidget(self.table)
        checklist_layout.addWidget(self.summary_label)

        # -- layout --------------------------------------------------------------
        left_column = QVBoxLayout()
        left_column.addWidget(conn_box)
        left_column.addWidget(scan_box)
        left_column.addWidget(assign_box)
        left_column.addStretch(1)

        root = QHBoxLayout(self)
        root.addLayout(left_column, 1)
        root.addWidget(checklist_box, 1)

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
        """The combo shows a descriptive label ("COM5 - USB-SERIAL CH340") but
        the SDK needs the bare device name. Picked entries carry it as item
        data; a hand-typed entry is taken as-is (minus any description the
        user pasted along with it)."""
        text = self.port_combo.currentText().strip()
        index = self.port_combo.findText(text)
        if index >= 0:
            return self.port_combo.itemData(index)
        return text.split(" - ", 1)[0].strip()

    def _on_connect_clicked(self) -> None:
        if self.connect_btn.text() == "Connect":
            self.connect_requested.emit(self.selected_port())
        else:
            self.disconnect_requested.emit()

    def _single_found_id(self) -> int | None:
        """The one servo on the bus, or None if there are zero or several -
        which is exactly the condition an id assignment is safe under."""
        ids = [i for id_list in self._found.values() for i in id_list]
        return ids[0] if len(ids) == 1 else None

    def _on_assign_clicked(self) -> None:
        current_id = self._single_found_id()
        if current_id is None:
            QMessageBox.warning(
                self, "Not exactly one servo",
                "Assigning an id is only safe with a single servo on the bus.\n\n"
                "Scan again with just the servo you want to configure connected.",
            )
            return
        new_id = self.target_combo.currentData()
        joint = JOINT_ORDER[self.target_combo.currentIndex()]
        if current_id == new_id:
            QMessageBox.information(
                self, "Already set", f"The connected servo is already id {new_id} ({joint})."
            )
            return
        confirm = QMessageBox.question(
            self, "Confirm id change",
            f"Change the connected servo from id {current_id} to id {new_id} ({joint})?\n\n"
            "This writes the servo's EEPROM and is permanent until changed again.",
        )
        if confirm == QMessageBox.Yes:
            self.assign_id_requested.emit(current_id, new_id)

    def _on_set_baudrate_clicked(self) -> None:
        motor_id = self._single_found_id()
        if motor_id is None:
            QMessageBox.warning(
                self, "Not exactly one servo",
                "Changing a baudrate is only safe with a single servo on the bus.",
            )
            return
        self.set_baudrate_requested.emit(motor_id, self.baud_combo.currentData())

    # -- called by MainWindow in response to worker signals --------------------
    def set_connected(self, connected: bool) -> None:
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.status_label.setText("CONNECTED" if connected else "DISCONNECTED")
        self.status_label.setObjectName("statusGood" if connected else "statusDanger")
        self.status_label.setStyleSheet("")
        for widget in (self.scan_btn, self.quick_scan_btn, self.deep_scan_btn):
            widget.setEnabled(connected)
        if not connected:
            self._found = {}
            self._apply_found_to_table()
        self._update_assign_enabled()

    def set_progress(self, text: str) -> None:
        self.progress_label.setText(text)

    def set_scan_result(self, found: dict[int, list[int]]) -> None:
        self._found = found
        self._apply_found_to_table()
        self._update_assign_enabled()
        self._preselect_next_missing()

    # ---------------------------------------------------------------- internals
    def _apply_found_to_table(self) -> None:
        found_ids = {i: rate for rate, ids in self._found.items() for i in ids}
        for row, name in enumerate(JOINT_ORDER):
            expected = DEFAULT_JOINT_IDS[name]
            item = self.table.item(row, 2)
            if not self._found:
                item.setText("not scanned")
                item.setForeground(QColor(COLORS["text_muted"]))
            elif found_ids.get(expected) == WORKING_BAUDRATE:
                item.setText("OK")
                item.setForeground(QColor(COLORS["good"]))
            elif expected in found_ids:
                item.setText(f"found, but at {found_ids[expected]} baud")
                item.setForeground(QColor(COLORS["warn"]))
            else:
                item.setText("missing")
                item.setForeground(QColor(COLORS["danger"]))

        stray = sorted(i for i in found_ids if i not in DEFAULT_JOINT_IDS.values())
        if stray:
            self.summary_label.setText(
                f"Also on the bus at unexpected id(s): {', '.join(map(str, stray))}. "
                "That is normal for a servo that has not been assigned yet - "
                "a brand-new one answers to id 1."
            )

    def _update_assign_enabled(self) -> None:
        single = self._single_found_id() is not None
        self.assign_btn.setEnabled(single)
        self.baud_btn.setEnabled(single)

    def _preselect_next_missing(self) -> None:
        """Point the target dropdown at the first joint still missing, so the
        common case (working through 1..6 in order) needs no dropdown fiddling
        at all."""
        found_ids = {i for ids in self._found.values() for i in ids}
        for index, name in enumerate(JOINT_ORDER):
            if DEFAULT_JOINT_IDS[name] not in found_ids:
                self.target_combo.setCurrentIndex(index)
                return
