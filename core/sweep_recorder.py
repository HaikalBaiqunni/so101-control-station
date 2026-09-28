"""Pure min/max bookkeeping shared by both hardware calibration dialogs
(ui/fashionstar_calibration_dialog.py for the leader,
ui/dm_gripper_calibration_dialog.py for the follower's gripper) - kept in
core/ rather than ui/ specifically so it has no Qt dependency and is
unit-testable without an event loop, and so neither dialog module needs to
import from the other."""
from __future__ import annotations


class SweepRecorder:
    """Tracks running min/max per joint from a stream of {name: degrees}
    dicts - the pure bookkeeping behind a live calibration table."""

    def __init__(self, joint_names):
        self.joint_names = list(joint_names)
        self.mins: dict[str, float] = {}
        self.maxes: dict[str, float] = {}
        self.last: dict[str, float] = {}

    def reset(self) -> None:
        self.mins = {}
        self.maxes = {}

    def update(self, positions: dict[str, float]) -> None:
        for name, value in positions.items():
            self.last[name] = value
            if name not in self.mins or value < self.mins[name]:
                self.mins[name] = value
            if name not in self.maxes or value > self.maxes[name]:
                self.maxes[name] = value

    def spans(self) -> dict[str, tuple[float, float]]:
        return {
            name: (self.mins[name], self.maxes[name])
            for name in self.joint_names
            if name in self.mins and name in self.maxes
        }

    def is_complete(self) -> bool:
        spans = self.spans()
        return len(spans) == len(self.joint_names) and all(hi > lo for lo, hi in spans.values())
