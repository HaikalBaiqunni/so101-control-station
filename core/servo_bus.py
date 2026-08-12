"""
Minimal, standalone Feetech STS3215 bus driver for the SO-101 arm.

Deliberately independent of the full LeRobot package (which pulls in PyTorch
and friends) - this only needs `feetech-servo-sdk` + `pyserial`, so the GUI
stays light enough to hand to someone on a bare Windows/Linux/Mac machine.

Register map and sign-magnitude encoding verified against LeRobot's own
`lerobot/motors/feetech/tables.py` and `encoding_utils.py` (Apache-2.0),
re-implemented here rather than imported to avoid the dependency.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

import scservo_sdk as scs

BAUDRATE = 1_000_000
MODEL_RESOLUTION = 4096  # STS3215: 12-bit encoder
MAX_RES = MODEL_RESOLUTION - 1  # 4095
EEPROM_WRITE_SETTLE_S = 0.02  # STS3215 needs a beat to finish an EEPROM write
                              # before it can ack the next packet - back-to-back
                              # EEPROM writes with no gap intermittently drop the
                              # status reply ("There is no status packet!"),
                              # always on a different id, which is exactly this.
WRITE_RETRIES = 3

# data_name: (address, size_bytes, sign_bit_index_or_None)
REGISTERS = {
    # -- EEPROM (persistent; see EEPROM_WRITE_SETTLE_S) --
    "ID": (5, 1, None),              # a servo's own bus address - what setup_worker rewrites
    "Baud_Rate": (6, 1, None),       # stored as an INDEX into BAUDRATE_TABLE, not the rate itself
    "Min_Position_Limit": (9, 2, None),
    "Max_Position_Limit": (11, 2, None),
    "CW_Dead_Zone": (26, 1, None),   # position deadband: inside it the servo stops
    "CCW_Dead_Zone": (27, 1, None),  # correcting, so it directly caps repeatability
    "Homing_Offset": (31, 2, 11),
    "Operating_Mode": (33, 1, None),
    # -- SRAM (volatile, cheap to write, safe in a fast loop) --
    "Torque_Enable": (40, 1, None),
    "Acceleration": (41, 1, None),
    "Goal_Position": (42, 2, 15),
    "Goal_Velocity": (46, 2, None),
    "Torque_Limit": (48, 2, None),   # servo-side output cap - lets the servo itself
                                     # hold a constant grip force in its own fast
                                     # loop, instead of us chasing a current
                                     # threshold from a 60Hz polling loop
    "Lock": (55, 1, None),           # EEPROM write guard: must be 0 to change ID/Baud_Rate,
                                     # back to 1 afterwards. Matches how LeRobot's own Feetech
                                     # _disable_torque/_enable_torque bracket every EEPROM write.
    # -- read-only feedback. 56..63 is one contiguous block, which read_telemetry()
    #    exploits to fetch all five in a single bus transaction.
    "Present_Position": (56, 2, None),
    "Present_Velocity": (58, 2, None),
    "Present_Load": (60, 2, None),
    "Present_Voltage": (62, 1, None),
    "Present_Temperature": (63, 1, None),
    "Present_Current": (69, 2, None),
}
OPERATING_MODE_POSITION = 0

# Baud_Rate (reg 6) stores an INDEX, not a rate. Same table as LeRobot's
# STS_SMS_SERIES_BAUDRATE_TABLE. Index 0 (1 Mbps) is what SO-101 kits ship at
# and what this app runs at; the rest exist so a bus scan can still FIND a
# servo somebody previously reconfigured, and offer to put it back.
BAUDRATE_TABLE = {0: 1_000_000, 1: 500_000, 2: 250_000, 3: 128_000,
                  4: 115_200, 5: 57_600, 6: 38_400, 7: 19_200}
BAUDRATE_INDEX = {rate: index for index, rate in BAUDRATE_TABLE.items()}

BROADCAST_ID = 254  # reserved by the protocol - never assignable to a real servo
MAX_SERVO_ID = 253
# A failed ping costs the full serial timeout (~20ms), so sweeping all 254
# addresses at all 8 baudrates would be a ~40s "is it frozen?" stall. 0..20
# covers the six real joints plus any plausible mis-assignment, and finishes
# in a few seconds; the full 0..253 sweep stays available as an explicit
# opt-in at ONE baudrate (see SetupWorker.request_deep_scan).
DEFAULT_SCAN_MAX_ID = 20

# read_telemetry()'s two contiguous read blocks: (start_address, byte_length).
# Present_Current sits at 69, past Status/Moving, so it can't join the 56..63 run.
TELEMETRY_BLOCK = (56, 8)
CURRENT_BLOCK = (69, 2)

# STS3215 memory map: addresses below Torque_Enable (40) are EEPROM, the rest
# is RAM. EEPROM writes physically take longer to settle than RAM writes -
# see EEPROM_WRITE_SETTLE_S.
_EEPROM_REGISTERS = {name for name, (addr, _size, _sign) in REGISTERS.items() if addr < 40}

# standard SO-101 joint order, used everywhere a calibration file doesn't override it
DEFAULT_JOINT_IDS = {
    "shoulder_pan": 1,
    "shoulder_lift": 2,
    "elbow_flex": 3,
    "wrist_flex": 4,
    "wrist_roll": 5,
    "gripper": 6,
}
JOINT_ORDER = list(DEFAULT_JOINT_IDS.keys())


def encode_sign_magnitude(value: int, sign_bit_index: int) -> int:
    max_magnitude = (1 << sign_bit_index) - 1
    magnitude = abs(value)
    if magnitude > max_magnitude:
        raise ValueError(f"Magnitude {magnitude} exceeds {max_magnitude} for sign_bit_index={sign_bit_index}")
    direction_bit = 1 if value < 0 else 0
    return (direction_bit << sign_bit_index) | magnitude


def decode_sign_magnitude(encoded_value: int, sign_bit_index: int) -> int:
    direction_bit = (encoded_value >> sign_bit_index) & 1
    magnitude = encoded_value & ((1 << sign_bit_index) - 1)
    return -magnitude if direction_bit else magnitude


@dataclass
class MotorCalibration:
    id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int

    @property
    def mid(self) -> float:
        return (self.range_min + self.range_max) / 2


class ServoBusError(RuntimeError):
    pass


class ServoBus:
    """
    One physical RS-485/TTL bus (one USB-serial port) with up to 6 STS3215
    servos on it, addressed by joint name via a loaded calibration file.
    """

    def __init__(self, port: str, baudrate: int = BAUDRATE):
        self.port_name = port
        self.baudrate = baudrate
        self.port_handler = scs.PortHandler(port)
        self.packet_handler = scs.PacketHandler(0)
        self.calibration: dict[str, MotorCalibration] = {}
        self._lock = threading.Lock()
        self._connected = False
        # cached sync-read/write handles - one bus transaction for ALL motors
        # instead of one round trip per motor, this is what makes lerobot's
        # own teleoperate loop hit 60Hz; naive per-joint reads/writes do not.
        pos_addr, pos_size, _ = REGISTERS["Present_Position"]
        self._sync_reader = scs.GroupSyncRead(self.port_handler, self.packet_handler, pos_addr, pos_size)
        # separate readers for the diagnostic telemetry - deliberately NOT folded
        # into the 60Hz position read, so adding telemetry can never slow the
        # control loop (read_telemetry is called at a much lower rate)
        self._sync_reader_tel = scs.GroupSyncRead(self.port_handler, self.packet_handler, *TELEMETRY_BLOCK)
        self._sync_reader_cur = scs.GroupSyncRead(self.port_handler, self.packet_handler, *CURRENT_BLOCK)
        goal_addr, goal_size, _ = REGISTERS["Goal_Position"]
        self._sync_writer = scs.GroupSyncWrite(self.port_handler, self.packet_handler, goal_addr, goal_size)

    # ---------------------------------------------------------------- connection
    def connect(self) -> None:
        if not self.port_handler.openPort():
            raise ServoBusError(f"Could not open port {self.port_name}")
        if not self.port_handler.setBaudRate(self.baudrate):
            self.port_handler.closePort()
            raise ServoBusError(f"Could not set baudrate {self.baudrate} on {self.port_name}")
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            try:
                self.disable_torque()
            except ServoBusError:
                pass
            self.port_handler.closePort()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ---------------------------------------------------------------- calibration
    def load_calibration(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self.calibration = {
            name: MotorCalibration(
                id=entry["id"],
                drive_mode=entry.get("drive_mode", 0),
                homing_offset=entry["homing_offset"],
                range_min=entry["range_min"],
                range_max=entry["range_max"],
            )
            for name, entry in raw.items()
        }

    def joint_id(self, name: str) -> int:
        if name in self.calibration:
            return self.calibration[name].id
        return DEFAULT_JOINT_IDS[name]

    def joint_names(self) -> list[str]:
        if self.calibration:
            return sorted(self.calibration, key=lambda n: self.calibration[n].id)
        return JOINT_ORDER

    def apply_homing_offsets(self) -> None:
        """Push the loaded calibration's homing offsets into each servo's EEPROM."""
        for cal in self.calibration.values():
            self.write_raw("Homing_Offset", cal.id, cal.homing_offset)

    # ---------------------------------------------------------------- low-level I/O
    def write_raw(self, data_name: str, motor_id: int, value: int) -> None:
        addr, size, sign_bit = REGISTERS[data_name]
        packed = encode_sign_magnitude(value, sign_bit) if sign_bit is not None else value
        is_eeprom = data_name in _EEPROM_REGISTERS

        result = scs.COMM_TX_ERROR
        for attempt in range(WRITE_RETRIES):
            with self._lock:
                if size == 1:
                    result, error = self.packet_handler.write1ByteTxRx(self.port_handler, motor_id, addr, packed)
                else:
                    result, error = self.packet_handler.write2ByteTxRx(self.port_handler, motor_id, addr, packed)
            if result == scs.COMM_SUCCESS:
                break
            if attempt < WRITE_RETRIES - 1:
                time.sleep(EEPROM_WRITE_SETTLE_S)
        if result != scs.COMM_SUCCESS:
            raise ServoBusError(
                f"write {data_name} id={motor_id} failed: {self.packet_handler.getTxRxResult(result)}"
            )
        if is_eeprom:
            time.sleep(EEPROM_WRITE_SETTLE_S)

    def read_raw(self, data_name: str, motor_id: int) -> int:
        addr, size, sign_bit = REGISTERS[data_name]
        with self._lock:
            if size == 1:
                value, result, error = self.packet_handler.read1ByteTxRx(self.port_handler, motor_id, addr)
            else:
                value, result, error = self.packet_handler.read2ByteTxRx(self.port_handler, motor_id, addr)
        if result != scs.COMM_SUCCESS:
            raise ServoBusError(
                f"read {data_name} id={motor_id} failed: {self.packet_handler.getTxRxResult(result)}"
            )
        return decode_sign_magnitude(value, sign_bit) if sign_bit is not None else value

    def ping(self, motor_id: int) -> bool:
        with self._lock:
            _model, result, _error = self.packet_handler.ping(self.port_handler, motor_id)
        return result == scs.COMM_SUCCESS

    # ---------------------------------------------------------------- first-time motor setup
    # Everything below is for a servo that is NOT yet part of a working arm -
    # straight out of the box every STS3215 answers to ID 1, so six of them on
    # one bus are indistinguishable until each has been given its own address.
    # This is the in-GUI equivalent of `lerobot-setup-motors`, and it has to
    # exist here because nothing else in this app can work before it's done.

    def set_baudrate(self, baudrate: int) -> None:
        """Re-rate the already-open port. Used by scan_bus() to sweep the
        baudrates a servo might have been left on, without reopening."""
        if not self.port_handler.setBaudRate(baudrate):
            raise ServoBusError(f"Could not set baudrate {baudrate} on {self.port_name}")
        self.baudrate = baudrate

    def scan_ids(self, max_id: int = DEFAULT_SCAN_MAX_ID) -> list[int]:
        """Ping every address on the bus at the CURRENT baudrate."""
        return [motor_id for motor_id in range(max_id + 1) if self.ping(motor_id)]

    def scan_bus(self, baudrates: list[int] | None = None,
                 max_id: int = DEFAULT_SCAN_MAX_ID) -> dict[int, list[int]]:
        """{baudrate: [ids found at it]}, skipping baudrates that found nothing.

        Sweeping every baudrate rather than assuming 1 Mbps is the difference
        between "no servos found, good luck" and "found id 1 at 115200, want me
        to move it to 1 Mbps?" - which is the actual state a servo ends up in
        after someone runs an unrelated Feetech tool on it.
        """
        found: dict[int, list[int]] = {}
        original = self.baudrate
        try:
            for rate in (baudrates or list(BAUDRATE_TABLE.values())):
                self.set_baudrate(rate)
                ids = self.scan_ids(max_id)
                if ids:
                    found[rate] = ids
        finally:
            self.set_baudrate(original)
        return found

    def assign_id(self, current_id: int, new_id: int) -> None:
        """Rewrite one servo's own bus address.

        ONLY safe with a single servo on the bus - `current_id` is how we
        address it, and if several servos share that id they would all take
        `new_id` simultaneously and stay indistinguishable. The GUI enforces
        the one-at-a-time rule; this just refuses the obviously-invalid ids.

        Torque off + Lock 0 before the write, Lock 1 after (on the NEW id,
        since that's what the servo answers to by then) - the same bracketing
        LeRobot's Feetech _disable_torque/_enable_torque does around every
        EEPROM write.
        """
        if not 0 <= new_id <= MAX_SERVO_ID:
            raise ServoBusError(f"Invalid servo id {new_id} - must be 0..{MAX_SERVO_ID}")
        if not self.ping(current_id):
            raise ServoBusError(f"No servo answering at id {current_id} on {self.port_name}")

        self.write_raw("Torque_Enable", current_id, 0)
        self.write_raw("Lock", current_id, 0)

        # The ID write is the one register write that cannot be checked by its
        # own status packet: the servo adopts `new_id` the instant it applies
        # the write, so the reply comes back from an address the SDK is no
        # longer listening for and read as a timeout. Retrying on that
        # "failure" would just re-send to `current_id`, which by then is
        # nobody. So: fire once, ignore the transport result, and let a ping
        # at the new address be the actual proof.
        addr, _size, _sign = REGISTERS["ID"]
        with self._lock:
            self.packet_handler.write1ByteTxRx(self.port_handler, current_id, addr, new_id)
        time.sleep(EEPROM_WRITE_SETTLE_S)

        if not self.ping(new_id):
            raise ServoBusError(
                f"Wrote ID={new_id} to the servo at id={current_id}, but nothing answers at "
                f"{new_id}. Power-cycle the arm and rescan before trying again."
            )
        self.write_raw("Lock", new_id, 1)

    def set_servo_baudrate(self, motor_id: int, baudrate: int) -> None:
        """Move a servo onto `baudrate` permanently (EEPROM). The servo starts
        answering at the new rate immediately, so the caller has to re-rate the
        port before it can talk to it again - scan_bus()/the GUI do that."""
        if baudrate not in BAUDRATE_INDEX:
            raise ServoBusError(f"Unsupported baudrate {baudrate} - pick one of {sorted(BAUDRATE_TABLE.values())}")
        self.write_raw("Torque_Enable", motor_id, 0)
        self.write_raw("Lock", motor_id, 0)

        # Same unverifiable-write situation as assign_id(): the servo changes
        # rate the moment it applies this, so its reply is transmitted at a
        # baudrate this port is not listening at yet. Fire once, don't retry.
        addr, _size, _sign = REGISTERS["Baud_Rate"]
        with self._lock:
            self.packet_handler.write1ByteTxRx(self.port_handler, motor_id, addr, BAUDRATE_INDEX[baudrate])
        time.sleep(EEPROM_WRITE_SETTLE_S)

        # No Lock=1 here: the servo is only reachable again once this port is
        # re-rated to `baudrate`, which is the caller's job (it re-scans, then
        # locks). Leaving Lock at 0 in between is harmless - it only gates
        # further EEPROM writes, and nothing writes EEPROM until setup resumes.
        self.set_baudrate(baudrate)
        if self.ping(motor_id):
            self.write_raw("Lock", motor_id, 1)

    # ---------------------------------------------------------------- torque
    def enable_torque(self, name: str | None = None) -> None:
        for n in ([name] if name else self.joint_names()):
            self.write_raw("Torque_Enable", self.joint_id(n), 1)

    def disable_torque(self, name: str | None = None) -> None:
        for n in ([name] if name else self.joint_names()):
            self.write_raw("Torque_Enable", self.joint_id(n), 0)

    # ---------------------------------------------------------------- position (degrees)
    def read_position_deg(self, name: str) -> float:
        raw = self.read_raw("Present_Position", self.joint_id(name))
        if name in self.calibration:
            cal = self.calibration[name]
            return (raw - cal.mid) * 360.0 / MAX_RES
        return (raw - MODEL_RESOLUTION / 2) * 360.0 / MAX_RES

    def read_all_positions_deg(self) -> dict[str, float]:
        """One bus transaction for every joint (GroupSyncRead), not six."""
        names = self.joint_names()
        addr, size, _ = REGISTERS["Present_Position"]

        self._sync_reader.clearParam()
        for name in names:
            self._sync_reader.addParam(self.joint_id(name))

        with self._lock:
            result = self._sync_reader.txRxPacket()
        if result != scs.COMM_SUCCESS:
            raise ServoBusError(f"sync_read Present_Position failed: {self.packet_handler.getTxRxResult(result)}")

        positions = {}
        for name in names:
            raw = self._sync_reader.getData(self.joint_id(name), addr, size)
            cal = self.calibration.get(name)
            mid = cal.mid if cal else MODEL_RESOLUTION / 2
            positions[name] = (raw - mid) * 360.0 / MAX_RES
        return positions

    def read_telemetry(self) -> dict[str, dict[str, int]]:
        """RAW register values for every joint, in two bus transactions.

        Values stay unscaled and unsigned at this layer on purpose: several of
        these registers put the direction in a high bit rather than using
        two's complement, so decoding is an interpretation, and the driver
        should hand back what the servo actually said. The interpretation
        lives one layer up, in ui/telemetry_panel.convert_telemetry(), which
        documents each scale factor's sourcing - including which one
        (Present_Velocity) is still a derived estimate rather than a confirmed
        unit, and how the Graph tab cross-checks it against differentiated
        Present_Position on real hardware.
        """
        names = self.joint_names()
        for reader in (self._sync_reader_tel, self._sync_reader_cur):
            reader.clearParam()
            for name in names:
                reader.addParam(self.joint_id(name))

        with self._lock:
            result_tel = self._sync_reader_tel.txRxPacket()
            result_cur = self._sync_reader_cur.txRxPacket()
        if result_tel != scs.COMM_SUCCESS:
            raise ServoBusError(
                f"sync_read telemetry failed: {self.packet_handler.getTxRxResult(result_tel)}"
            )
        if result_cur != scs.COMM_SUCCESS:
            raise ServoBusError(
                f"sync_read Present_Current failed: {self.packet_handler.getTxRxResult(result_cur)}"
            )

        telemetry = {}
        for name in names:
            motor_id = self.joint_id(name)
            telemetry[name] = {
                "position": self._sync_reader_tel.getData(motor_id, 56, 2),
                "velocity": self._sync_reader_tel.getData(motor_id, 58, 2),
                "load": self._sync_reader_tel.getData(motor_id, 60, 2),
                "voltage": self._sync_reader_tel.getData(motor_id, 62, 1),
                "temperature": self._sync_reader_tel.getData(motor_id, 63, 1),
                "current": self._sync_reader_cur.getData(motor_id, 69, 2),
            }
        return telemetry

    def write_goal_deg(self, name: str, degrees: float) -> None:
        """Single-joint convenience wrapper around write_goals_deg."""
        self.write_goals_deg({name: degrees})

    def write_goals_deg(self, goals: dict[str, float]) -> None:
        """Unnormalize degrees -> raw ticks and write ALL given joints in one
        bus transaction (GroupSyncWrite). Every value is clamped to the
        calibrated safe range first, so a GUI bug or wild slider drag can
        never command a servo past its known mechanical limits."""
        _addr, _size, sign_bit = REGISTERS["Goal_Position"]

        self._sync_writer.clearParam()
        for name, degrees in goals.items():
            if name not in self.calibration:
                raise ServoBusError(f"No calibration loaded for '{name}' - refusing to move it blind")
            cal = self.calibration[name]
            raw = int(round(degrees * MAX_RES / 360.0 + cal.mid))
            raw = max(cal.range_min, min(cal.range_max, raw))
            packed = encode_sign_magnitude(raw, sign_bit)
            self._sync_writer.addParam(cal.id, [scs.SCS_LOBYTE(packed), scs.SCS_HIBYTE(packed)])

        with self._lock:
            result = self._sync_writer.txPacket()
        if result != scs.COMM_SUCCESS:
            raise ServoBusError(f"sync_write Goal_Position failed: {self.packet_handler.getTxRxResult(result)}")

    def deg_limits(self, name: str) -> tuple[float, float]:
        if name not in self.calibration:
            return (-180.0, 180.0)
        cal = self.calibration[name]
        lo = (cal.range_min - cal.mid) * 360.0 / MAX_RES
        hi = (cal.range_max - cal.mid) * 360.0 / MAX_RES
        return (lo, hi)

    # ---------------------------------------------------------------- calibration procedure
    # Mirrors lerobot's so_follower/so_leader .calibrate() exactly (reset -> half-turn
    # homing -> record range of motion -> force wrist_roll full-turn -> write limits).
    # Works in raw ticks, independent of self.calibration (which doesn't exist yet
    # while calibrating) - joint_id() already falls back to DEFAULT_JOINT_IDS.

    def read_position_raw(self, name: str) -> int:
        return self.read_raw("Present_Position", self.joint_id(name))

    def prepare_for_calibration(self, name: str) -> None:
        """Disable torque, force position mode, and clear any prior homing/limits."""
        mid = self.joint_id(name)
        self.write_raw("Torque_Enable", mid, 0)
        self.write_raw("Operating_Mode", mid, OPERATING_MODE_POSITION)
        self.write_raw("Homing_Offset", mid, 0)
        self.write_raw("Min_Position_Limit", mid, 0)
        self.write_raw("Max_Position_Limit", mid, MAX_RES)

    def set_half_turn_homing(self, name: str) -> int:
        """Make the CURRENT physical position read back as the half-turn
        centre (2047). Call this once, right after the user parks the joint
        at the middle of its intended range of motion."""
        mid = self.joint_id(name)
        actual = self.read_raw("Present_Position", mid)
        offset = actual - int(MAX_RES / 2)
        self.write_raw("Homing_Offset", mid, offset)
        return offset

    def write_position_limits_raw(self, name: str, range_min: int, range_max: int) -> None:
        mid = self.joint_id(name)
        self.write_raw("Min_Position_Limit", mid, range_min)
        self.write_raw("Max_Position_Limit", mid, range_max)
