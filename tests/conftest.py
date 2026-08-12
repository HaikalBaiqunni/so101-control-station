"""Test setup shared by the whole suite.

Two things have to happen before any `ui.*` module is imported:

- Qt needs a platform plugin it can use without a display, or every import
  that touches QtWidgets aborts on a CI runner.
- MuJoCo needs its GL backend pinned for the same reason main.py pins it (see
  the comment there about WinError 6), since `ui.main_window` transitively
  imports it.

Neither belongs inside the modules themselves - an app that forced offscreen
rendering on its own users would be useless.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MUJOCO_GL", "wgl" if sys.platform == "win32" else "osmesa")

# Tests import `core.*` / `ui.*` as top-level packages, the same way main.py
# does - so the repo root has to be importable regardless of where pytest ran.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
