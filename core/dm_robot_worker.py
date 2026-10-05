"""
Background QThread driving the reBot B601-DM's real Damiao motors via
core/damiao_bus.py - the DM equivalent of core/workers.py's RobotWorker
(Feetech), with the SAME public signal/method surface
(positions_updated/telemetry_updated/error/connection_changed,
request_goal/request_torque/stop) so ui/main_window.py's existing
jog/telemetry code can hold either worker type in self.robot_worker without
needing to know which - that's what lets Joint AND Cartesian (World/Tool)
jogging both work against this robot for free: every jog path already
funnels through _drive_joint_programmatically -> robot_worker.request_goal,
never anything Feetech-specific.

Uses the _stop_requested flag pattern (core/dm_setup_worker.py's fix),
NOT the self._running = True unconditional-after-connect pattern RobotWorker
itself still had until this same session's Phase 2 pass fixed it too - see
that file's own comment for the exact race this avoids.
"""
from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QThread, Signal

from .damiao_bus import DamiaoBus, DamiaoBusError, speed_percent_to_mit_rate_deg_s, speed_percent_to_vel_limit
from .dm_can import Control_Type
from .gravity import GravityModel, clamp_ff
from .setpoint_slew import SetpointSlew

POLL_INTERVAL_S = 1 / 20  # see module docstring below for why this isn't 1/60 like RobotWorker
TELEMETRY_EVERY_N_CYCLES = 4  # -> 5 Hz, plenty for a diagnostics graph

# Damiao's protocol has no batched multi-motor read the way Feetech's
# GroupSyncRead gives ServoBus - core/dm_can.py's refresh_motor_status() is
# one request+reply round trip PER motor (confirmed directly this session).
# Seven of those every cycle is meaningfully slower than Feetech's single
# batched transaction, so this starts at a more conservative poll rate for
# the first real-hardware pass rather than assuming 60Hz is safe to copy
# over unchanged - worth re-measuring against the real bus and raising once
# actual round-trip timing on this hardware is known.


