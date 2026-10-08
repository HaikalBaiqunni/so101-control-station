"""Episode recorder: core logic (no Qt) and its wiring in the main window."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

from core.recorder import Dataset, EpisodeRecorder, episode_status, safe_dataset_name

JOINTS = ["a", "b"]


def make(tmp_path, camera="front", fps=30):
    return Dataset(str(tmp_path), "demo", JOINTS, fps, camera, "test_robot")


def frame(value=0):
    return np.full((24, 32, 3), value, dtype=np.uint8)


def test_safe_dataset_name():
    assert safe_dataset_name(" my set/1 ") == "my-set-1"
    assert safe_dataset_name("   ") == "dataset"
    assert safe_dataset_name("../../x") == "x"


def test_episode_roundtrip_and_numbering(tmp_path):
    ds = make(tmp_path)
    rec = EpisodeRecorder(ds)
    assert rec.start("pick") == 0
    for i in range(40):
        rec.add_sample({"a": i, "b": -i}, {"a": i + 1}, frame(i), frame_id=i)
    info = rec.finish()
    assert (info.index, info.frames, info.dropped_frames, info.status) == (0, 40, 0, "ok")

    folder = ds.episode_path(0)
    data = np.load(os.path.join(folder, "data.npz"))
    assert data["state"].shape == (40, 2) and data["action"].shape == (40, 2)
    assert data["state"][5].tolist() == [5.0, -5.0]
    assert data["action"][5].tolist() == [6.0, -5.0]      # missing goal falls back to the measured pose
    assert data["timestamp"][3] == pytest.approx(3 / 30)
    assert os.path.getsize(os.path.join(folder, "front.mp4")) > 0
    meta = json.load(open(os.path.join(folder, "meta.json"), encoding="utf-8"))
    assert meta["task"] == "pick" and meta["frames"] == 40

    assert rec.start("pick") == 1                          # next episode continues the numbering
    rec.discard()
    assert ds.episode_indices() == [0]                     # a discarded take leaves nothing behind


def test_repeated_or_missing_frames_count_as_dropped(tmp_path):
    rec = EpisodeRecorder(make(tmp_path))
    rec.start("t")
    rec.add_sample({}, {}, frame(1), frame_id=1)
    rec.add_sample({}, {}, frame(1), frame_id=1)           # same frame id twice: camera did not deliver
    rec.add_sample({}, {}, None, frame_id=1)               # no frame at all
    for i in range(30):
        rec.add_sample({}, {}, frame(2), frame_id=10 + i)
    info = rec.finish()
    assert info.dropped_frames == 2 and info.status == "dropped"


def test_without_camera_nothing_counts_as_dropped_and_no_video(tmp_path):
    ds = make(tmp_path, camera=None)
    rec = EpisodeRecorder(ds)
    rec.start("t")
    for _ in range(20):
        rec.add_sample({"a": 1.0}, {})
    info = rec.finish()
    assert info.dropped_frames == 0
    assert not os.path.exists(os.path.join(ds.episode_path(0), "front.mp4"))


def test_short_episode_flagged():
    assert episode_status(5, 0, 30) == "short"
    assert episode_status(60, 0, 30) == "ok"


def test_dataset_must_stay_consistent(tmp_path):
    EpisodeRecorder(make(tmp_path)).start("t")
    with pytest.raises(ValueError):
        EpisodeRecorder(Dataset(str(tmp_path), "demo", ["a", "c"], 30, "front")).start("t")
    with pytest.raises(ValueError):
        EpisodeRecorder(make(tmp_path, camera=None)).start("t")
    with pytest.raises(ValueError):
        EpisodeRecorder(make(tmp_path, fps=15)).start("t")


def test_list_and_delete_episodes(tmp_path):
    ds = make(tmp_path, camera=None)
    rec = EpisodeRecorder(ds)
    for _ in range(2):
        rec.start("t")
        for _ in range(20):
            rec.add_sample({}, {})
        rec.finish()
    assert [e.index for e in ds.list_episodes()] == [0, 1]
    ds.delete_episode(0)
    assert [e.index for e in ds.list_episodes()] == [1]


# ---------------------------------------------------------------- main window wiring
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ui import main_window as mw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    w = mw.MainWindow()
    yield w
    w.robot_worker = None
    w.camera_worker = None
    w.close()


class FakeFollower:
    port = "COMX"
    bus = None

    def __init__(self):
        self.last_goals = {}

    def request_torque(self, enabled):
        pass


def connect(win):
    win.robot_worker = FakeFollower()
    win._on_connection_changed(True)


def test_record_needs_a_follower_and_a_task(win):
    panel = win.recorder_panel
    assert not panel.record_btn.isEnabled()
    connect(win)
    assert panel.record_btn.isEnabled()
    panel.task_edit.setText("   ")
    panel.record_btn.click()
    assert win.recorder is None or not win.recorder.recording
    assert "task" in panel.status_label.text().lower()


def test_record_samples_state_and_goals_then_saves(win):
    connect(win)
    panel = win.recorder_panel
    panel.dataset_edit.setText("unit")
    panel.task_edit.setText("wave")
    names = win.robot_profile.joint_order
    win.current_positions[names[0]] = 12.5
    win.robot_worker.last_goals = {names[0]: 20.0}
    panel.record_btn.click()
    assert win.recorder.recording and panel.is_recording()
    assert not panel.task_edit.isEnabled()                  # form is locked while a take runs
    for _ in range(40):
        win._recorder_sample()
    panel.record_btn.click()                                # "Stop and save"
    assert not win.recorder.recording and not panel.is_recording()
    infos = win._recorder_dataset_for("unit").list_episodes()
    assert len(infos) == 1 and infos[0].frames == 40 and infos[0].task == "wave"
    data = np.load(os.path.join(win._recorder_dataset_for("unit").episode_path(0), "data.npz"))
    assert data["state"][0][0] == pytest.approx(12.5) and data["action"][0][0] == pytest.approx(20.0)
    assert panel.episode_list.count() == 1


def test_disconnect_and_stop_discard_the_take(win):
    connect(win)
    win.recorder_panel.task_edit.setText("x")
    win.recorder_panel.record_btn.click()
    assert win.recorder.recording
    win._on_connection_changed(False)
    assert not win.recorder.recording
    assert win._recorder_dataset_for("my-dataset").list_episodes() == []

    connect(win)
    win.recorder_panel.record_btn.click()
    assert win.recorder.recording
    win.top_bar.stop_btn.click()
    assert not win.recorder.recording and "Stop" in win.recorder_panel.status_label.text()


def test_discard_button_and_delete(win, monkeypatch):
    connect(win)
    win.recorder_panel.task_edit.setText("x")
    win.recorder_panel.record_btn.click()
    win.recorder_panel.discard_btn.click()
    assert not win.recorder.recording

    win.recorder_panel.record_btn.click()
    for _ in range(40):
        win._recorder_sample()
    win._on_recorder_stop()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    win.recorder_panel.episode_list.setCurrentRow(0)
    win.recorder_panel.delete_btn.click()
    assert win._recorder_dataset_for("my-dataset").list_episodes() == []


def test_record_drawer_nav_and_palette(win):
    win.top_bar.nav_buttons["record"].click()
    assert win.drawer.currentWidget() is win.recorder_panel
    assert not win.drawer.isHidden()
    assert any(a.title == "Open Recorder" for a in win._palette_actions())
