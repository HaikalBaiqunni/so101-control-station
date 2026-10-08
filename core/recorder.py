"""Episode recorder for imitation learning (read-only: it never moves a robot).

Each episode is sampled at a fixed rate and stored in a plain, dependency-light layout
that the GUI can write without pyarrow or a video toolchain:

    <root>/<dataset>/dataset.json              fps, joint names, camera key, robot type
    <root>/<dataset>/episode_000007/meta.json  task, frames, dropped_frames, duration_s, status
    <root>/<dataset>/episode_000007/data.npz   state (T x J), action (T x J), timestamp (T)
    <root>/<dataset>/episode_000007/<cam>.mp4  one video per camera (optional)

`tools/export_lerobot.py` turns that into a real LeRobotDataset (v3) with LeRobot's own
writer, so the on-disk format never has to be re-implemented here.

state  = the follower's measured joint angles (degrees)
action = what the operator commanded at that instant (degrees): the follower's goals when
         there are any, otherwise the measured pose.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass

import numpy as np

DEFAULT_FPS = 30
DEFAULT_CAMERA_KEY = "front"
EPISODE_RE = re.compile(r"^episode_(\d{6})$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_dataset_name(name: str) -> str:
    """A folder-safe dataset name; empty input falls back to a default."""
    cleaned = _SAFE_NAME_RE.sub("-", name.strip()).strip("-.")
    return cleaned or "dataset"


def episode_dir_name(index: int) -> str:
    return f"episode_{index:06d}"


@dataclass
class EpisodeInfo:
    index: int
    task: str
    frames: int
    dropped_frames: int
    duration_s: float
    status: str   # "ok" | "dropped" (some frames repeated) | "short"

    @property
    def label(self) -> str:
        if self.status == "dropped":
            return f"{self.dropped_frames} dropped frames"
        return self.status


def episode_status(frames: int, dropped: int, fps: int) -> str:
    if frames < fps // 2:   # under half a second is nearly always an accidental click
        return "short"
    return "dropped" if dropped else "ok"


class Dataset:
    """A folder of episodes. Cheap to construct; reads the disk when asked."""

    def __init__(self, root: str, name: str, joint_names: list[str], fps: int = DEFAULT_FPS,
                 camera_key: str | None = DEFAULT_CAMERA_KEY, robot_type: str = ""):
        self.name = safe_dataset_name(name)
        self.path = os.path.join(root, self.name)
        self.joint_names = list(joint_names)
        self.fps = int(fps)
        self.camera_key = camera_key
        self.robot_type = robot_type

    def ensure(self) -> None:
        """Create the folder and dataset.json; an existing dataset must match (joints, fps)."""
        os.makedirs(self.path, exist_ok=True)
        meta_path = os.path.join(self.path, "dataset.json")
        meta = {"fps": self.fps, "joint_names": self.joint_names,
                "camera_key": self.camera_key, "robot_type": self.robot_type}
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                old = json.load(f)
            if (old.get("joint_names") != self.joint_names or old.get("fps") != self.fps
                    or old.get("camera_key") != self.camera_key):
                raise ValueError(
                    f"Dataset '{self.name}' was recorded with joints {old.get('joint_names')}, "
                    f"{old.get('fps')} fps and camera '{old.get('camera_key')}' - every episode of a "
                    "dataset must match, so pick another dataset name (or start the same camera)."
                )
            return
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    def episode_indices(self) -> list[int]:
        if not os.path.isdir(self.path):
            return []
        found = []
        for entry in os.listdir(self.path):
            match = EPISODE_RE.match(entry)
            if match and os.path.exists(os.path.join(self.path, entry, "meta.json")):
                found.append(int(match.group(1)))
        return sorted(found)

    def next_index(self) -> int:
        indices = self.episode_indices()
        return indices[-1] + 1 if indices else 0

    def episode_path(self, index: int) -> str:
        return os.path.join(self.path, episode_dir_name(index))

    def list_episodes(self) -> list[EpisodeInfo]:
        infos = []
        for index in self.episode_indices():
            try:
                with open(os.path.join(self.episode_path(index), "meta.json"), encoding="utf-8") as f:
                    m = json.load(f)
                infos.append(EpisodeInfo(index, m.get("task", ""), int(m.get("frames", 0)),
                                         int(m.get("dropped_frames", 0)), float(m.get("duration_s", 0.0)),
                                         m.get("status", "ok")))
            except (OSError, ValueError):
                continue   # a half-written folder is not an episode
        return infos

    def delete_episode(self, index: int) -> None:
        shutil.rmtree(self.episode_path(index), ignore_errors=True)


class EpisodeRecorder:
    """Buffers one episode at a time. Call `add_sample` once per tick (driven by a QTimer in the
    GUI); `finish` writes it, `discard` throws it away. Pure logic, no Qt, no hardware."""

    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.recording = False
        self._reset_buffers()

    def _reset_buffers(self) -> None:
        self._state: list[list[float]] = []
        self._action: list[list[float]] = []
        self._times: list[float] = []
        self._dropped = 0
        self._last_frame_id: int | None = None
        self._writer = None
        self._last_image: np.ndarray | None = None
        self._index = -1
        self._video_path = ""
        self._frame_size: tuple[int, int] | None = None
        self._task = ""
        self._t0 = 0.0

    @property
    def frames(self) -> int:
        return len(self._times)

    @property
    def dropped_frames(self) -> int:
        return self._dropped

    @property
    def started_at(self) -> float:
        """time.monotonic() when the episode began; the GUI uses it to work out how many samples
        are due, so a late timer tick adds the missed samples instead of stretching the episode."""
        return self._t0

    @property
    def elapsed_s(self) -> float:
        return self.frames / self.dataset.fps

    def start(self, task: str) -> int:
        """Begin an episode; returns its index. Raises ValueError if the dataset cannot be used."""
        if self.recording:
            raise RuntimeError("already recording")
        self.dataset.ensure()
        self._reset_buffers()
        self._task = task.strip()
        self._index = self.dataset.next_index()
        os.makedirs(self.dataset.episode_path(self._index), exist_ok=True)
        self._t0 = time.monotonic()
        self.recording = True
        return self._index

    def add_sample(self, state: dict[str, float], action: dict[str, float],
                   frame: np.ndarray | None = None, frame_id: int | None = None) -> None:
        """One tick. `frame` is an RGB uint8 image; `frame_id` identifies it (e.g. a counter) so a
        tick that finds the same frame as the last one is counted as a dropped (repeated) frame."""
        if not self.recording:
            return
        names = self.dataset.joint_names
        self._state.append([float(state.get(n, 0.0)) for n in names])
        self._action.append([float(action.get(n, state.get(n, 0.0))) for n in names])
        self._times.append(len(self._times) / self.dataset.fps)
        if self.dataset.camera_key is None:
            return
        if frame is None or (frame_id is not None and frame_id == self._last_frame_id):
            self._dropped += 1
        self._last_frame_id = frame_id
        if frame is not None:
            self._write_frame(frame)
        elif self._writer is not None and self._last_image is not None:
            self._write_frame(self._last_image)   # repeat the last good frame so video stays in sync

    def _write_frame(self, frame: np.ndarray) -> None:
        import cv2  # already a dependency of the camera workers

        if self._writer is None:
            height, width = frame.shape[:2]
            self._frame_size = (width, height)
            self._video_path = os.path.join(self.dataset.episode_path(self._index), f"{self.dataset.camera_key}.mp4")
            self._writer = cv2.VideoWriter(self._video_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                           self.dataset.fps, self._frame_size)
            if not self._writer.isOpened():
                self._writer = None
                raise RuntimeError("could not open the video writer (is the disk full / path writable?)")
        if (frame.shape[1], frame.shape[0]) != self._frame_size:
            frame = cv2.resize(frame, self._frame_size)
        self._last_image = frame
        self._writer.write(cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_RGB2BGR))

    def _close_video(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def finish(self) -> EpisodeInfo:
        """Stop and write the episode to disk."""
        if not self.recording:
            raise RuntimeError("not recording")
        self.recording = False
        self._close_video()
        d = self.dataset
        folder = d.episode_path(self._index)
        np.savez(os.path.join(folder, "data.npz"),
                 state=np.asarray(self._state, dtype=np.float32).reshape(-1, len(d.joint_names)),
                 action=np.asarray(self._action, dtype=np.float32).reshape(-1, len(d.joint_names)),
                 timestamp=np.asarray(self._times, dtype=np.float32))
        info = EpisodeInfo(self._index, self._task, self.frames, self._dropped, self.elapsed_s,
                           episode_status(self.frames, self._dropped, d.fps))
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"task": info.task, "frames": info.frames, "dropped_frames": info.dropped_frames,
                       "duration_s": info.duration_s, "status": info.status,
                       "video": f"{d.camera_key}.mp4" if self._video_path else None}, f, indent=2)
        self._reset_buffers()
        return info

    def discard(self) -> None:
        """Stop and delete the episode in progress (no-op when idle)."""
        if not self.recording:
            return
        self.recording = False
        self._close_video()
        shutil.rmtree(self.dataset.episode_path(self._index), ignore_errors=True)
        self._reset_buffers()
