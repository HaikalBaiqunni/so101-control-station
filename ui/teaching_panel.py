from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

PLAYBACK_SPEED_MIN_DEG_PER_S = 5
PLAYBACK_SPEED_MAX_DEG_PER_S = 120
PLAYBACK_SPEED_DEFAULT_DEG_PER_S = 60


class TeachingPanel(QGroupBox):
    """JAKA-style teach & playback: record the follower's current pose as a
    waypoint (however it got there - hand-guided with torque off, driven by
    the leader, or via the manual sliders), then replay the recorded sequence
    point-to-point. MainWindow owns the actual waypoint data and playback
    state machine - this is just the list/button widget."""

    record_requested = Signal()
    record_grip_requested = Signal()
    delete_requested = Signal()
    move_up_requested = Signal()
    move_down_requested = Signal()
    play_requested = Signal()
    stop_requested = Signal()
    save_requested = Signal()
    load_requested = Signal()
    delay_set_requested = Signal(int)  # ms, applies to the currently-selected waypoint
    row_selected = Signal(int)         # so MainWindow can push that waypoint's own delay into the spinbox

    def __init__(self, parent=None):
        super().__init__("TEACHING (waypoints)", parent)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self.row_selected)
        # No cap here used to mean this list's own (generous, mostly-empty
        # in normal use) preferred height was the single biggest contributor
        # to the left control column outgrowing one screen's worth of
        # height, pushing Gamepad/Keyboard Jog below the fold on every
        # launch. It already scrolls its OWN content once there are enough
        # waypoints to need it, so capping the OUTER height costs nothing
        # functionally - it only stops this one panel from claiming more
        # room than the rest of the stack when it doesn't need to.
        self.list_widget.setMaximumHeight(140)

        self.record_btn = QPushButton("Record Waypoint")
        self.record_btn.clicked.connect(self.record_requested)
        self.record_grip_btn = QPushButton("Record Grip")
        self.record_grip_btn.setToolTip(
            "Same as Record Waypoint, but pushes the gripper's recorded value all\n"
            "the way to its calibrated limit (closed or open, whichever it's\n"
            "already nearest to) instead of the exact measured contact position.\n"
            "Use this for a waypoint that must actually hold something - a plain\n"
            "recorded contact position has no spare closing force behind it, so\n"
            "the grip won't survive any disturbance."
        )
        self.record_grip_btn.clicked.connect(self.record_grip_requested)
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.delete_requested)
        self.up_btn = QPushButton("Move Up")
        self.up_btn.clicked.connect(self.move_up_requested)
        self.down_btn = QPushButton("Move Down")
        self.down_btn.clicked.connect(self.move_down_requested)

        # Pause held AFTER arriving at the selected waypoint, before moving on
        # to the next one - stored per-waypoint (see MainWindow._on_set_delay),
        # so "delay at sequence N" is just this applied while N is selected.
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60000)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setToolTip("Dwell time after arriving, before moving to the next waypoint.")
        self.set_delay_btn = QPushButton("Set Delay on Selected")
        self.set_delay_btn.clicked.connect(lambda: self.delay_set_requested.emit(self.delay_spin.value()))

        self.play_btn = QPushButton("Play Sequence")
        self.play_btn.clicked.connect(self.play_requested)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_requested)
        self.stop_btn.setEnabled(False)
        self.loop_check = QCheckBox("Loop")
        self.loop_check.setToolTip(
            "Restart from the first waypoint after the last one instead of\n"
            "stopping - for a repeated demo cycle. Stop still works mid-loop."
        )

        # Read live every tick (see MainWindow._start_playback_waypoint), not
        # just at Play time - dragging it mid-sequence takes effect on the
        # NEXT waypoint-to-waypoint move, so a demo that's running too fast
        # can be slowed down without stopping and restarting it.
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(PLAYBACK_SPEED_MIN_DEG_PER_S, PLAYBACK_SPEED_MAX_DEG_PER_S)
        self.speed_slider.setValue(PLAYBACK_SPEED_DEFAULT_DEG_PER_S)
        self.speed_slider.setToolTip(
            "Playback speed cap, in deg/s of the fastest-moving joint per "
            "waypoint move.\nLower = slower and safer. Takes effect on the "
            "next waypoint-to-waypoint move."
        )
        self.speed_label = QLabel(f"{PLAYBACK_SPEED_DEFAULT_DEG_PER_S} deg/s")
        self.speed_label.setMinimumWidth(60)
        self.speed_slider.valueChanged.connect(
            lambda v: self.speed_label.setText(f"{v} deg/s")
        )

        self.save_btn = QPushButton("Save...")
        self.save_btn.clicked.connect(self.save_requested)
        self.load_btn = QPushButton("Load...")
        self.load_btn.clicked.connect(self.load_requested)

        self.status_label = QLabel("")
        self.status_label.setObjectName("sectionCaption")
        self.status_label.setWordWrap(True)

        # A grid, not one row of five: five buttons side by side need ~600px, far
        # more than the control column has, which made the whole column scroll
        # sideways and pushed the Jog panel's right-hand buttons off-screen.
        edit_row = QGridLayout()
        edit_row.addWidget(self.record_btn, 0, 0)
        edit_row.addWidget(self.record_grip_btn, 0, 1)
        edit_row.addWidget(self.up_btn, 1, 0)
        edit_row.addWidget(self.down_btn, 1, 1)
        edit_row.addWidget(self.delete_btn, 2, 0, 1, 2)

        delay_label = QLabel("Delay after selected waypoint")
        delay_label.setObjectName("sectionCaption")
        delay_row = QHBoxLayout()
        delay_row.addWidget(self.delay_spin, 1)
        delay_row.addWidget(self.set_delay_btn)

        play_row = QHBoxLayout()
        play_row.addWidget(self.play_btn)
        play_row.addWidget(self.stop_btn)
        play_row.addWidget(self.loop_check)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed"))
        speed_row.addWidget(self.speed_slider, 1)
        speed_row.addWidget(self.speed_label)

        file_row = QHBoxLayout()
        file_row.addWidget(self.save_btn)
        file_row.addWidget(self.load_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        layout.addLayout(edit_row)
        layout.addWidget(delay_label)
        layout.addLayout(delay_row)
        layout.addLayout(play_row)
        layout.addLayout(speed_row)
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
        self.record_grip_btn.setEnabled(not playing)
        self.delete_btn.setEnabled(not playing)
        self.up_btn.setEnabled(not playing)
        self.down_btn.setEnabled(not playing)
        self.load_btn.setEnabled(not playing)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_delay_spin_value(self, ms: int) -> None:
        """Called by MainWindow on row_selected, so the spinbox shows the
        NEWLY-selected waypoint's own delay instead of whatever was last
        typed for a different one - without this, "Set Delay" after
        clicking a different row would silently apply a stale value."""
        self.delay_spin.blockSignals(True)
        self.delay_spin.setValue(ms)
        self.delay_spin.blockSignals(False)

    def playback_speed_deg_per_s(self) -> float:
        return float(self.speed_slider.value())

    def loop_enabled(self) -> bool:
        """Deliberately left enabled during playback (unlike the other
        buttons in set_playing) - unticking it mid-loop is how you let the
        current cycle finish and stop cleanly, instead of having to hit Stop
        and cut it off mid-motion."""
        return self.loop_check.isChecked()
