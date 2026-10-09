"""Intel RealSense L515 through a helper process running an old pyrealsense2.

librealsense dropped the L515 after 2.54.x and those wheels only exist for Python <= 3.11,
so `core/rs_legacy_helper.py` runs in its own interpreter and sends frames back over a pipe
(see that file for the protocol). A source id is "l515:<serial>:<color|depth|both>".

The helper is found, in this order:
    1. $SO101_RS_HELPER   a ready-to-run program (e.g. a frozen rs_helper.exe)
    2. rs_helper.exe next to the application / inside the frozen bundle
    3. an interpreter that has pyrealsense2 2.54.x: $SO101_RS_PYTHON, or a conda env called
       "rs-l515" (see CONTRIBUTING.md), running core/rs_legacy_helper.py
Without any of them nothing is listed and the rest of the app is unaffected.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import threading

import numpy as np
from PySide6.QtCore import QThread, Signal

from .realsense_camera import MODE_LABELS, MODES

L515_PREFIX = "l515:"
MAGIC = b"RSF1"
HEADER = struct.Struct("<4sIII")
LIST_TIMEOUT_S = 8
MAX_FRAME_BYTES = 64 * 1024 * 1024   # sanity bound on a header read from the pipe
HELPER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rs_legacy_helper.py")
ENV_NAME = "rs-l515"


def is_l515_source(source) -> bool:
    return isinstance(source, str) and source.startswith(L515_PREFIX)


def parse_source(source: str) -> tuple[str, str]:
    serial, _, mode = source[len(L515_PREFIX):].partition(":")
    return serial, mode if mode in MODES else "color"


def _candidate_pythons() -> list[str]:
    home = os.path.expanduser("~")
    roots = [os.path.join(home, ".conda", "envs"), os.path.join(home, "miniconda3", "envs"),
             os.path.join(home, "anaconda3", "envs"), r"C:\ProgramData\miniconda3\envs",
             r"C:\ProgramData\anaconda3\envs"]
    names = ("python.exe", os.path.join("bin", "python"))
    return [os.path.join(root, ENV_NAME, name) for root in roots for name in names]


def find_helper() -> list[str] | None:
    """The command (argv prefix) that runs the helper, or None when there is none."""
    explicit = os.environ.get("SO101_RS_HELPER")
    if explicit and os.path.isfile(explicit):
        return [explicit]
    exe_name = "rs_helper.exe" if os.name == "nt" else "rs_helper"
    bases = [os.path.dirname(os.path.abspath(sys.argv[0] or "")), getattr(sys, "_MEIPASS", ""),
             os.path.dirname(sys.executable)]
    for base in bases:
        if base and os.path.isfile(os.path.join(base, exe_name)):
            return [os.path.join(base, exe_name)]
    python = os.environ.get("SO101_RS_PYTHON")
    pythons = ([python] if python else []) + _candidate_pythons()
    for candidate in pythons:
        if candidate and os.path.isfile(candidate) and os.path.isfile(HELPER_SCRIPT):
            return [candidate, HELPER_SCRIPT]
    return None


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW   # no console flashing up per camera
    return kwargs


def list_l515_cameras() -> list[tuple[str, str]]:
    """[(source id, label)] - three entries per L515; [] without a helper or a camera."""
    command = find_helper()
    if command is None:
        return []
    try:
        result = subprocess.run(command + ["--list"], capture_output=True, timeout=LIST_TIMEOUT_S,
                                **_popen_kwargs())
        devices = json.loads(result.stdout.decode("utf-8") or "[]")
    except Exception:
        return []
    found = []
    for dev in devices:
        name, serial = dev.get("name", ""), dev.get("serial", "")
        if "L5" not in name or not serial:   # only the L5xx: every other RealSense is handled by the normal SDK
            continue
        for mode in MODES:
            found.append((f"{L515_PREFIX}{serial}:{mode}", f"{name} ({serial}) - {MODE_LABELS[mode]}"))
    return found


def _read_exact(stream, count: int) -> bytes | None:
    buffer = bytearray()
    while len(buffer) < count:
        chunk = stream.read(count - len(buffer))
        if not chunk:
            return None
        buffer += chunk
    return bytes(buffer)


class L515CameraWorker(QThread):
    """Same signals as the other camera workers, so the Camera panel and Recorder treat them alike."""

    frame_ready = Signal(np.ndarray)  # RGB uint8 HxWx3
    error = Signal(str)
    started_ok = Signal()

    def __init__(self, serial: str, mode: str = "color", parent=None, command: list[str] | None = None):
        super().__init__(parent)
        self.serial = serial
        self.mode = mode if mode in MODES else "color"
        self._command = command
        self._stop_requested = False
        self._process: subprocess.Popen | None = None

    STOP_GRACE_S = 8.0

    def stop(self) -> None:
        """Ask the helper to stop by closing its stdin, so it stops the camera pipeline properly
        (a killed stream leaves the L515 half-open). A watchdog kills it if it does not comply."""
        self._stop_requested = True
        process = self._process
        if process is None:
            return
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        timer = threading.Timer(self.STOP_GRACE_S, lambda: process.poll() is None and process.kill())
        timer.daemon = True
        timer.start()

    def run(self) -> None:
        command = self._command or find_helper()
        if command is None:
            self.error.emit("The L515 needs the helper environment: see 'RealSense L515' in the README "
                            "(conda env rs-l515 with pyrealsense2 2.54.2).")
            return
        try:
            self._process = process = subprocess.Popen(
                command + ["--stream", "--serial", self.serial, "--mode", self.mode],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_popen_kwargs())
        except OSError as exc:
            self.error.emit(f"Could not start the L515 helper: {exc}")
            return
        stderr_lines: list[bytes] = []
        stderr_reader = threading.Thread(target=lambda: stderr_lines.extend(process.stderr.readlines()), daemon=True)
        stderr_reader.start()
        first = True
        try:
            while True:   # runs to EOF: after stop() the helper exits by itself and the pipe closes
                header = _read_exact(process.stdout, HEADER.size)
                if header is None:
                    break
                magic, width, height, size = HEADER.unpack(header)
                if magic != MAGIC or size != width * height * 3 or size > MAX_FRAME_BYTES:
                    self.error.emit("The L515 helper sent a corrupt frame; stopping the camera.")
                    return
                payload = _read_exact(process.stdout, size)
                if payload is None:
                    break
                if self._stop_requested:
                    continue   # drain what is still in the pipe, do not show it
                if first:
                    first = False
                    self.started_ok.emit()
                self.frame_ready.emit(np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 3).copy())
        finally:
            try:
                process.wait(self.STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            stderr_reader.join(1.0)
        if not self._stop_requested:
            message = b" ".join(stderr_lines).decode("utf-8", "replace").strip()
            self.error.emit(message or "The L515 helper stopped unexpectedly.")
