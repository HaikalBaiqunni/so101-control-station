"""Tests for core/kinematics.py - Cartesian jogging on top of MuJoCo.

Two models: a small synthetic 5-DoF arm shaped like an SO-101 (so the 5-DoF
limitation is exercised without needing any third-party MJCF), and the
bundled 6-DoF reBot B601-DM for the full six-axis case. Nothing here renders,
so no GL is needed.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pytest
from synthetic_arm import ARM5, SO101_LIKE

from core.kinematics import (
    AXES,
    KinematicChain,
    TcpSpec,
    rpy_deg_from_matrix,
)

REBOT_XML = os.path.join(os.path.dirname(__file__), "..", "models", "rebot_b601_dm", "rebot_b601_dm.xml")
REBOT_ARM = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6")


@pytest.fixture
def arm5(tmp_path):
    path = tmp_path / "so101_like.xml"
    path.write_text(SO101_LIKE)
    return KinematicChain(str(path), ARM5)


@pytest.fixture(scope="module")
def rebot():
    if not os.path.exists(REBOT_XML):
        pytest.skip("bundled reBot model not present")
    return KinematicChain(
        REBOT_XML, REBOT_ARM, TcpSpec(body="link6", offset=(0.0, 0.0, 0.15539))
    )


def _tcp(chain, q):
    chain.set_q(q)
    return chain.tcp_pose()


def _pose_rotation_error(r_new, r_old):
    """Rotation vector w such that r_new ~= exp([w]x) r_old."""
    delta = r_new @ r_old.T
    return 0.5 * np.array([delta[2, 1] - delta[1, 2], delta[0, 2] - delta[2, 0], delta[1, 0] - delta[0, 1]])


class TestForwardKinematics:
    def test_straight_arm_reaches_the_summed_link_lengths(self, arm5):
        pos, rot = _tcp(arm5, np.zeros(5))
        assert pos == pytest.approx([0.11 + 0.13 + 0.06 + 0.05, 0.0, 0.08], abs=1e-9)
        assert rot == pytest.approx(np.eye(3), abs=1e-9)

    def test_pan_swings_the_tip_around_the_base(self, arm5):
        pos, _ = _tcp(arm5, np.array([math.pi / 2, 0, 0, 0, 0]))
        assert pos == pytest.approx([0.0, 0.35, 0.08], abs=1e-9)

    def test_site_is_preferred_over_a_body_fallback(self, arm5):
        assert arm5.tcp.kind == "site"
        assert "gripperframe" in arm5.tcp.description

    def test_body_fallback_uses_the_offset(self, tmp_path):
        path = tmp_path / "m.xml"
        path.write_text(SO101_LIKE.replace('<site name="gripperframe" pos="0.05 0 0"/>', ""))
        chain = KinematicChain(str(path), ARM5, TcpSpec(body="gripper", offset=(0.05, 0, 0)))
        assert chain.tcp.kind == "body"
        pos, _ = _tcp(chain, np.zeros(5))
        assert pos[0] == pytest.approx(0.35)

    def test_last_resort_is_the_body_carrying_the_last_arm_joint(self, tmp_path):
        path = tmp_path / "m.xml"
        path.write_text(SO101_LIKE.replace('<site name="gripperframe" pos="0.05 0 0"/>', ""))
        chain = KinematicChain(str(path), ARM5)
        assert "no TCP site found" in chain.tcp.description
        pos, _ = _tcp(chain, np.zeros(5))
        assert pos[0] == pytest.approx(0.30)  # the gripper body's own origin

    def test_joints_missing_from_the_model_are_dropped_not_fatal(self, tmp_path):
        path = tmp_path / "m.xml"
        path.write_text(SO101_LIKE)
        chain = KinematicChain(str(path), (*ARM5, "no_such_joint"))
        assert chain.joint_names == list(ARM5)

    def test_no_matching_joints_at_all_is_an_error(self, tmp_path):
        path = tmp_path / "m.xml"
        path.write_text(SO101_LIKE)
        with pytest.raises(ValueError, match="none of the arm joints"):
            KinematicChain(str(path), ("nope",))


class TestJacobian:
    @pytest.mark.parametrize("q", [
        [0.3, 0.4, -0.6, 0.5, 0.2],
        [-0.8, -0.3, 0.9, -0.4, 1.1],
        [1.0, 0.7, 0.2, 0.3, -0.5],
    ])
    def test_matches_finite_differences(self, arm5, q):
        q = np.array(q)
        arm5.set_q(q)
        jac = arm5.jacobian()
        pos0, rot0 = arm5.tcp_pose()
        h = 1e-6
        for i in range(5):
            qh = q.copy()
            qh[i] += h
            pos1, rot1 = _tcp(arm5, qh)
            assert jac[:3, i] == pytest.approx((pos1 - pos0) / h, abs=1e-5)
            assert jac[3:, i] == pytest.approx(_pose_rotation_error(rot1, rot0) / h, abs=1e-5)

    def test_shape_is_six_by_joint_count(self, arm5):
        assert arm5.jacobian().shape == (6, 5)


class TestJog:
    DT = 1 / 30

    def _drift(self, chain, q0, twist, frame, ticks=30):
        q = np.array(q0, dtype=float)
        pos0, rot0 = _tcp(chain, q)
        for _ in range(ticks):
            q = chain.step(q, twist, frame, self.DT).q
        pos1, rot1 = chain.tcp_pose()
        return pos1 - pos0, _pose_rotation_error(rot1, rot0)

    def test_world_z_goes_straight_up(self, arm5):
        d, _ = self._drift(arm5, [0.0, 0.5, -0.8, 0.3, 0.0], [0, 0, 0.02, 0, 0, 0], "world")
        assert d[2] > 0.005
        assert abs(d[0]) < 0.2 * d[2] and abs(d[1]) < 0.2 * d[2]

    def test_world_x_stays_level(self, arm5):
        d, _ = self._drift(arm5, [0.0, 0.5, -0.8, 0.3, 0.0], [0.02, 0, 0, 0, 0, 0], "world")
        assert d[0] > 0.005
        assert abs(d[2]) < 0.2 * d[0]

    def test_tool_and_world_frames_move_the_tool_differently(self, arm5):
        # With the wrist pitched, the tool's own x axis no longer points along
        # world x, so the same "+X" button must produce different motion.
        q0 = [0.0, 0.6, -1.0, 0.8, 0.0]  # elbow bent: a fully straight arm is singular along itself
        world_d, _ = self._drift(arm5, q0, [0.02, 0, 0, 0, 0, 0], "world")
        tool_d, _ = self._drift(arm5, q0, [0.02, 0, 0, 0, 0, 0], "tool")
        _, rot = _tcp(arm5, q0)
        tool_x = rot[:, 0]
        cos_world = world_d @ np.array([1, 0, 0]) / np.linalg.norm(world_d)
        cos_tool = tool_d @ tool_x / np.linalg.norm(tool_d)
        assert cos_world > 0.9
        assert cos_tool > 0.9
        assert abs(tool_x[2]) > 0.3, "test pose must actually tilt the tool"
        assert np.linalg.norm(world_d - tool_d) > 0.005

    def test_tool_frame_at_zero_pose_equals_world_frame(self, arm5):
        q0 = np.zeros(5)
        a = arm5.step(q0, [0.02, 0, 0, 0, 0, 0], "world", self.DT).q
        b = arm5.step(q0, [0.02, 0, 0, 0, 0, 0], "tool", self.DT).q
        assert a == pytest.approx(b, abs=1e-12)

    def test_tool_roll_spins_the_gripper_without_moving_its_tip(self, arm5):
        d, w = self._drift(arm5, [0.3, 0.4, -0.5, 0.2, 0.0], [0, 0, 0, 0.6, 0, 0], "tool")
        assert np.linalg.norm(d) < 1e-3
        assert np.linalg.norm(w) > 0.05

    def test_zero_twist_does_not_move(self, arm5):
        q0 = np.array([0.2, 0.3, -0.4, 0.1, 0.0])
        assert arm5.step(q0, np.zeros(6), "world", self.DT).q == pytest.approx(q0)

    def test_unknown_frame_is_rejected(self, arm5):
        with pytest.raises(ValueError, match="unknown frame"):
            arm5.step(np.zeros(5), np.zeros(6), "base", self.DT)


class TestSafety:
    def test_a_joint_at_its_limit_is_blocked_and_never_exceeds_it(self, arm5):
        _, hi = arm5.joint_limits()
        q0 = np.array([0.0, hi[1], 0.0, 0.0, 0.0])
        # world +x at full extension needs more shoulder_lift in this direction
        result = None
        q = q0.copy()
        for _ in range(20):
            result = arm5.step(q, [0, 0, -0.05, 0, 0, 0], "world", 1 / 30)
            q = result.q
            assert np.all(q <= hi + 1e-9)
        # whichever way it resolved, it must have stayed inside its range
        lo, hi = arm5.joint_limits()
        assert np.all(q >= lo - 1e-9) and np.all(q <= hi + 1e-9)

    def test_pushing_a_limited_joint_reports_it_as_blocked(self, arm5):
        lo, hi = arm5.joint_limits()
        q0 = np.array([hi[0], 0.3, -0.3, 0.2, 0.0])
        result = arm5.step(q0, [0, 0, 0, 0, 0, 0.5], "world", 1 / 30)  # yaw further positive
        assert "shoulder_pan" in result.blocked
        assert result.q[0] <= hi[0] + 1e-12

    def test_joint_rate_is_capped_and_reported(self, arm5):
        q0 = np.array([0.0, 0.3, -0.3, 0.2, 0.0])
        result = arm5.step(q0, [5.0, 5.0, 5.0, 5.0, 5.0, 5.0], "world", 1 / 30)
        assert result.saturated
        peak = np.max(np.abs(result.q - q0)) / (1 / 30)
        assert peak <= arm5.max_joint_rate + 1e-9


class TestReachability:
    def test_a_five_dof_arm_cannot_do_every_direction(self, arm5):
        arm5.set_q([0.6, 0.5, -0.6, 0.4, 0.3])
        reach = arm5.reachability("world")
        assert reach.shape == (6,)
        assert reach.min() < 0.9, "5 joints cannot span all 6 twist directions"
        assert reach.max() > 0.99

    def test_a_six_dof_arm_reaches_every_direction_away_from_singularities(self, rebot):
        rebot.set_q(np.radians([20, -70, -60, 10, 25, 15]))
        assert rebot.reachability("world") == pytest.approx(np.ones(6), abs=1e-6)
        assert rebot.reachability("tool") == pytest.approx(np.ones(6), abs=1e-6)

    def test_reachability_is_reported_per_axis(self, arm5):
        arm5.set_q([0.6, 0.5, -0.6, 0.4, 0.3])
        assert len(arm5.reachability("tool")) == len(AXES)


class TestSixDofRebot:
    def test_every_world_axis_is_jogged_faithfully(self, rebot):
        q0 = np.radians([20, -70, -60, 10, 25, 15])
        dt = 1 / 30
        pos0, rot0 = _tcp(rebot, q0)
        for i in range(3):
            twist = np.zeros(6)
            twist[i] = 0.03
            q = q0.copy()
            for _ in range(15):
                q = rebot.step(q, twist, "world", dt).q
            pos1, rot1 = rebot.tcp_pose()
            want = np.zeros(3)
            want[i] = 0.03 * 15 * dt
            assert pos1 - pos0 == pytest.approx(want, abs=2e-3)
            assert np.linalg.norm(_pose_rotation_error(rot1, rot0)) < 0.01, "pure translation must not rotate"

    def test_every_axis_rotation_is_jogged_faithfully(self, rebot):
        q0 = np.radians([20, -70, -60, 10, 25, 15])
        dt = 1 / 30
        for frame in ("world", "tool"):
            for i in range(3, 6):
                twist = np.zeros(6)
                twist[i] = 0.4
                q = q0.copy()
                pos0, rot0 = _tcp(rebot, q0)
                for _ in range(15):
                    q = rebot.step(q, twist, frame, dt).q
                pos1, rot1 = rebot.tcp_pose()
                assert np.linalg.norm(pos1 - pos0) < 3e-3, f"{frame} rot axis {i} moved the TCP"
                spun = _pose_rotation_error(rot1, rot0)
                assert np.linalg.norm(spun) == pytest.approx(0.4 * 15 * dt, rel=0.1)

    def test_tcp_sits_at_the_fingers(self, rebot):
        pos, _ = _tcp(rebot, np.zeros(6))
        assert pos == pytest.approx([0.2603, 0.0, 0.1917], abs=2e-3)


class TestMapping:
    def test_fractions_roundtrip(self, arm5):
        q = np.array([0.5, -0.4, 0.6, 0.1, 1.0])
        back = arm5.fractions_to_q(arm5.q_to_fractions(q))
        assert back == pytest.approx(q)

    def test_fraction_extremes_hit_the_joint_limits(self, arm5):
        lo, hi = arm5.joint_limits()
        assert arm5.fractions_to_q(dict.fromkeys(ARM5, 0.0)) == pytest.approx(lo)
        assert arm5.fractions_to_q(dict.fromkeys(ARM5, 1.0)) == pytest.approx(hi)

    def test_out_of_range_fractions_are_clamped(self, arm5):
        lo, hi = arm5.joint_limits()
        assert arm5.fractions_to_q(dict.fromkeys(ARM5, 7.0)) == pytest.approx(hi)

    def test_joints_not_mentioned_keep_their_value(self, arm5):
        arm5.set_q([0.1, 0.2, 0.3, 0.4, 0.5])
        q = arm5.fractions_to_q({"shoulder_pan": 0.5})
        assert q[1:] == pytest.approx([0.2, 0.3, 0.4, 0.5])


class TestRpy:
    def test_identity(self):
        assert rpy_deg_from_matrix(np.eye(3)) == pytest.approx((0, 0, 0))

    @pytest.mark.parametrize("roll,pitch,yaw", [(10, 20, 30), (-45, 5, 170), (80, -60, -20)])
    def test_roundtrip_through_zyx_composition(self, roll, pitch, yaw):
        r, p, y = map(math.radians, (roll, pitch, yaw))
        rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
        ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
        rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
        assert rpy_deg_from_matrix(rz @ ry @ rx) == pytest.approx((roll, pitch, yaw), abs=1e-6)

    def test_gimbal_lock_does_not_blow_up(self):
        p = math.pi / 2
        ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
        roll, pitch, yaw = rpy_deg_from_matrix(ry)
        assert pitch == pytest.approx(90.0)
        assert math.isfinite(roll) and math.isfinite(yaw)
