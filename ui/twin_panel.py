from __future__ import annotations

import numpy as np
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class TwinPanel(QGroupBox):
    """Dumb display widget - MainWindow owns the DigitalTwin + render QTimer
    and just pushes frames in here. The model path is user-chosen so this
    works on any machine, not just the one this was built on."""

    load_requested = Signal(str)  # mjcf path

    def __init__(self, parent=None):
        super().__init__("DIGITAL TWIN (MuJoCo)", parent)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("path to scene.xml / *.xml MJCF model")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(lambda: self.load_requested.emit(self.path_edit.text()))

        self.caption = QLabel("not loaded - pick a scene.xml and click Load")
        self.caption.setObjectName("sectionCaption")

        self.view = QLabel("twin not loaded")
        self.view.setMinimumSize(420, 280)
        self.view.setMaximumHeight(520)  # a placeholder/render box, not "however tall the window happens to be"
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background-color: #101215; border: 1px solid #3a4048;")

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse_btn)
        path_row.addWidget(load_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(path_row)
        layout.addWidget(self.caption)
        layout.addWidget(self.view)
        layout.addStretch(1)  # leftover vertical room goes here, not into the view

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select MJCF scene", "", "MuJoCo XML (*.xml)")
        if path:
            self.path_edit.setText(path)

    def set_caption(self, text: str) -> None:
        self.caption.setText(text)

    def show_frame(self, frame_rgb: np.ndarray) -> None:
        h, w, _ = frame_rgb.shape
        image = QImage(frame_rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        self.view.setPixmap(QPixmap.fromImage(image).scaled(
            self.view.width(), self.view.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))
