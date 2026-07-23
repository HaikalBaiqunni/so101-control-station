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
    "Min_Position_Limit": (9, 2, None),
    "Max_Position_Limit": (11, 2, None),
    "Homing_Offset": (31, 2, 11),
    "Operating_Mode": (33, 1, None),
    "Torque_Enable": (40, 1, None),
    "Goal_Position": (42, 2, 15),
    "Present_Position": (56, 2, None),
    "Present_Current": (69, 2, None),
}
OPERATING_MODE_POSITION = 0

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
        for name, cal in self.calibration.items():
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
