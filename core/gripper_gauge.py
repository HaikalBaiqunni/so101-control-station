"""Use a linear-slide gripper as a rough caliper: close on an object gently, read the jaw angle
from the motor's encoder and convert it to a width.

Pure logic (no Qt, no hardware): the GUI feeds it the measured jaw angle ~20 times a second and
sends back whatever goal it returns.

How the force stays small: in MIT mode the motor's torque is kp * (setpoint - position). The
setpoint is therefore never allowed to run more than `preload_deg` ahead of the measured
position, which caps the torque at kp * preload no matter how long the jaws push on something.

How contact is found: the setpoint creeps toward "closed" at `speed_deg_s`. Free jaws follow it;
touching an object they stop while the setpoint pulls ahead. Contact = the setpoint leads by at
least `detect_lag_deg` AND the measured speed has dropped under `stall_speed_frac` of the
commanded speed for `stall_s`. The reading is the average angle once it has settled; the object
is released a few degrees and closed on again `repeats` times, always approaching from the same
side so backlash is the same every time.

Width = a + b * angle, fitted by least squares through reference points: the jaws closed on
nothing (0 mm) plus gauge blocks. Because references are measured with this same procedure, the
jaw's elastic give under the preload cancels out of the result.
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass

IDLE, CLOSING, SETTLING, REOPEN, RETURN, DONE, FAILED = (
    "idle", "closing", "settling", "reopen", "return", "done", "failed")

SPEED_RANGE = (1.0, 40.0)        # deg/s the setpoint may creep at
PRELOAD_RANGE = (0.5, 10.0)      # deg the setpoint may lead the jaw by (this is the force cap)
REPEATS_RANGE = (1, 9)
SPEED_WINDOW_S = 0.3             # measured speed is the position change over this window
OPEN_SPEED_FACTOR = 3.0          # reopen / return move this many times faster than the closing creep
POSITION_TOLERANCE_DEG = 1.5     # "arrived" when the jaw is this close to a reopen / return target


@dataclass(frozen=True)
class GaugeParams:
    close_sign: int = 1               # +1 when closing means the angle increases, -1 when it decreases
    limit_deg: float = 0.0            # hard end of travel in the closing direction (never commanded past it)
    speed_deg_s: float = 12.0
    preload_deg: float = 3.0
    detect_lag_deg: float = 2.0
    stall_speed_frac: float = 0.25
    stall_s: float = 0.2
    settle_s: float = 0.5
    sample_s: float = 0.4             # how long the settled reading is averaged over
    max_pass_spread_deg: float = 1.0  # a pass further than this from the earlier ones means friction fooled it
    stable_std_deg: float = 0.4       # a noisier reading than this is rejected (object slipping / still moving)
    min_travel_deg: float = 1.0       # contact sooner than this means "already touching or too weak to move"
    allow_zero_travel: bool = False   # the range finder may start right at an end of travel: that is a valid contact
    max_preload_deg: float = 0.0      # > preload: a stall is only believed after the push was raised stepwise to this
    repeats: int = 3
    reopen_deg: float = 8.0
    max_duration_s: float = 120.0

    def __post_init__(self):
        if self.close_sign not in (-1, 1):
            raise ValueError("close_sign must be +1 or -1")
        if not SPEED_RANGE[0] <= self.speed_deg_s <= SPEED_RANGE[1]:
            raise ValueError(f"speed must be {SPEED_RANGE[0]:g}-{SPEED_RANGE[1]:g} deg/s")
        if not PRELOAD_RANGE[0] <= self.preload_deg <= PRELOAD_RANGE[1]:
            raise ValueError(f"preload must be {PRELOAD_RANGE[0]:g}-{PRELOAD_RANGE[1]:g} deg")
        if not 0.0 < self.detect_lag_deg <= self.preload_deg:
            raise ValueError("detect lag must be above 0 and not above the preload")
        if self.max_preload_deg and not self.preload_deg <= self.max_preload_deg <= PRELOAD_RANGE[1]:
            raise ValueError("max preload must be between the preload and the preload ceiling")
        if not REPEATS_RANGE[0] <= self.repeats <= REPEATS_RANGE[1]:
            raise ValueError(f"repeats must be {REPEATS_RANGE[0]}-{REPEATS_RANGE[1]}")


@dataclass
class GaugeResult:
    angles: list[float]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.angles)

    @property
    def std(self) -> float:
        return statistics.stdev(self.angles) if len(self.angles) > 1 else 0.0


class GripperGauge:
    """One measurement run. Create, `start()`, then call `update()` every tick until `finished`."""

    def __init__(self, params: GaugeParams):
        self.p = params
        self.state = IDLE
        self.message = ""
        self.angles: list[float] = []
        self.start_angle = 0.0
        self.setpoint = 0.0
        self._t_prev = 0.0
        self._t_start = 0.0
        self._t_state = 0.0
        self._history: deque[tuple[float, float]] = deque()
        self._stall_time = 0.0
        self._close_start_pos = 0.0
        self._first_close = True
        self._hold = 0.0
        self._samples: list[float] = []
        self._reopen_target = 0.0
        self.hit_limit = False   # True when a pass ended at the end of travel instead of on an object
        # how far the setpoint may lead the jaw right now: raised while confirming a stall and then KEPT for the
        # rest of the pass, so friction that was beaten once does not stop the jaw again (stop-and-go motion)
        self._cap = params.preload_deg

    # -- state ---------------------------------------------------------------
    @property
    def finished(self) -> bool:
        return self.state in (DONE, FAILED)

    @property
    def result(self) -> GaugeResult | None:
        return GaugeResult(list(self.angles)) if self.state == DONE and self.angles else None

    @property
    def progress_text(self) -> str:
        n = len(self.angles)
        return {
            CLOSING: f"closing ({n}/{self.p.repeats} done)",
            SETTLING: f"touching, reading ({n}/{self.p.repeats} done)",
            REOPEN: f"releasing for the next pass ({n}/{self.p.repeats} done)",
            RETURN: "opening back to where it started",
            DONE: "done",
            FAILED: self.message,
        }.get(self.state, "")

    def _fail(self, message: str) -> None:
        self.state, self.message = FAILED, message

    def _enter(self, state: str, now: float) -> None:
        self.state, self._t_state = state, now

    # -- run -----------------------------------------------------------------
    def start(self, now: float, measured_deg: float) -> float:
        """Begin closing from the jaw's current position; returns the first goal (= where it is)."""
        self.start_angle = self.setpoint = measured_deg
        self._t_prev = self._t_start = now
        self._history.clear()
        self._history.append((now, measured_deg))
        self._begin_closing(now, measured_deg)
        return self.setpoint

    def _begin_closing(self, now: float, measured: float) -> None:
        self.setpoint = measured
        self._close_start_pos = measured
        self._stall_time = 0.0
        self._cap = self.p.preload_deg
        self._enter(CLOSING, now)

    def _measured_speed(self, now: float, measured: float) -> float | None:
        """Speed along the closing direction (deg/s), or None until a full window of history exists."""
        while len(self._history) > 1 and now - self._history[1][0] >= SPEED_WINDOW_S:
            self._history.popleft()
        t0, x0 = self._history[0]
        if now - t0 < SPEED_WINDOW_S * 0.9:
            return None
        return self.p.close_sign * (measured - x0) / (now - t0)

    def update(self, now: float, measured_deg: float | None) -> float | None:
        """One tick. Returns the goal angle to command, or None when there is nothing to send."""
        if self.finished or self.state == IDLE:
            return None
        if measured_deg is None or not math.isfinite(measured_deg):
            self._fail("lost the gripper's position feedback")
            return None
        if now - self._t_start > self.p.max_duration_s:
            self._fail("took too long - stopped")
            return None
        dt = min(0.2, max(0.0, now - self._t_prev))
        self._t_prev = now
        self._history.append((now, measured_deg))
        sign = self.p.close_sign

        if self.state == CLOSING:
            return self._closing(now, measured_deg, dt, sign)
        if self.state == SETTLING:
            return self._settling(now, measured_deg)
        if self.state == REOPEN:
            return self._moving_to(now, measured_deg, dt, self._reopen_target, nxt=CLOSING)
        if self.state == RETURN:
            return self._moving_to(now, measured_deg, dt, self.start_angle, nxt=DONE)
        return None

    def _closing(self, now: float, measured: float, dt: float, sign: int) -> float | None:
        p = self.p
        setpoint = self.setpoint + sign * p.speed_deg_s * dt
        ceiling = measured + sign * self._cap              # the force cap: never further ahead than this
        if sign * (setpoint - ceiling) > 0:
            setpoint = ceiling
        if sign * (setpoint - p.limit_deg) > 0:
            setpoint = p.limit_deg
        self.setpoint = setpoint

        # the jaw is at its calibrated end of travel: for the zero reference that IS the contact
        # (jaws meeting); for an object it means there was nothing between the jaws
        at_limit = sign * (measured - p.limit_deg) >= -POSITION_TOLERANCE_DEG * 0.5

        speed = self._measured_speed(now, measured)
        lead = sign * (setpoint - measured)
        pinned = sign * (setpoint - p.limit_deg) >= -1e-9   # the setpoint is already at the end of travel
        if (lead >= p.detect_lag_deg or pinned) and speed is not None and speed <= p.stall_speed_frac * p.speed_deg_s:
            self._stall_time += dt
        else:
            self._stall_time = 0.0
        max_cap = p.max_preload_deg or p.preload_deg
        if self._stall_time >= p.stall_s and not at_limit and self._cap < max_cap:
            # stopped, but at a gentle push that may only be friction: push harder (up to the ceiling)
            # before believing it is an end of travel
            self._cap = min(max_cap, self._cap + max(1.0, p.preload_deg))
            self._stall_time = 0.0
            # apply it now: at a slow creep the setpoint would take many seconds to lead by the new cap
            self.setpoint = measured + sign * self._cap
            if sign * (self.setpoint - p.limit_deg) > 0:
                self.setpoint = p.limit_deg
        elif self._stall_time >= p.stall_s or at_limit:
            travelled = sign * (measured - self._close_start_pos)
            if self._first_close and travelled < p.min_travel_deg and not at_limit and not p.allow_zero_travel:
                self._fail("the jaw did not move: it is already touching something, blocked, or the "
                           "gripper's kp is too low to move it")
                return None
            self._first_close = False
            self.hit_limit = self.hit_limit or at_limit or pinned
            hold = measured + sign * p.preload_deg   # constant torque while it is read
            if sign * (hold - p.limit_deg) > 0:
                hold = p.limit_deg
            self._hold = self.setpoint = hold
            self._samples = []
            self._enter(SETTLING, now)
        return self.setpoint

    def _settling(self, now: float, measured: float) -> float | None:
        p = self.p
        elapsed = now - self._t_state
        if elapsed >= p.settle_s:
            self._samples.append(measured)
        if elapsed >= p.settle_s + p.sample_s:
            if len(self._samples) < 3:
                self._fail("not enough position samples to read the contact")
                return None
            if statistics.pstdev(self._samples) > p.stable_std_deg:
                self._fail("the reading would not settle (object slipping or springy?)")
                return None
            reading = statistics.fmean(self._samples)
            if self.angles and abs(reading - statistics.fmean(self.angles)) > p.max_pass_spread_deg:
                self._fail("the passes disagree (stiction or a springy object): raise the preload or kp, "
                           "or measure again")
                return None
            self.angles.append(reading)
            if len(self.angles) < p.repeats:
                self._reopen_target = self.angles[-1] - p.close_sign * p.reopen_deg
                self._enter(REOPEN, now)
            else:
                self._enter(RETURN, now)
        return self._hold

    def _moving_to(self, now: float, measured: float, dt: float, target: float, nxt: str) -> float | None:
        """Move the setpoint to `target` (opening direction) and wait for the jaw to get there."""
        step = OPEN_SPEED_FACTOR * self.p.speed_deg_s * dt
        diff = target - self.setpoint
        setpoint = self.setpoint + max(-step, min(step, diff))
        # the same force cap as when closing: the setpoint stays within `preload_deg` of the jaw
        self.setpoint = max(measured - self.p.preload_deg, min(measured + self.p.preload_deg, setpoint))
        near = abs(measured - target) <= POSITION_TOLERANCE_DEG
        speed = self._measured_speed(now, measured)
        # parked: the setpoint is at the target but stiction holds the jaw a little short of it
        parked = abs(self.setpoint - target) < 1e-6 and speed is not None and abs(speed) <= 0.5
        if nxt == DONE:
            if near or parked:
                self.state = DONE
                if not near:
                    self.message = "the jaw stopped a little short of where it started"
            elif now - self._t_state > 8.0:
                self.state, self.message = DONE, "the jaw did not return all the way to its start"
        else:
            # released = clear of the object by half the reopen distance (and at least 2 deg)
            released = self.p.close_sign * (self.angles[-1] - measured) >= max(2.0, 0.5 * self.p.reopen_deg)
            if released and (near or parked):
                self._begin_closing(now, measured)
            elif now - self._t_state > 8.0:
                self._fail("the jaw would not release between passes")
                return None
        return self.setpoint


