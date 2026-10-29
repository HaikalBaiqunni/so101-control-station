"""The leader gripper hold test: locks the leader's gripper handle for a moment so a 'wall' can be felt.
Every way out must leave the handle free, and nothing may be commanded unless the handle is still."""
from __future__ import annotations

import pytest

import core.fashionstar_leader_worker as lw
from core import fashionstar_bus as fsb
from core.fashionstar_leader_worker import FashionStarLeaderWorker


class FakeBus:
    def __init__(self, angle=100.0):
        self.angle = angle
        self.calls: list[tuple] = []
        self.readings: list[float] | None = None   # scripted readings, consumed first

    def read_gripper_angle(self):
        if self.readings:
            return self.readings.pop(0)
        return self.angle

    def hold_gripper(self, angle):
        self.calls.append(("hold", angle))

    def release_gripper(self):
        self.calls.append(("release",))


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(lw.time, "sleep", lambda s: None)
    w = FashionStarLeaderWorker("COMX")
    w.bus = FakeBus()
    w.events: list[tuple] = []
    w.errors: list[str] = []
    w.hold_changed.connect(lambda holding, text: w.events.append((holding, text)))
    w.error.connect(w.errors.append)
    return w


def test_a_hold_locks_the_handle_where_it_is_and_lets_go_by_itself(worker):
    worker.request_hold(2.0, 1.0)
    worker._service_hold(10.0)
    assert worker.bus.calls == [("hold", 100.0)] and worker.events[0][0] is True
    worker._service_hold(11.0)
    assert worker.bus.calls == [("hold", 100.0)]                   # a steady hold is commanded once
    worker._service_hold(12.1)
    assert worker.bus.calls[-1] == ("release",) and worker.events[-1] == (False, "done")
    assert worker._hold is None


def test_a_light_hold_pulses(worker):
    worker.request_hold(2.0, 0.25)
    worker._service_hold(10.0)                                     # phase 0: locked
    worker._service_hold(10.1)                                     # past 25% of the 0.2 s period: free
    worker._service_hold(10.21)                                    # next period: locked again
    assert [c[0] for c in worker.bus.calls] == ["hold", "release", "hold"]


def test_nothing_is_commanded_when_the_handle_is_moving(worker):
    worker.bus.readings = [100.0, 110.0]
    worker.request_hold(2.0, 1.0)
    worker._service_hold(10.0)
    assert worker.bus.calls == [] and worker._hold is None
    assert worker.events[-1][0] is False and "moving" in worker.events[-1][1]


def test_release_request_frees_the_handle_at_once(worker):
    worker.request_hold(5.0, 1.0)
    worker._service_hold(10.0)
    worker.request_release()
    worker._service_hold(10.1)
    assert worker.bus.calls[-1] == ("release",) and worker._hold is None


def test_pushed_far_from_the_held_angle_lets_go(worker):
    worker.request_hold(5.0, 1.0)
    worker._service_hold(10.0)
    worker.bus.angle = 100.0 + lw.HOLD_ESCAPE_DEG + 1.0
    worker._service_hold(10.1)
    assert worker.bus.calls[-1] == ("release",) and "pushed" in worker.events[-1][1]


def test_a_bus_error_ends_with_a_release_and_a_message(worker):
    worker.request_hold(5.0, 1.0)
    worker._service_hold(10.0)

    def boom():
        raise fsb.FashionStarBusError("no reply")
    worker.bus.read_gripper_angle = boom
    worker._service_hold(10.1)
    assert worker._hold is None and worker.bus.calls[-1] == ("release",)
    assert worker.errors and "no reply" in worker.errors[0]


def test_the_hold_time_is_capped(worker):
    worker.request_hold(60.0, 5.0)
    assert worker._hold_request == (lw.HOLD_MAX_S, 1.0)


def test_a_request_with_no_hold_running_only_clears(worker):
    worker.request_release()
    worker._service_hold(10.0)
    assert worker.bus.calls == [] and worker.events == []


def test_the_end_message_reports_the_servo_current(worker):
    from types import SimpleNamespace
    worker.request_hold(2.0, 1.0)
    worker._service_hold(10.0)
    worker.bus.last_gripper_monitor = SimpleNamespace(current_ma=180.0, power_mw=2100.0)
    worker._service_hold(10.5)
    worker._service_hold(12.5)
    assert "180 mA" in worker.events[-1][1] and "2100 mW" in worker.events[-1][1]
