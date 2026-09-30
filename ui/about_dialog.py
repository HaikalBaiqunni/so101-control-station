from __future__ import annotations

import importlib
import platform

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QGridLayout, QLabel, QVBoxLayout

APP_NAME = "SO-101 Control Station"
APP_VERSION = "0.2.0"   # kept equal to pyproject.toml (a test checks it)
CREATOR = "Haikal Baiqunni"
REPO_URL = "https://github.com/HaikalBaiqunni/so101-control-station"
LICENSE_NAME = "MIT License"

# (what it is used for, importable module name or None, display name)
TECH_STACK = [
    ("User interface", "PySide6", "PySide6 (Qt 6)"),
    ("Digital twin and kinematics", "mujoco", "MuJoCo"),
    ("Numerics", "numpy", "NumPy"),
    ("Camera", "cv2", "OpenCV"),
    ("Serial links", "serial", "pySerial"),
    ("SO-101 servos (Feetech STS3215)", "scservo_sdk", "Feetech servo SDK"),
    ("Star Arm 102 leader (FashionStar)", "motorbridge_smart_servo", "motorbridge-smart-servo"),
    ("Gamepad", "pygame", "pygame"),
]

ROBOTS = [
    "SO-101 / SO-ARM100 - Feetech STS3215 servos",
    "reBot B601-DM - Damiao CAN motors, with the Seeed Star Arm 102 (FashionStar) leader",
]


def module_version(module_name: str) -> str:
    """Installed version of a dependency, or '-' if it is not importable."""
    try:
        module = importlib.import_module(module_name)
    except Exception:   # a missing or broken optional dependency must not break About
        return "-"
    return str(getattr(module, "__version__", getattr(module, "VERSION", "installed")))


def about_text() -> str:
    """Plain-text version of the dialog (used by tests and for copy/paste in bug reports)."""
    lines = [f"{APP_NAME} {APP_VERSION}", f"Created by {CREATOR}", REPO_URL, LICENSE_NAME, "", "Robots:"]
    lines += [f"  {r}" for r in ROBOTS]
    lines += ["", "Tech stack:", f"  Python {platform.python_version()}"]
    lines += [f"  {shown} {module_version(module)} - {purpose}" for purpose, module, shown in TECH_STACK]
    return "\n".join(lines)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(520)

        title = QLabel(f"{APP_NAME}")
        title.setObjectName("cardTitle")
        title.setStyleSheet("font-size: 20px;")
        version = QLabel(f"Version {APP_VERSION}  -  {LICENSE_NAME}")
        version.setObjectName("sectionCaption")
        creator = QLabel(f"Created by <b>{CREATOR}</b>")
        repo = QLabel(f'<a href="{REPO_URL}" style="color:#5b9dff">{REPO_URL}</a>')
        repo.setOpenExternalLinks(True)
        repo.setTextInteractionFlags(Qt.TextBrowserInteraction)
        summary = QLabel(
            "A standalone desktop app to set up, calibrate, jog and teleoperate two robot arms, "
            "with a live MuJoCo digital twin. No LeRobot install needed."
        )
        summary.setWordWrap(True)
        summary.setObjectName("sectionCaption")

        robots = QLabel("<b>Robots</b><br>" + "<br>".join(ROBOTS))
        robots.setWordWrap(True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.addWidget(QLabel("<b>Tech stack</b>"), 0, 0, 1, 3)
        rows = [("Language", f"Python {platform.python_version()}", "")]
        rows += [(purpose, shown, module_version(module)) for purpose, module, shown in TECH_STACK]
        for i, (purpose, shown, ver) in enumerate(rows, start=1):
            purpose_label = QLabel(purpose)
            purpose_label.setObjectName("sectionCaption")
            grid.addWidget(purpose_label, i, 0)
            grid.addWidget(QLabel(shown), i, 1)
            if ver:
                version_label = QLabel(ver)
                version_label.setObjectName("poseReadout")
                grid.addWidget(version_label, i, 2)
        grid.setColumnStretch(0, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        for widget in (title, version, creator, repo, summary):
            layout.addWidget(widget)
        layout.addWidget(robots)
        layout.addLayout(grid)
        layout.addWidget(buttons)
