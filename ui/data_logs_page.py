from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget


class DataLogsPage(QWidget):
    """Where this session's log lives, and a way to open its folder."""

    def __init__(self, log_path: str, parent=None):
        super().__init__(parent)
        self.log_path = log_path

        box = QGroupBox("SESSION LOG")
        note = QLabel(
            "Events (connects, torque, calibration, errors) and throttled joint "
            "positions are written to this file for the whole session."
        )
        note.setObjectName("sectionCaption")
        note.setWordWrap(True)
        self.path_label = QLabel(log_path)
        self.path_label.setObjectName("poseReadout")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_btn = QPushButton("Open log folder")
        self.open_btn.clicked.connect(self._open_folder)
        csv_note = QLabel("The telemetry CSV log is started from the Telemetry drawer (Start CSV Log).")
        csv_note.setObjectName("sectionCaption")
        csv_note.setWordWrap(True)

        layout = QVBoxLayout(box)
        layout.addWidget(note)
        layout.addWidget(self.path_label)
        layout.addWidget(self.open_btn)
        layout.addWidget(csv_note)

        root = QVBoxLayout(self)
        root.addWidget(box)
        root.addStretch(1)

    def _open_folder(self) -> None:
        folder = os.path.dirname(os.path.abspath(self.log_path))
        if hasattr(os, "startfile"):
            os.startfile(folder)
