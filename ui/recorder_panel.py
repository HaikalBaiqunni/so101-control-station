from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.recorder import DEFAULT_FPS, EpisodeInfo

MAX_EPISODE_S_DEFAULT = 30


class RecorderPanel(QGroupBox):
    """Record demonstration episodes for imitation learning. Read-only with respect to the
    robots: it samples what is already happening (follower pose, commanded goals, camera).
    MainWindow owns the sampling timer and the files; this is the form and the episode list."""

    start_requested = Signal(str, str, int)   # dataset name, task, max seconds
    stop_requested = Signal()                 # stop and save
    discard_requested = Signal()              # stop and delete the episode in progress
    delete_requested = Signal(int)            # delete a saved episode by index
    open_folder_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("RECORDER (imitation-learning episodes)", parent)
        note = QLabel(
            "Records the follower's pose, the commanded goals and the camera at "
            f"{DEFAULT_FPS} fps while you teleoperate. It never moves a robot. Export to a "
            "LeRobot dataset with tools/export_lerobot.py."
        )
        note.setObjectName("sectionCaption")
        note.setWordWrap(True)

        self.dataset_edit = QLineEdit("my-dataset")
        self.dataset_edit.setToolTip("Folder name under recordings/. A dataset keeps one robot and one frame rate.")
        self.task_edit = QLineEdit()
        self.task_edit.setPlaceholderText("What the operator is demonstrating, e.g. Put the block in the box")
        self.max_spin = QSpinBox()
        self.max_spin.setRange(3, 600)
        self.max_spin.setValue(MAX_EPISODE_S_DEFAULT)
        self.max_spin.setSuffix(" s")
        self.max_spin.setToolTip("The episode is saved automatically at this length.")

        self.readiness = QLabel("")
        self.readiness.setObjectName("sectionCaption")
        self.readiness.setWordWrap(True)

        self.record_btn = QPushButton("Record episode")
        self.record_btn.setObjectName("primaryButton")
        self.record_btn.clicked.connect(self._on_record_clicked)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setToolTip("Stop and throw this take away")
        self.discard_btn.clicked.connect(self.discard_requested)
        self.discard_btn.setEnabled(False)

        self.timer_label = QLabel("00:00 / 00:30")
        self.timer_label.setObjectName("poseReadout")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        self.episode_list = QListWidget()
        self.episode_list.setMinimumHeight(120)
        self.delete_btn = QPushButton("Delete selected")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.folder_btn = QPushButton("Open folder")
        self.folder_btn.clicked.connect(self.open_folder_requested)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel("Dataset"))
        row.addWidget(self.dataset_edit, 1)
        row.addWidget(QLabel("Max length"))
        row.addWidget(self.max_spin)
        layout.addLayout(row)
        layout.addWidget(self.task_edit)
        layout.addWidget(self.readiness)
        buttons = QHBoxLayout()
        buttons.addWidget(self.record_btn, 1)
        buttons.addWidget(self.discard_btn)
        layout.addLayout(buttons)
        layout.addWidget(self.timer_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        layout.addWidget(self.episode_list, 1)
        tail = QHBoxLayout()
        tail.addWidget(self.delete_btn)
        tail.addWidget(self.folder_btn)
        layout.addLayout(tail)
        self._recording = False
        self.set_progress(0.0, MAX_EPISODE_S_DEFAULT)

    # -- driven by MainWindow ------------------------------------------------
    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self.record_btn.setText("Stop and save" if recording else "Record episode")
        self.discard_btn.setEnabled(recording)
        for widget in (self.dataset_edit, self.task_edit, self.max_spin, self.delete_btn, self.episode_list):
            widget.setEnabled(not recording)   # nothing may change under a take in progress
        if not recording:
            self.set_progress(0.0, self.max_spin.value())

    def is_recording(self) -> bool:
        return self._recording

    def set_progress(self, elapsed_s: float, limit_s: float) -> None:
        def mmss(seconds: float) -> str:
            seconds = int(seconds)
            return f"{seconds // 60:02d}:{seconds % 60:02d}"
        self.timer_label.setText(f"{mmss(elapsed_s)} / {mmss(limit_s)}")
        self.progress.setValue(int(1000 * min(1.0, elapsed_s / max(limit_s, 1e-6))))

    def set_readiness(self, text: str, ok: bool) -> None:
        self.readiness.setText(text)
        self.readiness.setStyleSheet("" if ok else "color: #f5b544;")

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_episodes(self, episodes: list[EpisodeInfo]) -> None:
        self.episode_list.clear()
        for info in episodes:
            suffix = "" if info.status == "ok" else f"  ({info.label})"
            item = QListWidgetItem(f"#{info.index}  {info.duration_s:.1f} s  {info.frames} frames{suffix}"
                                   + (f"  - {info.task}" if info.task else ""))
            item.setData(Qt.UserRole, info.index)
            self.episode_list.addItem(item)
        self.episode_list.scrollToBottom()

    # -- internals -----------------------------------------------------------
    def _on_record_clicked(self) -> None:
        if self._recording:
            self.stop_requested.emit()
        else:
            self.start_requested.emit(self.dataset_edit.text(), self.task_edit.text(), self.max_spin.value())

    def _on_delete_clicked(self) -> None:
        item = self.episode_list.currentItem()
        if item is not None:
            self.delete_requested.emit(int(item.data(Qt.UserRole)))
