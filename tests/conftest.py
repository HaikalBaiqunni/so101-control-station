"""Test setup shared by the whole suite.

Two things have to happen before any `ui.*` module is imported:

- Qt needs a platform plugin it can use without a display, or every import
  that touches QtWidgets aborts on a CI runner.
- MuJoCo resolves and loads its GL backend at `import mujoco` time, not
  lazily on first render - and `ui.main_window` imports it transitively. On a
  bare runner with no GL libraries that import is itself the failure, before
  a single test has run.

  `disable` is the right answer rather than naming a real backend: nothing in
  the suite renders, so the tests need MuJoCo importable and able to parse a
  model, not able to draw. Naming `osmesa`/`egl` instead just moves the
  problem to "is that specific library installed on this machine".

Neither belongs inside the modules themselves - an app that forced offscreen
rendering on its own users would be useless.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MUJOCO_GL", "disable")

# Tests import `core.*` / `ui.*` as top-level packages, the same way main.py
# does - so the repo root has to be importable regardless of where pytest ran.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import dataclasses  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_default_twin_for_so101(monkeypatch, request):
    """The SO-101 profile now bundles a twin model and auto-loads it. Most tests build a
    MainWindow on that profile and drive it with their own synthetic kinematic chain, so
    keep them twin-free (the bundled model has its own test in test_shell.py)."""
    if request.node.get_closest_marker("real_so101_profile"):
        yield
        return
    from core import robot_profiles

    original = robot_profiles.PROFILES["so101"]
    monkeypatch.setitem(robot_profiles.PROFILES, "so101", dataclasses.replace(original, default_mjcf_path=""))
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "real_so101_profile: keep the SO-101 profile's bundled twin path")
    config.addinivalue_line("markers", "real_pylon: let the test use pypylon (the camera emulator)")


@pytest.fixture(autouse=True)
def _no_pylon_enumeration(monkeypatch, request):
    """MainWindow's Camera panel lists Basler cameras through pypylon. Keep the suite off
    real (and emulated) cameras unless a test opts in with @pytest.mark.real_pylon."""
    if request.node.get_closest_marker("real_pylon"):
        yield
        return
    from core import basler_camera

    monkeypatch.setattr(basler_camera, "list_basler_cameras", lambda: [])
    yield
