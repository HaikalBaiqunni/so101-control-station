"""
Background worker for assigning CAN IDs to the reBot B601-DM's Damiao motors
- the DM-motor equivalent of setup_worker.py's SO-101/Feetech id assignment,
same reasoning: every Damiao motor ships answering to the same factory
default id (usually 1), so a fully-wired arm is seven motors electrically
on one bus but indistinguishable until each has been given its own id, one
motor at a time.

Deliberately narrow scope (matches ../ReBot_B601_MuJoCo/dm_can_tools, which
this mirrors into the GUI): only reads/writes the ESC_ID (CAN id) and
MST_ID (master id) registers and calls the motor's own save-to-flash
command. Never sends a position/velocity/torque command, so it cannot make
the arm move - unlike SetupWorker's servo bus, there is no calibration
concept here at all, on purpose (see core/robot_profiles.py's
hardware_available=False for this profile - Phase 2 territory).

Runs on its own QThread for the same reason every other bus access in this
app does: register read/write here is several retries of up to ~1s each
(see dm_can.py's read_motor_param/change_motor_param), which has no
business blocking the Qt event loop.
"""
from __future__ import annotations

import queue

import serial
from PySide6.QtCore import QThread, Signal

from .dm_can import DM_Motor_Type, DM_variable, Motor, MotorControl

IDLE_SLEEP_S = 0.05
BAUD = 921600  # matches Damiao's own DM_Tools GUI and DM_Control_Python's own examples


class DmSetupError(Exception):
    pass


class DmSetupWorker(QThread):
    connected = Signal(bool)
    error = Signal(str)
    progress = Signal(str)
    probe_result = Signal(bool, int)       # (found, esc_id currently read back)
    id_assigned = Signal(int, int, int)    # (current_id, new_id, new_master_id)

    def __init__(self, port: str, parent=None):
        super().__init__(parent)
        self.port = port
        self._commands: queue.Queue = queue.Queue()
        self._running = False
        self._ser: serial.Serial | None = None
        self._mc: MotorControl | None = None

    # -- thread-safe public API (call from the GUI thread) -------------------
    def request_probe(self, current_id: int) -> None:
        self._commands.put(("probe", current_id))

    def request_assign(self, current_id: int, new_id: int, new_master_id: int) -> None:
        self._commands.put(("assign", (current_id, new_id, new_master_id)))

    def stop(self) -> None:
        self._running = False

    # -- worker thread body ----------------------------------------------------
    def run(self) -> None:
        try:
            self._ser = serial.Serial(self.port, BAUD, timeout=0.5)
            self._mc = MotorControl(self._ser)
        except (serial.SerialException, OSError) as exc:
            self.error.emit(str(exc))
            self.connected.emit(False)
            return

        self.connected.emit(True)
        self._running = True

        while self._running:
            try:
                command, payload = self._commands.get(timeout=IDLE_SLEEP_S)
            except queue.Empty:
                continue
            try:
                self._handle(command, payload)
            except (serial.SerialException, OSError) as exc:
                self.error.emit(str(exc))

        self._ser.close()
        self.connected.emit(False)

    def _probe_motor(self, motor_id: int) -> Motor:
        """A fresh Motor object per probe/assign - addMotor just registers it
        into the MotorControl's own lookup dict by id, so reusing one across
        different candidate ids would leave stale entries behind rather than
        cleanly representing "which id is currently being tried"."""
        motor = Motor(DM_Motor_Type.DM4310, motor_id, motor_id + 0x10)
        self._mc.addMotor(motor)
        return motor

    def _handle(self, command: str, payload) -> None:
        if command == "probe":
            current_id = payload
            self.progress.emit(f"probing id {current_id:#04x}...")
            motor = self._probe_motor(current_id)
            esc_id = self._mc.read_motor_param(motor, DM_variable.ESC_ID)
            if esc_id is None:
                self.probe_result.emit(False, 0)
                self.progress.emit(
                    f"no response at id {current_id:#04x} - check only ONE motor is "
                    "connected, it is powered, and the id guess is right"
                )
            else:
                self.probe_result.emit(True, int(esc_id))
                self.progress.emit(f"found a motor at id {current_id:#04x}")

        elif command == "assign":
            current_id, new_id, new_master_id = payload
            self.progress.emit(f"assigning id {current_id:#04x} -> {new_id:#04x}...")
            motor = self._probe_motor(current_id)
            ok_id = self._mc.change_motor_param(motor, DM_variable.ESC_ID, new_id)
            ok_master = self._mc.change_motor_param(motor, DM_variable.MST_ID, new_master_id)
            if not (ok_id and ok_master):
                self.error.emit(
                    f"write failed (motor did not confirm) - id {current_id:#04x} was NOT changed"
                )
                return
            self._mc.save_motor_param(motor)

            # Verify by re-reading at the NEW id - proves the change actually
            # stuck in flash rather than trusting the write-confirmation alone.
            verify_motor = self._probe_motor(new_id)
            verify_esc = self._mc.read_motor_param(verify_motor, DM_variable.ESC_ID)
            if verify_esc is None or int(verify_esc) != new_id:
                self.error.emit(
                    f"wrote id {new_id:#04x} but could not verify it by reading it back - "
                    "double-check with DM_Tools before trusting this"
                )
                return

            self.id_assigned.emit(current_id, new_id, new_master_id)
            self.progress.emit(f"saved: id {new_id:#04x}, master id {new_master_id:#04x}")
