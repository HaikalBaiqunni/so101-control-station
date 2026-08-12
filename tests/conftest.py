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
