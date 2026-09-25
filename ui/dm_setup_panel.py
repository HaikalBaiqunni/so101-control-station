from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
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
    enable_requested = Signal(int)                    # motor_id
    disable_requested = Signal(int)                   # motor_id
    read_pid_requested = Signal(int)                  # motor_id
    write_pid_requested = Signal(int, float, float, float, float)  # motor_id, kp_asr, ki_asr, kp_apr, ki_apr
    set_zero_requested = Signal(int)                  # motor_id
    verify_all_requested = Signal(dict)                # {joint: can_id}
    role_changed = Signal(str)                        # "follower" | "leader"
    control_mode_changed = Signal(str)                # "pos_vel" | "mit"
    mit_gains_changed = Signal(dict)                   # {joint: (kp, kd)}

    def __init__(self, joint_order: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.joint_order = joint_order
        self._probed_id: int | None = None  # last id that successfully answered a probe
        self._mapping: dict[str, dict[str, int]] = {}  # joint -> {"can_id", "master_id"}

        # -- 0: role -------------------------------------------------------------
        # Which PHYSICAL arm this whole panel currently talks about - the
        # follower (B601-DM) or a second, separately-wired Damiao arm used as
        # a teleop leader. Same probe/assign/verify workflow either way, just
        # a different settings key on the MainWindow side (see role_changed) -
        # disabled while connected since switching roles mid-session would
        # otherwise silently relabel whichever physical port is plugged in
        # right now.
        role_box = QGroupBox("0 - WHICH ARM")
        self.role_combo = QComboBox()
        self.role_combo.addItem("Follower (B601-DM arm)", "follower")
        self.role_combo.addItem("Leader (teleop arm)", "leader")
        self.role_combo.currentIndexChanged.connect(
            lambda _i: self.role_changed.emit(self.role_combo.currentData())
        )
        role_layout = QHBoxLayout(role_box)
        role_layout.addWidget(QLabel("Setting up:"))
        role_layout.addWidget(self.role_combo, 1)

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

        # -- 4: motor state & PID ---------------------------------------------------
        # Acts on whichever motor the current Probe succeeded against
        # (self._probed_id) - a bring-up diagnostic, not part of the id
        # workflow above, so it stays usable regardless of which joint the
        # id-assignment section happens to have selected.
        state_box = QGroupBox("4 - MOTOR STATE & PID (optional, diagnostic)")
        state_warning = QLabel(
            "Acts on the motor the last successful Probe found. Enable arms "
            "the motor's own control loop using whatever target it already "
            "has - since this tool never sends a position/velocity/torque "
            "command, that should just mean it holds wherever it currently "
            "is, not that it moves. Disable lets it spin freely by hand."
        )
        state_warning.setObjectName("sectionCaption")
        state_warning.setWordWrap(True)

        self.enable_btn = QPushButton("Enable")
        self.enable_btn.setEnabled(False)
        self.enable_btn.clicked.connect(self._on_enable_clicked)
        self.disable_btn = QPushButton("Disable")
        self.disable_btn.setEnabled(False)
        self.disable_btn.clicked.connect(self._on_disable_clicked)
        self.set_zero_btn = QPushButton("Set Zero")
        self.set_zero_btn.setEnabled(False)
        self.set_zero_btn.setToolTip(
            "Saves the motor's CURRENT physical position as its new zero "
            "reference, to flash - matches DM_Tools' 'SaveZero'. Make sure "
            "the arm is actually where you want zero to be before clicking."
        )
        self.set_zero_btn.clicked.connect(self._on_set_zero_clicked)

        state_row = QHBoxLayout()
        state_row.addWidget(self.enable_btn)
        state_row.addWidget(self.disable_btn)
        state_row.addWidget(self.set_zero_btn)
        state_row.addStretch(1)

        self.pid_spins: dict[str, QDoubleSpinBox] = {}
        pid_grid = QGridLayout()
        for col, label in enumerate(("KP (velocity)", "KI (velocity)", "KP (position)", "KI (position)")):
            pid_grid.addWidget(QLabel(label), 0, col)
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 1000.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.01)
            pid_grid.addWidget(spin, 1, col)
            self.pid_spins[label] = spin

        self.pid_read_btn = QPushButton("Read PID")
        self.pid_read_btn.setEnabled(False)
        self.pid_read_btn.clicked.connect(self._on_read_pid_clicked)
        self.pid_write_btn = QPushButton("Write PID")
        self.pid_write_btn.setEnabled(False)
        self.pid_write_btn.setToolTip(
            "Disabled until Read PID succeeds for this motor - the fields "
            "below start at 0.0000, and writing that blind would zero out "
            "the motor's real gains instead of tuning them."
        )
        self.pid_write_btn.clicked.connect(self._on_write_pid_clicked)

        pid_btn_row = QHBoxLayout()
        pid_btn_row.addWidget(self.pid_read_btn)
        pid_btn_row.addWidget(self.pid_write_btn)
        pid_btn_row.addStretch(1)

        state_layout = QVBoxLayout(state_box)
        state_layout.addWidget(state_warning)
        state_layout.addLayout(state_row)
        state_layout.addLayout(pid_grid)
        state_layout.addLayout(pid_btn_row)

        # -- 5: motion control mode (follower only) --------------------------------
        # Which control mode the Control tab's Connect uses for the FOLLOWER
        # arm - unrelated to the role selector above (the leader is always
        # torque-disabled right after connect, so its control mode has no
        # behavioral effect and always connects in POS_VEL). Takes effect on
        # the next Connect, not live - switching modes needs a fresh
        # switchControlMode() call, which only happens during connect().
        motion_box = QGroupBox("5 - MOTION CONTROL MODE (used by the Control tab's Connect - follower only)")
        motion_warning = QLabel(
            "POS_VEL (default) relies on the motor's own onboard position/"
            "velocity loop - the safe, already-tested mode. MIT sends a fresh "
            "stiffness/damping (kp/kd) with every command instead - these are "
            "NOT the KP/KI gains above (those are persisted onboard registers; "
            "these are per-command only). Start low, tune up gradually while "
            "watching the real arm - do not guess higher blind."
        )
        motion_warning.setObjectName("sectionCaption")
        motion_warning.setWordWrap(True)

        self.control_mode_combo = QComboBox()
        self.control_mode_combo.addItem("POS_VEL (safe default)", "pos_vel")
        self.control_mode_combo.addItem("MIT (needs live tuning)", "mit")
        self.control_mode_combo.currentIndexChanged.connect(self._on_control_mode_changed)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Control mode"))
        mode_row.addWidget(self.control_mode_combo, 1)

        self.mit_gain_spins: dict[str, tuple[QDoubleSpinBox, QDoubleSpinBox]] = {}
        mit_grid = QGridLayout()
        mit_grid.addWidget(QLabel("Joint"), 0, 0)
        mit_grid.addWidget(QLabel("kp"), 0, 1)
        mit_grid.addWidget(QLabel("kd"), 0, 2)
        for row, name in enumerate(self.joint_order, start=1):
            mit_grid.addWidget(QLabel(name), row, 0)
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
            mit_grid.addWidget(kp_spin, row, 1)
            mit_grid.addWidget(kd_spin, row, 2)
            self.mit_gain_spins[name] = (kp_spin, kd_spin)

        motion_layout = QVBoxLayout(motion_box)
        motion_layout.addWidget(motion_warning)
        motion_layout.addLayout(mode_row)
        motion_layout.addLayout(mit_grid)

        # -- checklist ----------------------------------------------------------
        checklist_box = QGroupBox("MOTOR STATUS (all seven must be assigned before Phase 2 control)")
        self.table = QTableWidget(len(self.joint_order), 4)
        self.table.setHorizontalHeaderLabels(["Joint", "CAN id", "Master id", "Verified"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row, name in enumerate(self.joint_order):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem("not assigned"))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            self.table.setItem(row, 3, QTableWidgetItem(""))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

        # Only meaningful once every motor already has its own unique id -
        # at that point all seven can sit on the bus at once (no more
        # one-at-a-time isolation needed), and this reads each one's id back
        # in a single pass to confirm the whole arm's wiring/assignment is
        # actually correct, without unplugging anything again.
        self.verify_all_btn = QPushButton("Verify All (all motors wired + connected)")
        self.verify_all_btn.setEnabled(False)
        self.verify_all_btn.clicked.connect(self._on_verify_all_clicked)

        summary_label = QLabel(
            "Wire one motor, probe, assign, unplug it, chain the next one on and "
            "repeat. This tool only touches the id registers - it never commands "
            "position/velocity/torque, so the arm cannot move because of this."
        )
        summary_label.setObjectName("sectionCaption")
        summary_label.setWordWrap(True)

        checklist_layout = QVBoxLayout(checklist_box)
        checklist_layout.addWidget(self.table)
        checklist_layout.addWidget(self.verify_all_btn)
        checklist_layout.addWidget(summary_label)

        # -- layout --------------------------------------------------------------
        left_column = QVBoxLayout()
        left_column.addWidget(role_box)
        left_column.addWidget(conn_box)
        left_column.addWidget(probe_box)
        left_column.addWidget(assign_box)
        left_column.addWidget(state_box)
        left_column.addWidget(motion_box)
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

    def _on_enable_clicked(self) -> None:
        if self._probed_id is None:
            return
        confirm = QMessageBox.question(
            self, "Confirm enable",
            f"Enable the motor at id {self._probed_id:#04x}?\n\n"
            "This arms its own control loop using whatever target it already "
            "has - this tool never sent it a position/velocity/torque command, "
            "so it should hold wherever it currently is rather than move. "
            "Still, keep clear of the arm and be ready to disable.",
        )
        if confirm == QMessageBox.Yes:
            self.enable_requested.emit(self._probed_id)

    def _on_disable_clicked(self) -> None:
        if self._probed_id is not None:
            self.disable_requested.emit(self._probed_id)

    def _on_set_zero_clicked(self) -> None:
        if self._probed_id is None:
            return
        confirm = QMessageBox.question(
            self, "Confirm set zero",
            f"Save the CURRENT physical position of the motor at id {self._probed_id:#04x} "
            "as its new zero reference, to flash?\n\n"
            "Make sure the arm is actually where you want zero to be first - "
            "this is permanent until set again.",
        )
        if confirm == QMessageBox.Yes:
            self.set_zero_requested.emit(self._probed_id)

    def _on_verify_all_clicked(self) -> None:
        if not self._mapping:
            QMessageBox.warning(
                self, "Nothing assigned yet",
                "No joints have a saved id yet - assign at least one before verifying.",
            )
            return
        for row in range(self.table.rowCount()):
            self.table.item(row, 3).setText("")
        self.verify_all_requested.emit(
            {joint: entry["can_id"] for joint, entry in self._mapping.items()}
        )

    def _on_read_pid_clicked(self) -> None:
        if self._probed_id is not None:
            self.read_pid_requested.emit(self._probed_id)

    def _on_write_pid_clicked(self) -> None:
        if self._probed_id is None:
            return
        values = {label: spin.value() for label, spin in self.pid_spins.items()}
        confirm = QMessageBox.question(
            self, "Confirm PID write",
            f"Write these PID gains to the motor at id {self._probed_id:#04x} and save to flash?\n\n"
            + "\n".join(f"{label}: {value:.4f}" for label, value in values.items()),
        )
        if confirm == QMessageBox.Yes:
            self.write_pid_requested.emit(
                self._probed_id,
                self.pid_spins["KP (velocity)"].value(),
                self.pid_spins["KI (velocity)"].value(),
                self.pid_spins["KP (position)"].value(),
                self.pid_spins["KI (position)"].value(),
            )

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

    def role(self) -> str:
        return self.role_combo.currentData()

    def set_role(self, role: str) -> None:
        index = self.role_combo.findData(role)
        if index >= 0:
            self.role_combo.blockSignals(True)
            self.role_combo.setCurrentIndex(index)
            self.role_combo.blockSignals(False)

    # -- called by MainWindow in response to worker signals --------------------
    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.status_label.setText("CONNECTED" if connected else "DISCONNECTED")
        self.status_label.setObjectName("statusGood" if connected else "statusDanger")
        self.status_label.setStyleSheet("")
        self.role_combo.setEnabled(not connected)
        self.probe_btn.setEnabled(connected)
        self.verify_all_btn.setEnabled(connected and bool(self._mapping))
        if not connected:
            self._probed_id = None
            self.assign_btn.setEnabled(False)
            self.enable_btn.setEnabled(False)
            self.disable_btn.setEnabled(False)
            self.set_zero_btn.setEnabled(False)
            self.pid_read_btn.setEnabled(False)
            self.pid_write_btn.setEnabled(False)
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
        for btn in (self.enable_btn, self.disable_btn, self.set_zero_btn, self.pid_read_btn):
            btn.setEnabled(found)
        # A fresh probe (this motor, or a different one at the same id) means
        # any PID values currently shown are stale/unread for it - Write PID
        # re-locks until a real Read PID confirms what's actually in there.
        # Reset the fields to 0 too rather than leaving the PREVIOUS motor's
        # numbers on screen looking like they still apply to this one.
        self.pid_write_btn.setEnabled(False)
        for spin in self.pid_spins.values():
            spin.setValue(0.0)

    def set_pid_result(self, kp_asr: float, ki_asr: float, kp_apr: float, ki_apr: float) -> None:
        self.pid_spins["KP (velocity)"].setValue(kp_asr)
        self.pid_spins["KI (velocity)"].setValue(ki_asr)
        self.pid_spins["KP (position)"].setValue(kp_apr)
        self.pid_spins["KI (position)"].setValue(ki_apr)
        self.pid_write_btn.setEnabled(True)

    def set_mapping(self, mapping: dict[str, dict[str, int]]) -> None:
        """Full joint->{can_id,master_id} mapping, e.g. loaded from settings
        or refreshed after a successful assign - drives both the checklist
        table and the assign step's auto-suggested next free id."""
        self._mapping = dict(mapping)
        self.verify_all_btn.setEnabled(getattr(self, "_connected", False) and bool(self._mapping))
        for row, name in enumerate(self.joint_order):
            entry = self._mapping.get(name)
            id_item = self.table.item(row, 1)
            master_item = self.table.item(row, 2)
            verified_item = self.table.item(row, 3)
            if entry:
                id_item.setText(f"{entry['can_id']:#04x}")
                id_item.setForeground(QColor(COLORS["good"]))
                master_item.setText(f"{entry['master_id']:#04x}")
            else:
                id_item.setText("not assigned")
                id_item.setForeground(QColor(COLORS["text_muted"]))
                master_item.setText("")
            verified_item.setText("")

    def set_verify_result(self, joint: str, ok: bool) -> None:
        if joint not in self.joint_order:
            return
        row = self.joint_order.index(joint)
        item = self.table.item(row, 3)
        if ok:
            item.setText("OK")
            item.setForeground(QColor(COLORS["good"]))
        else:
            item.setText("NO RESPONSE")
            item.setForeground(QColor(COLORS["danger"]))
        self._suggest_ids()