# ---------------------------------------------------------------- calibration
class GaugeCalibration:
    """width_mm = intercept + slope * angle_deg, from (angle, size) reference points."""

    def __init__(self, close_sign: int = 1, points: list[tuple[float, float]] | None = None):
        self.close_sign = close_sign
        self.points: list[tuple[float, float]] = [(float(a), float(m)) for a, m in (points or [])]

    def set_point(self, angle: float, size_mm: float) -> None:
        """Add a reference; measuring the same size again replaces the earlier reading."""
        self.points = [(a, m) for a, m in self.points if abs(m - size_mm) > 1e-6]
        self.points.append((float(angle), float(size_mm)))
        self.points.sort(key=lambda point: point[1])

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.points):
            del self.points[index]

    def clear(self) -> None:
        self.points = []

    @property
    def usable(self) -> bool:
        return len({round(a, 6) for a, _ in self.points}) >= 2 and len({m for _, m in self.points}) >= 2

    def fit(self) -> tuple[float, float] | None:
        """(slope mm/deg, intercept mm) or None until there are two distinct references."""
        if not self.usable:
            return None
        angles = [a for a, _ in self.points]
        sizes = [m for _, m in self.points]
        mean_a, mean_m = statistics.fmean(angles), statistics.fmean(sizes)
        denom = sum((a - mean_a) ** 2 for a in angles)
        slope = sum((a - mean_a) * (m - mean_m) for a, m in self.points) / denom
        return slope, mean_m - slope * mean_a

    def width_mm(self, angle: float) -> float | None:
        fit = self.fit()
        return None if fit is None else fit[1] + fit[0] * angle

    def max_residual_mm(self) -> float:
        fit = self.fit()
        if fit is None:
            return 0.0
        return max(abs(fit[1] + fit[0] * a - m) for a, m in self.points)

    def direction_ok(self) -> bool:
        """Closing must make the width smaller: slope has the opposite sign of close_sign."""
        fit = self.fit()
        return fit is None or fit[0] * self.close_sign < 0

    def in_span(self, angle: float, margin: float = 0.1) -> bool:
        """Is `angle` inside the references' span (plus a margin)? Outside it the result is an extrapolation."""
        angles = [a for a, _ in self.points]
        if len(angles) < 2:
            return False
        lo, hi = min(angles), max(angles)
        pad = (hi - lo) * margin
        return lo - pad <= angle <= hi + pad

    def to_dict(self) -> dict:
        return {"close_sign": self.close_sign, "points": [list(p) for p in self.points]}

    @classmethod
    def from_dict(cls, data: dict | None) -> GaugeCalibration:
        try:
            data = data or {}
            sign = int(data.get("close_sign", 1))
            return cls(sign if sign in (-1, 1) else 1, [(float(a), float(m)) for a, m in data.get("points", [])])
        except (TypeError, ValueError):
            return cls()


def describe_measurement(calibration: GaugeCalibration, result: GaugeResult) -> tuple[float, float, list[str]]:
    """(width mm, +/- mm, warnings) for a finished measurement."""
    width = calibration.width_mm(result.mean)
    if width is None:
        raise ValueError("the gauge is not calibrated yet")
    slope = abs(calibration.fit()[0])
    spread = slope * result.std
    warnings = []
    if not calibration.direction_ok():
        warnings.append("the references disagree with the chosen closing direction - check it")
    if not calibration.in_span(result.mean):
        warnings.append("outside the size range of your references: this is an extrapolation")
    if calibration.max_residual_mm() > 0.5:
        warnings.append(f"the references do not fit a straight line well (off by up to "
                        f"{calibration.max_residual_mm():.2f} mm): re-measure them")
    if width < -0.3:
        warnings.append("negative width: the closed reference is probably off")
    return width, spread, warnings
