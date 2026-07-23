from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)


class TeachingPanel(QGroupBox):
    """JAKA-style teach & playback: record the follower's current pose as a
    waypoint (however it got there - hand-guided with torque off, driven by
    the leader, or via the manual sliders), then replay the recorded sequence
    point-to-point. MainWindow owns the actual waypoint data and playback
    state machine - this is just the list/button widget."""

    record_requested = Signal()
    delete_requested = Signal()
    move_up_requested = Signal()
    move_down_requested = Signal()
    play_requested = Signal()
    stop_requested = Signal()
    save_requested = Signal()
    load_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("TEACHING (waypoints)", parent)

        self.list_widget = QListWidget()

        self.record_btn = QPushButton("Record Waypoint")
        self.record_btn.clicked.connect(self.record_requested)
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.delete_requested)
        self.up_btn = QPushButton("Move Up")
        self.up_btn.clicked.connect(self.move_up_requested)
        self.down_btn = QPushButton("Move Down")
        self.down_btn.clicked.connect(self.move_down_requested)

        self.play_btn = QPushButton("Play Sequence")
        self.play_btn.clicked.connect(self.play_requested)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_requested)
        self.stop_btn.setEnabled(False)

        self.save_btn = QPushButton("Save...")
        self.save_btn.clicked.connect(self.save_requested)
        self.load_btn = QPushButton("Load...")
        self.load_btn.clicked.connect(self.load_requested)

        self.status_label = QLabel("")
        self.status_label.setObjectName("sectionCaption")
        self.status_label.setWordWrap(True)

        edit_row = QHBoxLayout()
        edit_row.addWidget(self.record_btn)
        edit_row.addWidget(self.delete_btn)
        edit_row.addWidget(self.up_btn)
        edit_row.addWidget(self.down_btn)

        play_row = QHBoxLayout()
        play_row.addWidget(self.play_btn)
        play_row.addWidget(self.stop_btn)

        file_row = QHBoxLayout()
        file_row.addWidget(self.save_btn)
        file_row.addWidget(self.load_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        layout.addLayout(edit_row)
        layout.addLayout(play_row)
        layout.addLayout(file_row)
        layout.addWidget(self.status_label)

    def set_waypoints(self, labels: list[str]) -> None:
        current_row = self.list_widget.currentRow()
        self.list_widget.clear()
        self.list_widget.addItems(labels)
        if 0 <= current_row < self.list_widget.count():
            self.list_widget.setCurrentRow(current_row)

    def selected_index(self) -> int | None:
        row = self.list_widget.currentRow()
        return row if row >= 0 else None

    def select_row(self, row: int) -> None:
        self.list_widget.setCurrentRow(row)

    def set_playing(self, playing: bool) -> None:
        self.play_btn.setEnabled(not playing)
        self.stop_btn.setEnabled(playing)
        self.record_btn.setEnabled(not playing)
        self.delete_btn.setEnabled(not playing)
        self.up_btn.setEnabled(not playing)
        self.down_btn.setEnabled(not playing)
        self.load_btn.setEnabled(not playing)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)
