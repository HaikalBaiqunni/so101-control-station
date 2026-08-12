"""
SO-101 Control Station - standalone Python GUI for teleoperating / jogging
the SO-ARM100/SO-101 arm, with a live MuJoCo digital twin, optional camera
feed, and optional gamepad input.

Run: python main.py
"""
import os
import sys

# Must happen BEFORE `import mujoco` (transitively pulled in by ui.main_window
# -> core.digital_twin), which resolves and loads its GL backend at import
# time rather than on first render.
#
# Windows only: mujoco's default backend auto-detection trips ctypes.WinDLL
# with "[WinError 6] The handle is invalid" while loading mujoco.dll on some
# terminals/consoles - forcing the native WGL backend avoids whatever
# console-handle probing triggers that. WGL is a Windows API, so pinning it
# unconditionally would break the twin everywhere else; other platforms are
# left to mujoco's own detection (EGL/GLX), which works there.
if sys.platform == "win32":
    os.environ.setdefault("MUJOCO_GL", "wgl")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402
from ui.style import STYLE_SHEET  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE_SHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
