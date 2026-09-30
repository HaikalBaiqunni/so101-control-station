"""Setup hub: one page that holds every set-up/maintenance tool as a section."""
from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .icons import icon
from .style import COLORS
from .top_bar import BAR_H

SECTION_ICONS = {
    "Motors and ids": "chip",
    "Calibration": "target",
    "Inputs": "gamepad",
    "Data and logs": "doc",
}


class SetupHub(QWidget):
    back_requested = Signal()

    def __init__(self):
        super().__init__()
        self.back_btn = QPushButton("Back to stage")
        self.back_btn.setObjectName("segButton")
        self.back_btn.clicked.connect(self.back_requested)
        self.back_btn.setIcon(icon("back", COLORS["text_muted"]))
        self.back_btn.setIconSize(QSize(16, 16))

        self.section_list = QListWidget()
        self.section_list.setObjectName("sectionList")
        self.section_list.setFixedWidth(230)
        self.section_list.setIconSize(QSize(18, 18))
        self.stack = QStackedWidget()
        self.section_list.currentRowChanged.connect(self.stack.setCurrentIndex)

        body = QHBoxLayout()
        body.addWidget(self.section_list)
        body.addWidget(self.stack, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, BAR_H, 12, 8)   # clear of the floating top bar
        head = QHBoxLayout()
        head.addWidget(self.back_btn)
        head.addStretch(1)
        layout.addLayout(head)
        layout.addLayout(body, 1)

        self._titles: list[str] = []

    def add_section(self, title: str, widget: QWidget, scroll: bool = True) -> None:
        """Adds `widget` under `title`. Wrapped in a QScrollArea by default so a
        tall panel scrolls instead of being clipped (learned on the DM setup page)."""
        if scroll:
            area = QScrollArea()
            area.setWidget(widget)
            area.setWidgetResizable(True)
            widget = area
        self._titles.append(title)
        self.section_list.addItem(title)
        if title in SECTION_ICONS:
            self.section_list.item(self.section_list.count() - 1).setIcon(
                icon(SECTION_ICONS[title], COLORS["text_muted"], COLORS["text"])
            )
        self.stack.addWidget(widget)
        if self.section_list.currentRow() < 0:
            self.section_list.setCurrentRow(0)

    def section_titles(self) -> list[str]:
        return list(self._titles)

    def show_section(self, title: str) -> None:
        self.section_list.setCurrentRow(self._titles.index(title))
