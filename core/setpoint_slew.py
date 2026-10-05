"""Rate-limited setpoint, so a far-away target is approached instead of jumped to.

In MIT mode the motor torque is kp * (setpoint - position): hand it a target 90 degrees
away and it asks for 90 degrees' worth of torque at once, which is both violent and
(through a stiff kp) hard on the gears. Streaming a setpoint that moves toward the goal
at a bounded speed turns that into a smooth approach. Pure logic, no hardware or Qt.
"""
from __future__ import annotations


class SetpointSlew:
    def __init__(self) -> None:
        self.setpoint: dict[str, float] = {}

    def reset(self, name: str | None = None) -> None:
        """Forget the setpoint (all of them if name is None): the next goal is then
        approached from where the joint actually IS, not from a stale value."""
        if name is None:
            self.setpoint.clear()
        else:
            self.setpoint.pop(name, None)

    def step(self, name: str, goal: float, measured: float | None, rate_deg_s: float, dt: float) -> float:
        """Advance this joint's setpoint toward `goal` by at most rate * dt degrees."""
        current = self.setpoint.get(name)
        if current is None:
            current = goal if measured is None else measured   # first goal: start from the real position
        limit = max(0.0, rate_deg_s) * max(0.0, dt)
        new = current + max(-limit, min(limit, goal - current))
        self.setpoint[name] = new
        return new

    def at_goal(self, name: str, goal: float) -> bool:
        return name in self.setpoint and abs(self.setpoint[name] - goal) < 1e-6
