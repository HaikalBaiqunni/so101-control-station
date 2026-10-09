"""Stand-alone helper that talks to a RealSense L515 through an OLD pyrealsense2.

Intel removed L500 (L515) support from librealsense after 2.54.x, and wheels of that
generation only exist for Python 3.11 and older. The GUI runs on a newer Python, so the
camera is driven from this script in a separate interpreter and its frames come back over
a pipe. Nothing here imports the app (and no Qt), so it can be frozen into its own exe.

    python rs_legacy_helper.py --list
    python rs_legacy_helper.py --stream --serial <serial> --mode color|depth|both

--list prints JSON  [{"serial": "...", "name": "..."}]  on stdout.
--stream writes frames to stdout until its stdin is closed (or stdout breaks):
    b"RSF1" + uint32 width + uint32 height + uint32 byte-count + RGB uint8 pixels
Errors go to stderr as one line, with exit status 1.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import threading
import time

MAGIC = b"RSF1"
HEADER = struct.Struct("<4sIII")
# L515: depth 1024x768 / 640x480 / 320x240 and colour 1280x720 / 1920x1080, all at 30 fps.
DEPTH_MODES = ((1024, 768), (640, 480), (320, 240))
COLOR_SIZE = (1280, 720)
FPS = 30
FRAME_TIMEOUT_MS = 3000
FIRST_FRAME_TIMEOUT_MS = 4000
START_ATTEMPTS = 4
RETRY_PAUSE_S = 1.5


def fail(message: str) -> int:
    sys.stderr.write(message.strip().replace("\n", " ") + "\n")
    sys.stderr.flush()
    return 1


def list_devices() -> int:
    import pyrealsense2 as rs

    found = []
    for dev in rs.context().query_devices():
        try:
            found.append({"serial": dev.get_info(rs.camera_info.serial_number),
                          "name": dev.get_info(rs.camera_info.name)})
        except Exception:
            continue
    sys.stdout.write(json.dumps(found))
    sys.stdout.flush()
    return 0


def stream(serial: str, mode: str) -> int:
    import numpy as np
    import pyrealsense2 as rs

    want_color = mode in ("color", "both")
    want_depth = mode in ("depth", "both")
    config = rs.config()
    config.enable_device(serial)
    if want_color:
        config.enable_stream(rs.stream.color, COLOR_SIZE[0], COLOR_SIZE[1], rs.format.rgb8, FPS)
    if want_depth:
        width, height = DEPTH_MODES[0]
        config.enable_stream(rs.stream.depth, width, height, rs.format.z16, FPS)

    # An L515 often delivers nothing when started right after it was stopped (its scanner is still
    # settling), so the first frame is waited for, and the start retried a few times.
    pipeline = first = None
    last_error = None
    for _attempt in range(START_ATTEMPTS):
        candidate = rs.pipeline()
        try:
            candidate.start(config)
        except Exception as exc:
            text = str(exc)
            if "busy" in text.lower() or "in use" in text.lower() or "could not be opened" in text.lower():
                return fail("The RealSense camera is in use by another program (RealSense Viewer?). Close it, then press Start again.")
            last_error = exc
        else:
            try:
                first = candidate.wait_for_frames(FIRST_FRAME_TIMEOUT_MS)
                pipeline = candidate
                break
            except RuntimeError as exc:
                last_error = exc
                try:
                    candidate.stop()
                except Exception:
                    pass
        time.sleep(RETRY_PAUSE_S)
    if pipeline is None:
        return fail(f"RealSense L515 error: {last_error}")

    colorizer = rs.colorizer()
    align = rs.align(rs.stream.color) if mode == "both" else None
    out = sys.stdout.buffer
    misses = 0

    # The GUI asks for a stop by closing our stdin (also what happens if the GUI dies). Stopping
    # the pipeline properly matters: a killed stream leaves the L515 half-open for the next start.
    stop = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.buffer.read(), stop.set()), daemon=True).start()
    try:
        while not stop.is_set():
            try:
                frames = first if first is not None else pipeline.wait_for_frames(FRAME_TIMEOUT_MS)
                first = None
            except RuntimeError as exc:
                if "didn't arrive" in str(exc) and misses < 3:
                    misses += 1
                    continue
                return fail(f"RealSense L515 error: {exc}")
            misses = 0
            if align is not None:
                frames = align.process(frames)
            color = depth_rgb = None
            if want_color:
                color_frame = frames.get_color_frame()
                if color_frame:
                    color = np.asanyarray(color_frame.get_data())
            if want_depth:
                depth_frame = frames.get_depth_frame()
                if depth_frame:
                    depth_rgb = np.asanyarray(colorizer.colorize(depth_frame).get_data())
            if mode == "color":
                image = color
            elif mode == "depth":
                image = depth_rgb
            else:
                image = None if color is None or depth_rgb is None else np.hstack((color, depth_rgb))
            if image is None:
                continue
            image = np.ascontiguousarray(image)
            height, width = image.shape[:2]
            try:
                out.write(HEADER.pack(MAGIC, width, height, image.nbytes))
                out.write(image.data)
                out.flush()
            except (BrokenPipeError, OSError):
                return 0   # the GUI closed the pipe: normal shutdown
    finally:
        try:
            pipeline.stop()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--serial", default="")
    parser.add_argument("--mode", default="color", choices=("color", "depth", "both"))
    args = parser.parse_args()
    try:
        if args.list:
            return list_devices()
        if args.stream:
            if not args.serial:
                return fail("--stream needs --serial")
            return stream(args.serial, args.mode)
    except ImportError:
        return fail("pyrealsense2 is not installed in the helper's Python (needs 2.54.x for the L515).")
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code or 0)   # a plain exit can crash while the stdin-watching thread is still blocked