class DmRobotWorker(QThread):
    positions_updated = Signal(dict)   # {joint_name: degrees}
    telemetry_updated = Signal(dict)   # {joint_name: {position, velocity, load}}
    error = Signal(str)
    connection_changed = Signal(bool)
    gravity_status = Signal(bool, str)   # (model loaded, message)

    def __init__(
        self,
        port: str,
        id_master_by_joint: dict[str, tuple[int, int]],
        ranges_deg: dict[str, tuple[float, float]],
        control_mode: Control_Type = Control_Type.POS_VEL,
        mit_gains: dict[str, tuple[float, float]] | None = None,
        gravity_mjcf_path: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.gravity_mjcf_path = gravity_mjcf_path
        self.port = port
        self.id_master_by_joint = id_master_by_joint
        self.ranges_deg = ranges_deg
        self.control_mode = control_mode
        self.mit_gains = mit_gains
        self._pending_goals: dict[str, float] = {}
        self.last_goals: dict[str, float] = {}
        self._goals_lock = threading.Lock()
        self._torque_commands: queue.Queue = queue.Queue()
        # Lets the Setup tab's MIT kp/kd spinboxes retune a CONNECTED arm
        # live - added directly after a real bring-up session where
        # disconnect/reconnect-per-tweak (which also cycles torque off/on
        # each time, re-requiring the arm to be physically supported) proved
        # too slow for the gain-by-gain "raise it until it holds against
        # gravity, back off if it oscillates" process MIT tuning actually
        # needs. Only ever replaces self.bus.mit_gains wholesale - same
        # queue-handoff pattern as _torque_commands, safe because only the
        # worker thread ever reads/writes self.bus after connect().
        self._gains_commands: queue.Queue = queue.Queue()
        self._speed_percent = 30.0
        # MIT gravity feed-forward (core/gravity.py). Always starts OFF; the GUI only raises the
        # target after its own check against the motors' torque feedback has passed.
        self._gravity: GravityModel | None = None
        self._g_target = 0.0     # percent, requested
        self._g_now = 0.0        # percent, after the ramp
        self._g_sent_nonzero = False
        self._torque_on = False
        self._slew = SetpointSlew()          # MIT only: the setpoint streamed to each motor
        self._measured: dict[str, float] = {}
        self._last_tick = time.monotonic()
        self._speed_dirty = True   # applied on the worker thread, before the next command
        self._stop_requested = False
        self.bus: DamiaoBus | None = None

    # -- thread-safe public API (call from GUI thread) ------------------------
    def request_goal(self, name: str, degrees: float) -> None:
        with self._goals_lock:
            self._pending_goals[name] = degrees
            self.last_goals[name] = degrees

    def request_torque(self, enabled: bool, name: str | None = None) -> None:
        self._torque_commands.put((enabled, name))

    def request_speed_percent(self, percent: float) -> None:
        """Speed slider -> POS_VEL velocity cap (ignored in MIT, which has none).
        Only stores the value; the worker thread applies it, like every other command."""
        self._speed_percent = float(percent)
        self._speed_dirty = True

    def request_gravity_percent(self, percent: float) -> None:
        """0-100 % of the model's gravity torque, ramped in by the worker thread."""
        self._g_target = max(0.0, min(100.0, float(percent)))

    def request_mit_gains(self, gains: dict[str, tuple[float, float]]) -> None:
        self._gains_commands.put(dict(gains))

    GRAVITY_RAMP_PERCENT_PER_S = 40.0   # 0 -> 100 % takes 2.5 s: no torque step when it is switched on

    def _load_gravity_model(self) -> None:
        if not self.gravity_mjcf_path or self.bus.control_mode != Control_Type.MIT:
            return
        try:
            self._gravity = GravityModel(self.gravity_mjcf_path, list(self.ranges_deg))
            self.gravity_status.emit(True, f"model loaded ({self._gravity.total_mass_kg:.1f} kg)")
        except Exception as exc:   # a missing / odd MJCF must not take the connection down
            self._gravity = None
            self.gravity_status.emit(False, f"gravity model unavailable: {exc}")

    def _gravity_ff(self, dt: float) -> dict[str, float]:
        """Feed-forward torque per joint for this cycle ({} when off)."""
        step = self.GRAVITY_RAMP_PERCENT_PER_S * max(0.0, dt)
        self._g_now += max(-step, min(step, self._g_target - self._g_now))
        if self._g_now <= 0.0 or self._gravity is None or not self._torque_on:
            return {}
        names = self._gravity.joint_names
        if not names or any(n not in self._measured for n in names):
            return {}
        tau = self._gravity.torques({n: self._measured[n] for n in names})
        scale = self._g_now / 100.0
        return {n: clamp_ff(n, t * scale) for n, t in tau.items()}

    def _stream_mit(self, new_goals: dict[str, float], held: dict[str, float], dt: float) -> None:
        """MIT: move each joint's setpoint toward its goal at the Speed-slider rate and
        send it while it is still moving (plus once for every freshly requested goal).
        A joint that has arrived is not re-sent every cycle, so idle traffic is unchanged."""
        rate = speed_percent_to_mit_rate_deg_s(self._speed_percent)
        out: dict[str, float] = {}
        for name, goal in held.items():
            before = self._slew.setpoint.get(name)
            new = self._slew.step(name, goal, self._measured.get(name), rate, dt)
            if name in new_goals or before is None or new != before:
                out[name] = new
        ff = self._gravity_ff(dt)
        if ff:
            # The torque changes with the pose, so every held joint is re-sent each cycle (a joint
            # that has arrived still needs its new torque), and a joint with no goal yet is held
            # where it is, seeded once - never re-seeded from measured, which would only float it.
            for name in ff:
                if name not in held:
                    out[name] = self._slew.setpoint.setdefault(name, self._measured[name])
            for name in held:
                out[name] = self._slew.setpoint[name]
            self._g_sent_nonzero = True
        elif self._g_sent_nonzero and self._torque_on:
            # just switched off: send the held setpoints once more with zero torque, because a
            # motor keeps the last command's torque until it receives another
            for name in list(held) + [n for n in self._slew.setpoint if n not in held]:
                if name in self._slew.setpoint:
                    out[name] = self._slew.setpoint[name]
            self._g_sent_nonzero = False
        if out:
            try:
                if ff:
                    self.bus.write_goals_deg(out, feedforward=ff)
                else:
                    self.bus.write_goals_deg(out)
            except DamiaoBusError as exc:
                self.error.emit(str(exc))

    def stop(self) -> None:
        self._stop_requested = True

    # -- worker thread body ----------------------------------------------------
    def run(self) -> None:
        self.bus = DamiaoBus(
            self.port,
            self.id_master_by_joint,
            self.ranges_deg,
            control_mode=self.control_mode,
            mit_gains=self.mit_gains,
        )
        try:
            self.bus.connect()
        except DamiaoBusError as exc:
            self.error.emit(str(exc))
            self.connection_changed.emit(False)
            return

        self.connection_changed.emit(True)
        self._load_gravity_model()
        cycle = 0

        while not self._stop_requested:
            cycle += 1
            if self._speed_dirty:
                self._speed_dirty = False
                self.bus.vel_limit_rad_s = speed_percent_to_vel_limit(self._speed_percent)
            now = time.monotonic()
            dt = min(0.1, now - self._last_tick)   # a stalled loop must not turn into one big step
            self._last_tick = now
            with self._goals_lock:
                goals, self._pending_goals = self._pending_goals, {}
                held = dict(self.last_goals)
            if self.bus.control_mode == Control_Type.MIT:
                self._stream_mit(goals, held, dt)
            elif goals:
                try:
                    self.bus.write_goals_deg(goals)
                except DamiaoBusError as exc:
                    self.error.emit(str(exc))

            while True:
                try:
                    enabled, name = self._torque_commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    (self.bus.enable_torque if enabled else self.bus.disable_torque)(name)
                except DamiaoBusError as exc:
                    self.error.emit(str(exc))
                self._slew.reset(name)   # re-arming approaches the next goal from the real position
                if name is None:
                    self._torque_on = enabled
                if not enabled:
                    self._g_now = self._g_target = 0.0   # never leave feed-forward behind a torque-off
                    self._g_sent_nonzero = False

            new_gains_applied = False
            while True:
                try:
                    gains = self._gains_commands.get_nowait()
                except queue.Empty:
                    break
                self.bus.mit_gains = gains
                new_gains_applied = True
            if new_gains_applied and self.last_goals:
                # A gains-only change (no new position) would otherwise sit
                # unused until the next request_goal() - controlMIT is a
                # per-command send, not a register the motor keeps applying
                # on its own the way POS_VEL's onboard loop does, so
                # re-issuing the CURRENTLY-HELD target is what makes a live
                # kp/kd tweak actually reach a joint that's just sitting
                # still holding position (confirmed missing on real
                # hardware: raising joint4's kp live did nothing until it
                # was jogged again). Same target, so this can't itself cause
                # any new motion - only how hard the motor holds it.
                resend = {n: self._slew.setpoint.get(n, g) for n, g in self.last_goals.items()}
                try:
                    self.bus.write_goals_deg(resend)
                except DamiaoBusError as exc:
                    self.error.emit(str(exc))

            try:
                positions = self.bus.read_all_positions_deg()
                self._measured = dict(positions)
                self.positions_updated.emit(positions)
            except DamiaoBusError as exc:
                self.error.emit(str(exc))

            if cycle % TELEMETRY_EVERY_N_CYCLES == 0:
                try:
                    self.telemetry_updated.emit(self.bus.read_telemetry())
                except DamiaoBusError as exc:
                    self.error.emit(str(exc))

            time.sleep(POLL_INTERVAL_S)

        self.bus.disconnect()
        self.connection_changed.emit(False)
