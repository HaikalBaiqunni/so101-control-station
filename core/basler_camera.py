"""Basler cameras (USB3 Vision / GigE) through Basler's own pypylon SDK.

OpenCV only sees ordinary UVC webcams; a Basler industrial camera such as the
acA1300-200um is not one, so it needs the pylon driver (installed with the pylon
Camera Software Suite) plus the `pypylon` Python package:

    pip install pypylon

pypylon is optional: when it is missing `list_basler_cameras()` just returns an empty
list and the rest of the app is unaffected. A Basler source is identified as
"basler:<serial number>" - the serial is stable across replugs and reboots, unlike an
enumeration index.
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QThread, Signal

BASLER_PREFIX = "basler:"


def _pylon():
    """The pypylon module, imported on first use (and replaceable in tests)."""
    from pypylon import pylon
    return pylon


def is_basler_source(source) -> bool:
    return isinstance(source, str) and source.startswith(BASLER_PREFIX)


def serial_of(source: str) -> str:
    return source[len(BASLER_PREFIX):]


def list_basler_cameras() -> list[tuple[str, str]]:
    """[(source id, label)] for every Basler camera pylon can see; [] without pypylon."""
    try:
        pylon = _pylon()
        infos = pylon.TlFactory.GetInstance().EnumerateDevices()
    except Exception:   # pypylon not installed, or the pylon runtime refused to start
        return []
    found = []
    for info in infos:
        serial = info.GetSerialNumber()
        found.append((BASLER_PREFIX + serial, f"Basler {info.GetModelName()} ({serial})"))
    return found


def friendly_error(exc: Exception) -> str:
    """pylon's own messages are long and technical; name the two causes people actually hit."""
    text = str(exc)
    if "exclusively opened" in text or "already opened" in text or "in use" in text.lower():
        return ("The Basler camera is in use by another program (pylon Viewer?). "
                "Close it, then press Start again.")
    return f"Basler camera error: {text}"


class BaslerCameraWorker(QThread):
    """Same signals as core.workers.CameraWorker, so the Camera panel treats both alike."""

    frame_ready = Signal(np.ndarray)  # RGB uint8 HxWx3
    error = Signal(str)
    started_ok = Signal()

    def __init__(self, serial: str, fps: int = 20, parent=None):
        super().__init__(parent)
        self.serial = serial
        self.fps = fps
        self._stop_requested = False   # same early-stop guard as the other workers

    def stop(self) -> None:
        self._stop_requested = True

    @staticmethod
    def _try_set(camera, feature: str, value) -> None:
        """Best-effort feature write: a camera that lacks it (or is read-only) is fine."""
        try:
            getattr(camera, feature).SetValue(value)
        except Exception:
            pass

    def run(self) -> None:
        try:
            pylon = _pylon()
        except Exception:
            self.error.emit("Basler support needs the pypylon package: pip install pypylon")
            return
        camera = None
        try:
            factory = pylon.TlFactory.GetInstance()
            info = next((d for d in factory.EnumerateDevices() if d.GetSerialNumber() == self.serial), None)
            if info is None:
                self.error.emit(f"Basler camera {self.serial} not found (is it plugged in, and does pylon Viewer see it?)")
                return
            camera = pylon.InstantCamera(factory.CreateDevice(info))
            camera.Open()
            # An industrial camera starts with whatever exposure it was last left at, often a
            # black image: let it adjust itself, as pylon Viewer's one-click "continuous" does.
            self._try_set(camera, "ExposureAuto", "Continuous")
            self._try_set(camera, "GainAuto", "Continuous")
            converter = pylon.ImageFormatConverter()
            converter.OutputPixelFormat = pylon.PixelType_RGB8packed              # mono and Bayer both become RGB
            converter.OutputBitAlignment.Value = "MsbAligned"
            camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)       # never queue old frames
            self.started_ok.emit()
            period = 1.0 / max(self.fps, 1)
            while not self._stop_requested and camera.IsGrabbing():
                started = time.perf_counter()
                result = camera.RetrieveResult(500, pylon.TimeoutHandling_Return)
                try:
                    if result is not None and result.IsValid() and result.GrabSucceeded():
                        self.frame_ready.emit(np.ascontiguousarray(converter.Convert(result).GetArray()))
                finally:
                    if result is not None:
                        result.Release()
                time.sleep(max(0.0, period - (time.perf_counter() - started)))
        except Exception as exc:   # pylon raises its own exception types; show the message
            self.error.emit(friendly_error(exc))
        finally:
            if camera is not None:
                try:
                    if camera.IsGrabbing():
                        camera.StopGrabbing()
                    camera.Close()
                except Exception:
                    pass
