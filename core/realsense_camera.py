"""Intel RealSense cameras (D4xx) through the pyrealsense2 SDK, with depth.

OpenCV can show a RealSense's RGB stream as an ordinary webcam but never its depth
stream. This module opens the camera through Intel's SDK instead and offers three
views of the same device:

    color   the RGB image
    depth   the depth image as a colour map (near = warm, far = cool)
    both    colour and depth side by side, depth aligned to the colour image

pyrealsense2 is optional (`pip install pyrealsense2`); without it nothing here is
listed and the rest of the app is unaffected. A source id is
"realsense:<serial>:<mode>" - the serial is stable across replugs.
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QThread, Signal

REALSENSE_PREFIX = "realsense:"
MODES = ("color", "depth", "both")
MODE_LABELS = {"color": "color", "depth": "depth (colormap)", "both": "color + depth"}
# Tried best-first; the first one the connected camera can actually run is used. A camera on a
# USB 2 port only offers the small modes, so this falls back instead of failing.
RESOLUTIONS = ((1280, 720), (848, 480), (640, 480))
FPS = 30
FRAME_TIMEOUT_MS = 3000
MAX_MISSED_FRAMES = 3   # consecutive late frames tolerated before it counts as an error


def _rs():
    """The pyrealsense2 module, imported on first use (and replaceable in tests)."""
    import pyrealsense2 as rs
    return rs


def is_realsense_source(source) -> bool:
    return isinstance(source, str) and source.startswith(REALSENSE_PREFIX)


def parse_source(source: str) -> tuple[str, str]:
    """"realsense:<serial>:<mode>" -> (serial, mode); a missing / unknown mode means color."""
    body = source[len(REALSENSE_PREFIX):]
    serial, _, mode = body.partition(":")
    return serial, mode if mode in MODES else "color"


def list_realsense_cameras() -> list[tuple[str, str]]:
    """[(source id, label)] - three entries per camera; [] without pyrealsense2 or a camera."""
    try:
        rs = _rs()
        devices = list(rs.context().query_devices())
    except Exception:
        return []
    found = []
    for dev in devices:
        try:
            serial = dev.get_info(rs.camera_info.serial_number)
            name = dev.get_info(rs.camera_info.name)
        except Exception:
            continue
        for mode in MODES:
            found.append((f"{REALSENSE_PREFIX}{serial}:{mode}", f"{name} ({serial}) - {MODE_LABELS[mode]}"))
    return found


def compose_frame(color: np.ndarray | None, depth_rgb: np.ndarray | None, mode: str) -> np.ndarray | None:
    """The image to show for `mode`, from an RGB colour frame and an RGB-colourised depth frame."""
    if mode == "color":
        return color
    if mode == "depth":
        return depth_rgb
    if color is None or depth_rgb is None:
        return None
    if depth_rgb.shape[:2] != color.shape[:2]:   # should not happen once aligned; never crash on it
        return color
    return np.ascontiguousarray(np.hstack((color, depth_rgb)))


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "busy" in low or "in use" in low or "could not be opened" in low or "failed to set power state" in low:
        return "The RealSense camera is in use by another program (RealSense Viewer?). Close it, then press Start again."
    if "no device connected" in low:
        return "No RealSense camera found. Check the cable and that RealSense Viewer sees it."
    return f"RealSense camera error: {text}"


class RealSenseCameraWorker(QThread):
    """Same signals as the other camera workers, so the Camera panel treats them alike."""

    frame_ready = Signal(np.ndarray)  # RGB uint8 HxWx3
    error = Signal(str)
    started_ok = Signal()

    def __init__(self, serial: str, mode: str = "color", fps: int = 20, parent=None):
        super().__init__(parent)
        self.serial = serial
        self.mode = mode if mode in MODES else "color"
        self.fps = fps
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _configure(self, rs):
        """A rs.config for the requested streams at the best resolution this camera can run."""
        want_color = self.mode in ("color", "both")
        want_depth = self.mode in ("depth", "both")
        last_error = None
        for width, height in RESOLUTIONS:
            config = rs.config()
            config.enable_device(self.serial)
            if want_color:
                config.enable_stream(rs.stream.color, width, height, rs.format.rgb8, FPS)
            if want_depth:
                config.enable_stream(rs.stream.depth, width, height, rs.format.z16, FPS)
            try:
                if config.can_resolve(rs.pipeline_wrapper(rs.pipeline())):
                    return config
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"no supported stream mode ({last_error})" if last_error else "no supported stream mode")

    def run(self) -> None:
        try:
            rs = _rs()
        except Exception:
            self.error.emit("RealSense support needs the pyrealsense2 package: pip install pyrealsense2")
            return
        pipeline = None
        try:
            config = self._configure(rs)
            pipeline = rs.pipeline()
            pipeline.start(config)
            self.started_ok.emit()
            colorizer = rs.colorizer()
            align = rs.align(rs.stream.color) if self.mode == "both" else None
            period = 1.0 / max(self.fps, 1)
            misses = 0
            while not self._stop_requested:
                started = time.perf_counter()
                try:
                    frames = pipeline.wait_for_frames(FRAME_TIMEOUT_MS)
                except RuntimeError as exc:
                    # the first frames of a stream can be slow (USB 2, a camera just re-opened):
                    # a late frame is not an error, several in a row is
                    if "didn't arrive" in str(exc) and misses < MAX_MISSED_FRAMES:
                        misses += 1
                        continue
                    raise
                misses = 0
                if align is not None:
                    frames = align.process(frames)
                color = depth_rgb = None
                color_frame = frames.get_color_frame() if self.mode != "depth" else None
                depth_frame = frames.get_depth_frame() if self.mode != "color" else None
                if color_frame:
                    color = np.asanyarray(color_frame.get_data())
                if depth_frame:
                    depth_rgb = np.asanyarray(colorizer.colorize(depth_frame).get_data())
                image = compose_frame(color, depth_rgb, self.mode)
                if image is not None:
                    self.frame_ready.emit(np.ascontiguousarray(image))
                time.sleep(max(0.0, period - (time.perf_counter() - started)))
        except Exception as exc:
            self.error.emit(friendly_error(exc))
        finally:
            if pipeline is not None:
                try:
                    pipeline.stop()
                except Exception:
                    pass
