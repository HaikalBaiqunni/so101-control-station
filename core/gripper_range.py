"""Find a gripper's two ends of travel by itself: creep one way until the jaw stops, then the other.

Pure logic (no Qt, no hardware), built from two single-pass GripperGauge runs - the gauge already
knows how to creep with the force capped at kp * preload and to tell a stalled encoder from a moving
one. It never needs to know which end is "open": it just records both.

A run that does not find a stop within `max_travel_deg` is a failure, never a reason to push on, and
so is a jaw that moves less than `min_span_deg` between its two stops (blocked, or kp too low).
"""
from __future__ import annotations

from .gripper_gauge import DONE, FAILED, IDLE, GaugeParams, GripperGauge

SPEED_RANGE = (3.0, 40.0)


class GripperRangeFinder:
    """Same surface as GripperGauge (update / finished / state / start_angle / progress_text), so
    MainWindow can run it in the gauge's slot and every way of stopping the gauge stops this too."""

    def __init__(self, speed_deg_s: float = 15.0, preload_deg: float = 3.0,
                 max_travel_deg: float = 360.0, min_span_deg: float = 20.0, max_preload_deg: float = 0.0):
        self.speed_deg_s = speed_deg_s
        self.preload_deg = preload_deg
        self.max_travel_deg = max_travel_deg
        self.min_span_deg = min_span_deg
        self.max_preload_deg = max_preload_deg
        self.state = IDLE
        self.message = ""
        self.start_angle = 0.0
        self.first_end: float | None = None    # the end found in the increasing-angle direction
        self.span: tuple[float, float] | None = None
        self._sub: GripperGauge | None = None
        self._sign = 1

    @property
    def finished(self) -> bool:
        return self.state in (DONE, FAILED)

    @property
    def result(self) -> tuple[float, float] | None:
        """(low, high) end of travel in degrees once done."""
        return self.span if self.state == DONE else None

    @property
    def progress_text(self) -> str:
        if self.state == FAILED:
            return self.message
        side = "upper" if self._sign > 0 else "lower"
        return f"looking for the {side} end of travel" if not self.finished else "done"

    def _fail(self, message: str) -> None:
        self.state, self.message = FAILED, message

    def _begin(self, sign: int, now: float, measured: float) -> float:
        self._sign = sign
        params = GaugeParams(close_sign=sign, limit_deg=measured + sign * self.max_travel_deg,
                             speed_deg_s=self.speed_deg_s, preload_deg=self.preload_deg,
                             detect_lag_deg=min(2.0, self.preload_deg), repeats=1, allow_zero_travel=True,
                             max_preload_deg=self.max_preload_deg)
        self._sub = GripperGauge(params)
        self.state = "seeking"
        return self._sub.start(now, measured)

    def start(self, now: float, measured_deg: float) -> float:
        """Begin from where the jaw is; returns the first goal (= where it is)."""
        self.start_angle = measured_deg
        return self._begin(1, now, measured_deg)

    def update(self, now: float, measured_deg: float | None) -> float | None:
        if self.finished or self.state == IDLE or self._sub is None:
            return None
        goal = self._sub.update(now, measured_deg)
        sub = self._sub
        if sub.state == FAILED:
            self._fail(sub.message)
            return None
        if not sub.finished:
            return goal
        direction = "upper" if self._sign > 0 else "lower"
        if sub.hit_limit or not sub.angles:
            self._fail(f"found no {direction} end of travel within {self.max_travel_deg:g} deg: stopped")
            return None
        if self._sign > 0:
            self.first_end = sub.angles[0]
            return self._begin(-1, now, measured_deg)
        low, high = sub.angles[0], self.first_end
        if high - low < self.min_span_deg:
            self._fail(f"the jaw only moved {max(0.0, high - low):.1f} deg between its stops: it is blocked, "
                       "or kp is too low to move it")
            return None
        self.span = (low, high)
        self.state = DONE
        return goal
