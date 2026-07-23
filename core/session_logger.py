"""
Lightweight session logger - one markdown file per app run, in `logs/`.

Interleaved, timestamped, greppable lines rather than separate "sections",
since events and position samples arrive in whatever order they actually
happen and a plain append-only file can't reflow itself:

    - `14:32:01.123` [EVENT] Follower connected on COM5
    - `14:32:01.456` [POS follower] pan=-88.5 lift=-92.9 elbow=96.4 wrist_flex=35.6 wrist_roll=6.5 gripper=-53.8
    - `14:32:02.001` [EVENT] Robot error: ...

Position samples are throttled per-source (default 2 Hz) - logging every
single 60Hz update would produce an unreviewable, multi-megabyte file within
minutes for no real benefit; 2 Hz is still plenty to reconstruct "what was
the arm doing when this error happened".
"""
from __future__ import annotations

import datetime
import os
import threading
import time

from .servo_bus import JOINT_ORDER

POSITION_LOG_INTERVAL_S = 0.5  # 2 Hz per source


class SessionLogger:
    def __init__(self, log_dir: str = "logs", position_log_interval_s: float = POSITION_LOG_INTERVAL_S):
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = os.path.join(log_dir, f"session_{ts}.md")
        self.position_log_interval_s = position_log_interval_s
        self._lock = threading.Lock()
        self._last_position_log: dict[str, float] = {}

        with open(self.path, "w", encoding="utf-8") as f:
            f.write(
                f"# SO-101 Control Session Log\n\n"
                f"Started: {datetime.datetime.now().isoformat()}\n\n"
                f"Format: `[EVENT]` for connects/disconnects/errors/mode changes, "
                f"`[POS <source>]` for a throttled ({1 / position_log_interval_s:.0f} Hz/source) "
                f"position snapshot in degrees.\n\n"
            )

    def _timestamp(self) -> str:
        return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def _write(self, line: str) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(f"- `{self._timestamp()}` {line}\n")

    def log_event(self, message: str) -> None:
        self._write(f"[EVENT] {message}")

    def log_positions(self, source: str, positions: dict[str, float]) -> None:
        """Call as often as you like (e.g. every frame) - internally
        throttled to `position_log_interval_s` per distinct `source`."""
        now = time.monotonic()
        last = self._last_position_log.get(source, 0.0)
        if now - last < self.position_log_interval_s:
            return
        self._last_position_log[source] = now

        # full names, not a shortened suffix - "elbow_flex" and "wrist_flex" would
        # otherwise both collapse to the ambiguous "flex="
        parts = " ".join(f"{name}={positions[name]:.1f}" for name in JOINT_ORDER if name in positions)
        self._write(f"[POS {source}] {parts}")
