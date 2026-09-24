"""A small synthetic 5-DoF arm shaped like an SO-101, shared by the kinematics and
jog tests. Joint names match the real robot's, so the same profile logic
applies, and it needs no third-party MJCF - the bundled repo has none for the
SO-101. Nothing here renders.
"""
from __future__ import annotations

SO101_LIKE = """
<mujoco>
  <worldbody>
    <geom type="box" size=".03 .03 .02"/>
    <body name="shoulder" pos="0 0 0.05">
      <joint name="shoulder_pan" axis="0 0 1" range="-110 110"/>
      <geom type="sphere" size=".02"/>
      <body name="upper_arm" pos="0 0 0.03">
        <joint name="shoulder_lift" axis="0 1 0" range="-100 100"/>
        <geom type="capsule" fromto="0 0 0 0.11 0 0" size=".01"/>
        <body name="forearm" pos="0.11 0 0">
          <joint name="elbow_flex" axis="0 1 0" range="-97 97"/>
          <geom type="capsule" fromto="0 0 0 0.13 0 0" size=".01"/>
          <body name="wrist" pos="0.13 0 0">
            <joint name="wrist_flex" axis="0 1 0" range="-95 95"/>
            <geom type="capsule" fromto="0 0 0 0.06 0 0" size=".01"/>
            <body name="gripper" pos="0.06 0 0">
              <joint name="wrist_roll" axis="1 0 0" range="-160 160"/>
              <geom type="box" size=".02 .01 .01"/>
              <site name="gripperframe" pos="0.05 0 0"/>
              <body name="jaw" pos="0.03 0.01 0">
                <joint name="gripper" axis="0 0 1" range="0 90"/>
                <geom type="box" size=".01 .005 .01"/>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
</mujoco>
"""

ARM5 = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
