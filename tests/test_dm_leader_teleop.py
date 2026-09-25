"""Phase 2.5 coverage: the Setup tab's Follower/Leader role selector (separate
gui_settings.json keys, no cross-contamination) and Damiao-to-Damiao leader
teleop (both arms share the same joint names, so the relay needs no per-joint
mapping - unlike the SO-101 leader, which never matches B601-DM's joint set at
all and is intentionally NOT wired up as a leader for this profile).

Headless (Qt's offscreen platform) - builds a real MainWindow on the
reBot B601-DM profile and drives it through the same connect methods the UI
buttons trigger, with core.dm_robot_worker.DamiaoBus monkeypatched to a
FakeBus so no real serial port is touched.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

import core.dm_robot_worker as dm_robot_worker_module
from ui import main_window as mw

JOINT_ORDER = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "finger_left")


class FakeBus:
    """Stands in for DamiaoBus - enough behavior for the connect/torque/relay
    path under test, nothing about the real CAN protocol."""

    def __init__(self, port, id_master_by_joint, ranges_deg, control_mode=None, mit_gains=None):
        self.port = port
        self.ranges_deg = ranges_deg
        self.control_mode = control_mode
        self.mit_gains = mit_gains
        self._positions = dict.fromkeys(ranges_deg, 3.0)
        self.torque_enabled = False
        self.calibration = {}

    def connect(self):
        pass

    def disconnect(self):
        pass

    def deg_limits(self, name):
        return self.ranges_deg.get(name, (-180.0, 180.0))

    def write_goals_deg(self, goals):
        self._positions.update(goals)

    def enable_torque(self, name=None):
        self.torque_enabled = True

    def disable_torque(self, name=None):
        self.torque_enabled = False

    def read_all_positions_deg(self):
        return dict(self._positions)

    def read_telemetry(self):
        return {n: {"position": p, "velocity": 0.0, "load": 0.0} for n, p in self._positions.items()}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fake_damiao_bus(monkeypatch):
    monkeypatch.setattr(dm_robot_worker_module, "DamiaoBus", FakeBus)


@pytest.fixture(autouse=True)
def auto_dismiss_message_boxes(monkeypatch):
    # QMessageBox.warning()/.question() are native static methods in this
    # PySide6 build - they open a real modal event loop internally WITHOUT
    # going through a Python-visible .exec() call, so patching .exec() (which
    # looks like it should work, and is what an earlier ad-hoc debug script
    # in this repo's history assumed) does NOT intercept them; confirmed the
    # hard way, this hung indefinitely on the offscreen Qt platform until the
    # statics themselves were patched instead.
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
    # Unlike test_jog.py's window fixture, robot_worker/leader_worker here are
    # REAL QThreads (DmRobotWorker) whenever a test connected one - closeEvent
    # already knows how to retire those properly (_on_disconnect/
    # _on_leader_disconnect -> _retire_worker -> stop() + wait()), so don't
    # null the references first or those threads would be orphaned with
    # nothing left to stop them, hanging the process at interpreter exit.
    win.close()


def pump(qapp, iterations, delay=0.02):
    for _ in range(iterations):
        qapp.processEvents()
        time.sleep(delay)


def _assign_all(win, key: str):
    mapping = {name: {"can_id": i + 1, "master_id": i + 0x11} for i, name in enumerate(JOINT_ORDER)}
    settings = win._load_settings()
    settings[key] = mapping
    win._save_setting(key, mapping)
    return mapping


class TestSetupRoleSelector:
    def test_default_role_is_follower(self, window):
        assert window._dm_setup_role == "follower"
        assert window._dm_mapping_settings_key() == "dm_can_id_mapping"

    def test_switching_role_swaps_the_settings_key_and_does_not_cross_contaminate(self, window, qapp):
        follower_mapping = _assign_all(window, "dm_can_id_mapping")

        window.dm_setup_panel.role_combo.setCurrentIndex(
            window.dm_setup_panel.role_combo.findData("leader")
        )
        pump(qapp, 2)
        assert window._dm_setup_role == "leader"
        assert window._dm_mapping_settings_key() == "dm_can_id_mapping_leader"
        # Nothing assigned yet for "leader" - the follower's own mapping must
        # not leak into the panel's checklist for the other role.
        assert window.dm_setup_panel._mapping == {}

        # Assigning a joint while in the leader role must land under the
        # LEADER key, leaving the follower's own mapping untouched.
        window._dm_setup_role = "leader"
        window._on_dm_id_assigned(0x01, 0x01, 0x11)
        leader_settings = window._load_settings()
        assert "joint1" in leader_settings.get("dm_can_id_mapping_leader", {})
        assert leader_settings.get("dm_can_id_mapping") == follower_mapping


class TestDamiaoLeaderTeleop:
    def test_leader_connect_refuses_when_motors_not_fully_assigned(self, window, qapp):
        window._on_dm_leader_connect("COM_LEADER")
        assert window.leader_worker is None

    def test_leader_and_follower_connect_and_relay_matching_joint_names(self, window, qapp):
        _assign_all(window, "dm_can_id_mapping")
        _assign_all(window, "dm_can_id_mapping_leader")

        window._on_dm_connect_real("COM_FOLLOWER")
        window._on_dm_leader_connect("COM_LEADER")
        pump(qapp, 15)

        assert window.robot_worker is not None
        assert window.leader_worker is not None

        # The leader is meant to be moved by hand - always free-spinning,
        # exactly like the existing SO-101 leader relay.
        assert window.leader_worker.bus.torque_enabled is False

        window.control_source_panel.leader_radio.setChecked(True)
        window._on_control_source_changed("leader")

        window._on_leader_positions(dict.fromkeys(JOINT_ORDER, 42.0))
        pump(qapp, 5)

        # No per-joint name remapping needed - both arms share the same
        # joint vocabulary, unlike the SO-101 leader (shoulder_pan, ...,
        # gripper), which this profile deliberately never wires up as a
        # leader source at all.
        for name in JOINT_ORDER:
            assert name in window.robot_worker.last_goals

    def test_leader_connect_is_unaffected_by_the_followers_mit_control_mode(self, window, qapp):
        _assign_all(window, "dm_can_id_mapping")
        _assign_all(window, "dm_can_id_mapping_leader")
        window._save_setting("dm_control_mode", "mit")

        window._on_dm_connect_real("COM_FOLLOWER")
        window._on_dm_leader_connect("COM_LEADER")
        pump(qapp, 15)

        from core.dm_can import Control_Type

        assert window.robot_worker.bus.control_mode == Control_Type.MIT
        # Leader always connects POS_VEL regardless of the follower's chosen
        # mode - its own control mode never actually drives anything since
        # torque is forced off right after connect.
        assert window.leader_worker.bus.control_mode == Control_Type.POS_VEL
