"""
Background worker for setting up the reBot B601-DM's Damiao motors before
real hardware control exists for this profile (see core/robot_profiles.py's
hardware_available=False - Phase 2 territory) - id assignment, plus
enable/disable and PID gain read/write for bring-up diagnostics, the DM
equivalent of setup_worker.py's SO-101/Feetech id assignment (every Damiao
motor ships answering to the same factory default id, so a fully-wired arm
is seven motors electrically on one bus but indistinguishable until each
has been given its own id, one motor at a time).

Still deliberately narrow: nothing here ever sends a position/velocity/
torque TARGET. enable()/disable() (0xFC/0xFD control-command frames, see
dm_can.py) arm/disarm the motor's own internal control loop using whatever
target it already has - since this app never sends one, enabling should
just make it hold wherever it currently is, not move it. PID gain writes
are plain register values with no motion of their own either. Real
closed-loop position/velocity control (a DamiaoBus driver wired into
RobotWorker) is still Phase 2, not this file.

Runs on its own QThread for the same reason every other bus access in this
app does: register read/write here is several retries of up to ~1s each
(see dm_can.py's read_motor_param/change_motor_param), which has no
business blocking the Qt event loop.
"""
from __future__ import annotations

import queue

import serial
from PySide6.QtCore import QThread, Signal

