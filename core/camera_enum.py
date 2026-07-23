"""
List available cameras with human-readable names (e.g. "Integrated Webcam",
"Full HD webcam") instead of a bare OpenCV index - that index is meaningless
to look at and, worse, isn't guaranteed stable across reboots/replugs, so a
name-based picker is also just more reliable than remembering "camera 1 was
the external one last time I checked".

Windows: uses pygrabber (DirectShow device enumeration) for real names, in
the same index order OpenCV's default backend uses.
Other platforms / pygrabber unavailable: falls back to probing indices 0-9
with OpenCV directly and labelling them generically.
"""
from __future__ import annotations

import cv2


def list_cameras(max_probe: int = 10) -> list[tuple[int, str]]:
    try:
        from pygrabber.dshow_graph import FilterGraph

        names = FilterGraph().get_input_devices()
        return list(enumerate(names))
    except Exception:
        pass  # not on Windows, or pygrabber/COM unavailable - fall back to probing

    found = []
    for index in range(max_probe):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                found.append((index, f"Camera {index}"))
        cap.release()
    return found
