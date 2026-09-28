"""Coverage for the B601-DM's REAL leader: Seeed's Star Arm 102 (FashionStar
UART smart servos, core/fashionstar_bus.py / core/fashionstar_leader_worker.py)
- replaces the earlier tests/test_dm_leader_teleop.py, which tested a wrong
assumption (that the leader was a second Damiao CAN arm) discovered and
corrected via real-hardware bring-up this session.

Two layers:
- Pure unit tests for FashionStarBus's name-mapping/unwrap/direction math
  against a mocked FashionStarServo (mirrors tests/test_damiao_bus.py's
  FakeMotorControl style).
- A MainWindow-level headless test confirming a connected leader relays
  positions to the follower under the FOLLOWER's own joint names
  (joint1..joint6/finger_left), and that the follower's MIT control mode is
  unaffected by the leader (ported from the old test file - that part of
  Phase 2.5 was correct and independent of which leader hardware this is).
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import core.dm_robot_worker as dm_robot_worker_module
import core.fashionstar_leader_worker as fashionstar_leader_worker_module
from core import fashionstar_bus as fsb
from ui import main_window as mw

JOINT_ORDER = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "finger_left")


class FakeFashionStarServo:
    """Stands in for motorbridge_smart_servo.FashionStarServo."""

    def __init__(self, port, baudrate=None, library_path=None):
        self.port = port
        self.angles: dict[int, float] = dict.fromkeys(fsb.JOINT_IDS.values(), 0.0)
        self.missing_ids: set[int] = set()
        self.closed = False

    def ping(self, servo_id: int) -> bool:
        return servo_id not in self.missing_ids

    def sync_monitor(self, servo_ids: list[int]):
        result = {}
        for sid in servo_ids:
            if sid in self.missing_ids:
                result[sid] = None
            else:
                result[sid] = SimpleNamespace(angle_deg=self.angles[sid])
        return result

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_fashionstar_servo(monkeypatch):
    monkeypatch.setattr(fsb, "FashionStarServo", FakeFashionStarServo)


# --------------------------------------------------------------------------- unit
class TestFashionStarBus:
    def test_connect_pings_all_seven_joints(self):
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        assert bus._bus is not None

    def test_connect_raises_when_a_servo_does_not_respond(self):
        real_init = FakeFashionStarServo.__init__

        def init_with_missing(self, port, baudrate=None, library_path=None):
            real_init(self, port, baudrate, library_path)
            self.missing_ids = {fsb.JOINT_IDS["wrist_yaw"]}

        FakeFashionStarServo.__init__ = init_with_missing
        try:
            bus = fsb.FashionStarBus("COM_FAKE")
            with pytest.raises(fsb.FashionStarBusError, match="wrist_yaw"):
                bus.connect()
        finally:
            FakeFashionStarServo.__init__ = real_init

    def test_read_all_positions_maps_fashionstar_names_to_follower_names(self):
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        # elbow_flex has direction=+1 and no gripper-style scaling - raw angle
        # at its range center should map straight through to joint3.
        lo, hi = fsb.JOINT_RANGES["elbow_flex"]
        bus._bus.angles[fsb.JOINT_IDS["elbow_flex"]] = (lo + hi) / 2.0
        positions = bus.read_all_positions_deg()
        assert "joint3" in positions
        assert positions["joint3"] == pytest.approx((lo + hi) / 2.0)

    def test_negative_direction_flips_sign(self):
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        # shoulder_pan has direction=-1 - a positive raw angle should come out
        # negative on joint1.
        bus._bus.angles[fsb.JOINT_IDS["shoulder_pan"]] = 30.0
        positions = bus.read_all_positions_deg()
        assert positions["joint1"] == pytest.approx(-30.0)

    def test_gripper_scale_is_applied_not_just_a_sign(self):
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        # gripper's direction is -6 (a widening SCALE, not just a sign) - a
        # small raw servo angle should come out scaled up by 6x (and negated)
        # on finger_left, clamped to the follower's own gripper range.
        bus._bus.angles[fsb.JOINT_IDS["gripper"]] = -10.0
        positions = bus.read_all_positions_deg()
        lo, hi = fsb.JOINT_RANGES["gripper"]
        expected = max(lo, min(hi, -10.0 * -6))
        assert positions["finger_left"] == pytest.approx(expected)

    def test_unwrap_handles_a_multi_turn_offset(self):
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        lo, hi = fsb.JOINT_RANGES["elbow_flex"]
        center = (lo + hi) / 2.0
        # Servo reports one extra full turn added to an otherwise-valid angle.
        bus._bus.angles[fsb.JOINT_IDS["elbow_flex"]] = center + 360.0
        positions = bus.read_all_positions_deg()
        assert positions["joint3"] == pytest.approx(center, abs=0.5)

    def test_missing_monitor_entry_raises(self):
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        bus._bus.missing_ids.add(fsb.JOINT_IDS["wrist_roll"])
        with pytest.raises(fsb.FashionStarBusError, match="wrist_roll"):
            bus.read_all_positions_deg()

    def test_deg_limits_default_to_follower_scaled_ranges(self):
        bus = fsb.FashionStarBus("COM_FAKE")
        lo, hi = bus.deg_limits("finger_left")
        assert (lo, hi) == fsb.JOINT_RANGES["gripper"]

    def test_positions_remap_onto_an_injected_follower_range_that_disagrees_numerically(self):
        # Regression test for a real bug found on hardware this session:
        # RobotProfile.preview_ranges["finger_left"] is (0.0, 100.0) - POSITIVE
        # - while FashionStar's own native JOINT_RANGES["gripper"] is
        # (-270.0, 0.0) - NEGATIVE, no overlap at all. Before this fix,
        # read_all_positions_deg() emitted values in the native (negative)
        # range regardless of what deg_limits() reported, so every value fell
        # outside the Jog panel's finger_left spinbox range and got silently
        # clamped to 0 by Qt - the gripper looked permanently frozen even
        # though the raw servo was responding correctly.
        follower_ranges = {"finger_left": (0.0, 100.0)}
        bus = fsb.FashionStarBus("COM_FAKE", ranges_deg=follower_ranges)
        native_lo, native_hi = fsb.JOINT_RANGES["gripper"]

        # Raw angle chosen so the native (unmapped) computation would land
        # near the native range's midpoint - i.e. clearly not a boundary
        # artifact.
        native_mid_raw_angle = ((native_lo + native_hi) / 2.0) / fsb.JOINT_DIRECTIONS["gripper"]
        bus.connect()
        bus._bus.angles[fsb.JOINT_IDS["gripper"]] = native_mid_raw_angle

        positions = bus.read_all_positions_deg()
        # Native fraction is 0.5 (midpoint) -> should map to the midpoint of
        # the INJECTED follower range (0..100), i.e. ~50, not clamp to 0 and
        # not report a value in the native -270..0 range at all.
        assert 0.0 <= positions["finger_left"] <= 100.0
        assert positions["finger_left"] == pytest.approx(50.0, abs=1.0)

    def test_deg_limits_reports_the_injected_follower_range_when_given_one(self):
        follower_ranges = {"finger_left": (0.0, 100.0)}
        bus = fsb.FashionStarBus("COM_FAKE", ranges_deg=follower_ranges)
        assert bus.deg_limits("finger_left") == (0.0, 100.0)

    def test_disconnect_closes_the_underlying_bus(self):
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        bus.disconnect()
        assert bus._bus is None

    def test_calibration_sweep_does_not_falsely_unwrap_a_non_wrapping_joint(self):
        # Regression test for a real bug found on hardware this session: an
        # earlier version of read_all_native_positions_deg() unwrapped every
        # joint against a generic +-180deg window "defensively" - but
        # shoulder_lift/elbow_flex never actually wrap (confirmed: a direct
        # raw-angle measurement with NO unwrap logic at all got clean,
        # sensible spans), so applying wrap-correction to them anyway
        # corrupted their real sweep readings into a near-exact +-180deg
        # clamp artifact (both joints' saved calibration came back as almost
        # exactly -180..180, a dead giveaway). A calibration sweep must
        # report the RAW angle (direction-adjusted only) with no wrap
        # correction, since discovering the true range is the whole point.
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        # A raw angle safely past +-180 on its own (no wraparound artifact
        # possible) - a real, wide, but legitimately single-turn reading.
        bus._bus.angles[fsb.JOINT_IDS["shoulder_lift"]] = 179.9
        natives = bus.read_all_native_positions_deg()
        direction = fsb.JOINT_DIRECTIONS["shoulder_lift"]
        assert natives["joint2"] == pytest.approx(179.9 * direction)

    def test_native_positions_are_unclamped_past_the_default_joint_ranges(self):
        # Regression coverage for the calibration sweep's whole point:
        # confirmed on real hardware this session that wrist_roll's true
        # native range (measured ~-156.9..157.9) is far wider than
        # JOINT_RANGES' hardcoded default (-90..90) - a sweep reading that
        # clamped to the default could never discover that.
        bus = fsb.FashionStarBus("COM_FAKE")
        bus.connect()
        bus._bus.angles[fsb.JOINT_IDS["wrist_roll"]] = 150.0  # well past the +-90 default
        natives = bus.read_all_native_positions_deg()
        # wrist_roll direction is -1, so a raw +150 comes out around -150.
        assert natives["joint6"] == pytest.approx(-150.0, abs=1.0)

    def test_native_ranges_deg_override_changes_the_fraction_source(self):
        # Regression test for the actual bug found on hardware: without a
        # measured native_ranges_deg override, wrist_roll's teleop motion
        # came out ~2x the real physical rotation because the fraction was
        # computed against the wrong (too narrow) default span.
        measured_native = {"wrist_roll": (-156.9, 157.9)}
        follower_ranges = {"joint6": (-179.9, 179.9)}
        bus_default = fsb.FashionStarBus("COM_FAKE", ranges_deg=follower_ranges)
        bus_calibrated = fsb.FashionStarBus(
            "COM_FAKE", ranges_deg=follower_ranges, native_ranges_deg=measured_native
        )
        bus_default.connect()
        bus_calibrated.connect()

        raw_angle = 45.0  # a real, modest rotation
        bus_default._bus.angles[fsb.JOINT_IDS["wrist_roll"]] = raw_angle
        bus_calibrated._bus.angles[fsb.JOINT_IDS["wrist_roll"]] = raw_angle

        default_result = bus_default.read_all_positions_deg()["joint6"]
        calibrated_result = bus_calibrated.read_all_positions_deg()["joint6"]
        # Same raw motion, same follower target range - but a MUCH smaller
        # apparent follower-side change once the true (wider) native span is
        # known, because the same raw delta is now a smaller fraction of it.
        assert abs(calibrated_result) < abs(default_result)


# --------------------------------------------------------------------------- MainWindow
class FakeDamiaoBus:
    def __init__(self, port, id_master_by_joint, ranges_deg, control_mode=None, mit_gains=None):
        self.ranges_deg = ranges_deg
        self.control_mode = control_mode
        self.mit_gains = mit_gains
        self._positions = dict.fromkeys(ranges_deg, 3.0)
        self.calibration = {}
        self.write_goals_calls: list[dict] = []

    def connect(self):
        pass

    def disconnect(self):
        pass

    def deg_limits(self, name):
        return self.ranges_deg.get(name, (-180.0, 180.0))

    def write_goals_deg(self, goals):
        self.write_goals_calls.append(dict(goals))
        self._positions.update(goals)

    def enable_torque(self, name=None):
        pass

    def disable_torque(self, name=None):
        pass

    def read_all_positions_deg(self):
        return dict(self._positions)

    def read_telemetry(self):
        return {n: {"position": p, "velocity": 0.0, "load": 0.0} for n, p in self._positions.items()}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fake_damiao_bus(monkeypatch):
    monkeypatch.setattr(dm_robot_worker_module, "DamiaoBus", FakeDamiaoBus)


@pytest.fixture(autouse=True)
def auto_dismiss_message_boxes(monkeypatch):
    # QMessageBox.warning()/.question() are native static methods in this
    # PySide6 build - patching .exec() (which looks like it should work) does
    # NOT intercept them; confirmed the hard way while writing the earlier
    # Damiao-leader tests. The statics themselves must be patched instead, or
    # any dialog hangs forever on the offscreen Qt platform.
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    win = mw.MainWindow()
    idx = win.robot_combo.findData("rebot_b601_dm")
    win.robot_combo.setCurrentIndex(idx)
    pump(qapp, 10)
    yield win
    # robot_worker/leader_worker may be real QThreads here (DmRobotWorker /
    # FashionStarLeaderWorker) - closeEvent already knows how to retire them
    # properly, so don't null the references first (see the equivalent
    # comment in the old test_dm_leader_teleop.py - orphaning a real QThread
    # like that hangs the process at interpreter exit).
    win.close()


def pump(qapp, iterations, delay=0.02):
    for _ in range(iterations):
        qapp.processEvents()
        time.sleep(delay)


def _assign_follower_mapping(win):
    mapping = {name: {"can_id": i + 1, "master_id": i + 0x11} for i, name in enumerate(JOINT_ORDER)}
    win._save_setting("dm_can_id_mapping", mapping)
    return mapping


class TestTwinGripperFractionFlip:
    # Regression coverage for a real bug found on hardware this session: a
    # calibration sweep only ever records numeric min/max, with no way to
    # know which physical extreme is open vs closed - so the fraction fed to
    # the twin's MJCF has a 50/50 chance of landing backwards against that
    # model's own convention, independent of (and not fixable via) the
    # leader relay's gripper_invert_override checkbox, which only affects
    # real hardware control. Confirmed wrong specifically for finger_left on
    # real B601-DM hardware; RobotProfile.twin_gripper_fraction_inverted
    # flips ONLY what MainWindow._positions_to_fractions computes for the
    # twin, never anything sent to real hardware.

    def test_gripper_fraction_is_flipped_for_this_profile(self, window):
        window.joint_deg_ranges["finger_left"] = (0.0, 100.0)
        window.current_positions["finger_left"] = 25.0  # fraction 0.25 unflipped
        fractions = window._positions_to_fractions()
        assert fractions["finger_left"] == pytest.approx(0.75)

    def test_other_joints_are_never_flipped(self, window):
        window.joint_deg_ranges["joint1"] = (-160.4, 160.4)
        window.current_positions["joint1"] = 0.0  # fraction 0.5
        fractions = window._positions_to_fractions()
        assert fractions["joint1"] == pytest.approx(0.5)

    def test_flip_applies_to_an_arbitrary_passed_in_positions_dict_too(self, window):
        # _positions_to_fractions is also used for waypoints/jog targets, not
        # just the live pose (self.current_positions) - the flip must apply
        # there too, since the twin renders those the same way.
        window.joint_deg_ranges["finger_left"] = (0.0, 100.0)
        fractions = window._positions_to_fractions({"finger_left": 100.0})  # fraction 1.0 unflipped
        assert fractions["finger_left"] == pytest.approx(0.0)


class TestFashionStarLeaderTeleop:
    def test_leader_connect_needs_no_calibration_file(self, window, qapp):
        # Unlike the SO-101 leader, this leader's zero point lives in the
        # servo's own flash (set via lerobot-calibrate) - the GUI's leader
        # connect must not require a calibration_path for this profile.
        window._on_leader_connect("COM_LEADER", "")
        pump(qapp, 10)
        assert window.leader_worker is not None

    def test_leader_positions_relay_to_the_follower_under_follower_joint_names(self, window, qapp):
        _assign_follower_mapping(window)
        window._on_dm_connect_real("COM_FOLLOWER")
        window._on_leader_connect("COM_LEADER", "")
        pump(qapp, 15)

        assert isinstance(window.leader_worker, fashionstar_leader_worker_module.FashionStarLeaderWorker)

        window.control_source_panel.leader_radio.setChecked(True)
        window._on_control_source_changed("leader")

        # Positions arrive already keyed by the FOLLOWER's joint names (the
        # name mapping happens inside FashionStarBus, not in MainWindow) -
        # simulate exactly what positions_updated actually emits.
        window._on_leader_positions(dict.fromkeys(JOINT_ORDER, 20.0))
        pump(qapp, 5)

        for name in JOINT_ORDER:
            assert name in window.robot_worker.last_goals

    def test_gripper_invert_checkbox_applies_to_finger_left_not_just_literal_gripper(self, window, qapp):
        # Regression test for a real bug found on hardware this session:
        # _relay_direction_flipped/_leader_deg_to_follower_deg used to check
        # the literal string "gripper" only, so the invert checkbox silently
        # did nothing at all for B601-DM's actual gripper joint ("finger_left").
        window._on_leader_connect("COM_LEADER", "")
        pump(qapp, 10)
        lo, hi = window.leader_worker.bus.deg_limits("finger_left")
        window.joint_deg_ranges["finger_left"] = (lo, hi)
        window.leader_deg_ranges["finger_left"] = (lo, hi)

        baseline = window._leader_deg_to_follower_deg("finger_left", lo)
        window.gripper_invert_override = True
        inverted = window._leader_deg_to_follower_deg("finger_left", lo)
        assert inverted != baseline
        assert inverted == pytest.approx(hi)

    def test_preview_without_a_follower_still_applies_the_same_conversion_as_relay(self, window, qapp):
        # Regression test: the preview branch (no follower connected yet)
        # used to update current_positions with RAW leader degrees, skipping
        # the gripper invert override entirely - what you saw previewing
        # didn't match what teleoperating would actually send once a
        # follower joined.
        window._on_leader_connect("COM_LEADER", "")
        pump(qapp, 10)
        assert window.robot_worker is None  # genuinely no follower yet

        lo, hi = window.leader_worker.bus.deg_limits("finger_left")
        window.joint_deg_ranges["finger_left"] = (lo, hi)
        window.leader_deg_ranges["finger_left"] = (lo, hi)
        window.gripper_invert_override = True

        window._on_leader_positions({"finger_left": lo})
        assert window.current_positions["finger_left"] == pytest.approx(hi)

    def test_leader_request_torque_is_a_safe_no_op(self, window, qapp):
        # _on_leader_connection_changed unconditionally calls
        # leader_worker.request_torque(False) after every connect - this must
        # not raise for a read-only FashionStar leader.
        window._on_leader_connect("COM_LEADER", "")
        pump(qapp, 10)
        window.leader_worker.request_torque(False)  # should not raise

    def test_leader_connect_is_unaffected_by_the_followers_mit_control_mode(self, window, qapp):
        _assign_follower_mapping(window)
        window._save_setting("dm_control_mode", "mit")

        window._on_dm_connect_real("COM_FOLLOWER")
        window._on_leader_connect("COM_LEADER", "")
        pump(qapp, 15)

        from core.dm_can import Control_Type

        assert window.robot_worker.bus.control_mode == Control_Type.MIT
        # The leader has no control mode concept at all (read-only UART
        # servos) - just confirm it connected fine alongside a MIT follower.
        assert window.leader_worker is not None

    def test_dm_connect_overlays_a_calibrated_gripper_range_onto_preview_ranges(self, window, qapp):
        # Regression test for the post-incident fix: a saved
        # dm_follower_calibrated_ranges entry must actually reach the
        # DamiaoBus this connect constructs, not just sit unused in settings.
        _assign_follower_mapping(window)
        window._save_setting("dm_follower_calibrated_ranges", {"finger_left": [5.0, 42.0]})

        window._on_dm_connect_real("COM_FOLLOWER")
        pump(qapp, 10)

        assert window.robot_worker.bus.ranges_deg["finger_left"] == (5.0, 42.0)
        # Every other joint still comes from the profile's own preview_ranges,
        # untouched by the calibration overlay.
        assert window.robot_worker.bus.ranges_deg["joint1"] == window.robot_profile.preview_ranges["joint1"]

    def test_mit_gains_retune_a_connected_follower_live_no_reconnect(self, window, qapp):
        # New feature: MIT kp/kd edited on the Setup tab while already
        # connected must reach the running DmRobotWorker's bus without a
        # disconnect/reconnect cycle - added directly after a live-tuning
        # session where reconnecting per gain tweak also cycles torque
        # off/on each time, unnecessarily re-requiring the arm to be
        # physically supported.
        _assign_follower_mapping(window)
        window._on_dm_connect_real("COM_FOLLOWER")
        pump(qapp, 10)

        new_gains = {"joint4": (24.0, 2.0)}
        window._on_dm_setup_mit_gains_changed(new_gains)
        pump(qapp, 10)

        assert window.robot_worker.bus.mit_gains == new_gains

    def test_mit_gains_change_resends_the_currently_held_target(self, window, qapp):
        # Regression test for a real gap found live-tuning on hardware: a
        # gains-only change used to sit unused until the next request_goal()
        # - controlMIT is a per-command send, not a register the motor keeps
        # re-applying on its own, so a joint that's just holding still (no
        # new goal in flight) never actually felt a kp/kd change until it
        # was jogged again. The worker must re-issue the last commanded
        # target itself so a live retune reaches a joint that's holding
        # still, not just one that's actively moving.
        _assign_follower_mapping(window)
        window._on_dm_connect_real("COM_FOLLOWER")
        pump(qapp, 10)

        window.robot_worker.request_goal("joint4", 12.5)
        pump(qapp, 5)
        calls_before = len(window.robot_worker.bus.write_goals_calls)

        window._on_dm_setup_mit_gains_changed({"joint4": (24.0, 2.0)})
        pump(qapp, 10)

        calls_after = window.robot_worker.bus.write_goals_calls[calls_before:]
        assert any(call.get("joint4") == 12.5 for call in calls_after)
