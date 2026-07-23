"""
SO-101 Control Station - standalone Python GUI for teleoperating / jogging
the SO-ARM100/SO-101 arm, with a live MuJoCo digital twin, optional camera
feed, and optional gamepad input.

Run: python main.py
"""
import os
import sys

# Must happen BEFORE `import mujoco` (transitively pulled in by ui.main_window
# -> core.digital_twin). On some Windows terminals/consoles, mujoco's default
# GL backend auto-detection trips ctypes.WinDLL with "[WinError 6] The handle
# is invalid" while loading mujoco.dll - forcing the native WGL backend avoids
# whatever console-handle probing triggers that.
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