from .dm_can import Control_Type, DM_Motor_Type, DM_variable, Motor, MotorControl

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
    enabled_changed = Signal(bool)         # (True = enable sent, False = disable sent)
    pid_result = Signal(float, float, float, float)  # (kp_asr, ki_asr, kp_apr, ki_apr)
    pid_written = Signal()
    zero_set = Signal()
    verify_result = Signal(str, bool)      # (joint, responded correctly at its assigned id)
    verify_done = Signal()

    def __init__(self, port: str, parent=None):
        super().__init__(parent)
        self.port = port
        self._commands: queue.Queue = queue.Queue()
        # A plain flag that stop() sets and run()'s loop condition checks -
        # deliberately never written back to False->True anywhere else (see
        # stop()/run() below - this is the exact race TwinWorker had: run()
        # used to set self._running = True unconditionally after opening the
        # serial port, which could CLOBBER a stop() that arrived while the
        # port was still opening, leaving the loop running forever with the
        # COM port never released even though the UI already said
        # "DISCONNECTED" - confirmed as the real cause of a port staying
        # locked across Disconnect, blocking DM_Tools from opening it too).
        self._stop_requested = False
        self._ser: serial.Serial | None = None
        self._mc: MotorControl | None = None

    # -- thread-safe public API (call from the GUI thread) -------------------
    def request_probe(self, current_id: int) -> None:
        self._commands.put(("probe", current_id))

    def request_assign(self, current_id: int, new_id: int, new_master_id: int) -> None:
        self._commands.put(("assign", (current_id, new_id, new_master_id)))

    def request_enable(self, motor_id: int) -> None:
        self._commands.put(("enable", motor_id))

    def request_disable(self, motor_id: int) -> None:
        self._commands.put(("disable", motor_id))

    def request_read_pid(self, motor_id: int) -> None:
        self._commands.put(("read_pid", motor_id))

    def request_set_zero(self, motor_id: int) -> None:
        self._commands.put(("set_zero", motor_id))

    def request_verify_all(self, id_by_joint: dict[str, int]) -> None:
        """id_by_joint: {joint: can_id} for every joint that has a saved
        assignment - reads each one's ESC_ID in turn over ONE connection.
        Only meaningful once every motor has its own unique id: with all
        seven wired to the same bus simultaneously (no more one-at-a-time
        isolation needed, unlike Probe/Assign above), each id only ever gets
        a reply from the ONE motor that actually owns it."""
        self._commands.put(("verify_all", dict(id_by_joint)))

    def request_write_pid(
        self, motor_id: int, kp_asr: float, ki_asr: float, kp_apr: float, ki_apr: float
    ) -> None:
        self._commands.put(("write_pid", (motor_id, kp_asr, ki_asr, kp_apr, ki_apr)))

    def stop(self) -> None:
        self._stop_requested = True

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

        # See _stop_requested's own comment - opening the serial port above
        # can take long enough for a stop() to have already arrived by the
        # time we get here, and the loop condition below reads the flag
        # fresh on every pass with nothing in this method ever writing it
        # back to False, so that stop() is honored immediately instead of
        # being silently overwritten.
        while not self._stop_requested:
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

        elif command == "enable":
            motor_id = payload
            motor = self._probe_motor(motor_id)
            # CTRL_MODE (RID 10) is a separate persisted register from
            # anything id-assignment ever touches - confirmed directly in
            # dm_can.py: enable_old()'s own arbitration id even ENCODES the
            # control mode into it. A motor whose mode was never explicitly
            # set (e.g. straight from the id-assignment step, which never
            # calls switchControlMode) may not arm from a bare enable() the
            # way it does from DM_Tools, which always has an active mode
            # selected. MIT is the base/universal mode, and setting it alone
            # sends no position/velocity/torque target of its own, so this
            # doesn't change the "should hold still" safety property.
            self._mc.switchControlMode(motor, Control_Type.MIT)
            self._mc.enable(motor)
            self.enabled_changed.emit(True)
            self.progress.emit(f"enabled id {motor_id:#04x} - holding wherever it currently is")

        elif command == "set_zero":
            motor_id = payload
            motor = self._probe_motor(motor_id)
            self._mc.set_zero_position(motor)
            self.zero_set.emit()
            self.progress.emit(f"set zero position for id {motor_id:#04x}")

        elif command == "verify_all":
            id_by_joint = payload
            self.progress.emit(f"verifying {len(id_by_joint)} motor(s) on the shared bus...")
            for joint, expected_id in id_by_joint.items():
                motor = self._probe_motor(expected_id)
                esc_id = self._mc.read_motor_param(motor, DM_variable.ESC_ID)
                ok = esc_id is not None and int(esc_id) == expected_id
                self.verify_result.emit(joint, ok)
            self.verify_done.emit()
            self.progress.emit("verify all done")

        elif command == "disable":
            motor_id = payload
            motor = self._probe_motor(motor_id)
            self._mc.disable(motor)
            self.enabled_changed.emit(False)
            self.progress.emit(f"disabled id {motor_id:#04x} - free to move by hand now")

        elif command == "read_pid":
            motor_id = payload
            motor = self._probe_motor(motor_id)
            kp_asr = self._mc.read_motor_param(motor, DM_variable.KP_ASR)
            ki_asr = self._mc.read_motor_param(motor, DM_variable.KI_ASR)
            kp_apr = self._mc.read_motor_param(motor, DM_variable.KP_APR)
            ki_apr = self._mc.read_motor_param(motor, DM_variable.KI_APR)
            if None in (kp_asr, ki_asr, kp_apr, ki_apr):
                self.error.emit(f"could not read PID gains from id {motor_id:#04x} - check connection")
                return
            self.pid_result.emit(float(kp_asr), float(ki_asr), float(kp_apr), float(ki_apr))
            self.progress.emit(f"read PID gains from id {motor_id:#04x}")

        elif command == "write_pid":
            motor_id, kp_asr, ki_asr, kp_apr, ki_apr = payload
            motor = self._probe_motor(motor_id)
            ok = all((
                self._mc.change_motor_param(motor, DM_variable.KP_ASR, kp_asr),
                self._mc.change_motor_param(motor, DM_variable.KI_ASR, ki_asr),
                self._mc.change_motor_param(motor, DM_variable.KP_APR, kp_apr),
                self._mc.change_motor_param(motor, DM_variable.KI_APR, ki_apr),
            ))
            if not ok:
                self.error.emit(f"PID write failed (motor did not confirm) for id {motor_id:#04x}")
                return
            self._mc.save_motor_param(motor)
            self.pid_written.emit()
            self.progress.emit(f"saved PID gains for id {motor_id:#04x}")
