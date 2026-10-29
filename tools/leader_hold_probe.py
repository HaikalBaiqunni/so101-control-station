"""Probe how the Star Arm 102 leader's GRIPPER servo can be made to resist a hand.

Standalone (close the GUI first: it needs the serial port). Only servo id 6 is ever commanded. Every step is
short (STEP_S), ends with an unlock (free-spinning), and the whole run stops at once if the handle moves more
than ABORT_DEG from where it started. Keep a finger lightly on the handle and push it gently during each step;
the printed table shows the servo's current and power, which says whether it was energised at all.

    python tools/leader_hold_probe.py COM10
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motorbridge_smart_servo import FashionStarServo  # noqa: E402

from core.fashionstar_bus import BAUD, JOINT_IDS  # noqa: E402

SERVO = JOINT_IDS["gripper"]
STEP_S = 2.0
REST_S = 3.0
START_DELAY_S = 10.0
ABORT_DEG = 15.0
NUDGE_DEG = 5.0


def sample(bus):
    for _ in range(3):
        m = bus.sync_monitor([SERVO]).get(SERVO)
        if m is not None and getattr(m, "reliable", True):
            return m
    raise RuntimeError("no fresh reading from the gripper servo")


def run_step(bus, name, command, start_angle):
    print(f"\n--- {name}", flush=True)
    angles, currents, powers = [], [], []
    command()
    t_end = time.monotonic() + STEP_S
    while time.monotonic() < t_end:
        m = sample(bus)
        angles.append(m.angle_deg)
        currents.append(m.current_ma)
        powers.append(m.power_mw)
        if abs(m.angle_deg - start_angle) > ABORT_DEG:
            raise RuntimeError(f"handle moved {m.angle_deg - start_angle:+.1f} deg from the start: stopping")
        time.sleep(0.05)
    bus.unlock(SERVO)
    print(f"    angle {min(angles):.1f}..{max(angles):.1f} (start {start_angle:.1f})   "
          f"current max {max(currents):.0f} mA   power max {max(powers):.0f} mW", flush=True)


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM10"
    bus = FashionStarServo(port, baudrate=BAUD)
    try:
        print(f"Connected on {port}. Hold the gripper handle lightly; first step in {START_DELAY_S:g} s.", flush=True)
        time.sleep(START_DELAY_S)
        first, second = sample(bus).angle_deg, None
        time.sleep(0.1)
        second = sample(bus).angle_deg
        if abs(first - second) > 2.0:
            print("The handle was moving: keep it still and run again.")
            return 1
        start = second
        idle = sample(bus)
        print(f"Idle: angle {start:.1f}, current {idle.current_ma:.0f} mA, power {idle.power_mw:.0f} mW, "
              f"status {idle.status}", flush=True)
        steps = [
            ("1: position command to the SAME angle", lambda: bus.set_angle(SERVO, start, multi_turn=True)),
            (f"2: position command {NUDGE_DEG:+g} deg (the handle should move)",
             lambda: bus.set_angle(SERVO, start + NUDGE_DEG, multi_turn=True)),
            (f"3: position command {-NUDGE_DEG:+g} deg (the handle should move)",
             lambda: bus.set_angle(SERVO, start - NUDGE_DEG, multi_turn=True)),
            ("4: stop mode 0x11 (lock?), power 1000", lambda: bus.set_stop_mode(SERVO, 0x11, 1000)),
            ("5: stop mode 0x12 (damping?), power 500", lambda: bus.set_stop_mode(SERVO, 0x12, 500)),
            ("6: stop mode 0x12 (damping?), power 3000", lambda: bus.set_stop_mode(SERVO, 0x12, 3000)),
        ]
        for name, command in steps:
            time.sleep(REST_S)
            run_step(bus, name, command, start)
        print("\nDone: handle released.", flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - whatever happens the handle must end up free
        print(f"\nSTOPPED: {exc}", flush=True)
        return 2
    finally:
        try:
            bus.unlock(SERVO)
        except Exception as exc:  # noqa: BLE001
            print(f"unlock failed: {exc}", flush=True)
        bus.close()


if __name__ == "__main__":
    raise SystemExit(main())
