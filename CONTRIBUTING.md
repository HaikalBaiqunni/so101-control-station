# Contributing

Thanks for looking. This project exists so that a beginner's first hour with an
SO-101 isn't spent fighting tooling — contributions that serve that goal are
the most welcome ones.

## Especially useful contributions

- **First-time setup reports.** If you set this up from scratch and something
  was confusing, unclear or broken, that's a bug even if nothing crashed. Say
  where you got stuck.
- **Hardware confirmations.** Several numbers in this codebase are
  cross-checked rather than vendor-documented — most notably
  `Present_Velocity`'s unit (see `convert_telemetry()` in
  `ui/telemetry_panel.py`). If you confirm or refute one on real hardware,
  that's genuinely valuable.
- **Platform coverage.** Development happens on Windows. Linux and macOS
  reports, especially around serial permissions and camera backends, are
  under-represented.
- **Translations** of `docs/GETTING_STARTED.md`.

## Development setup

```bash
git clone https://github.com/HaikalBaiqunni/so101-control-station.git
cd so101-control-station
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

Run the checks the CI runs:

```bash
python -m pytest
```

```bash
python -m ruff check .
```

Tests need no hardware — `tests/conftest.py` pins Qt to its offscreen platform.
Anything requiring a real servo can't be covered in CI and has to be verified
by hand; say so explicitly in the PR.

## Code conventions

Read a few existing files before writing new ones — the style is consistent
and somewhat unusual on one point:

- **Comments explain *why*, and they're load-bearing.** Where a decision looks
  arbitrary or wrong at a glance, there is almost always a comment explaining
  what was tried and what broke. `CameraWorker.run()` and
  `_drive_joint_programmatically()` are representative. Please keep this up:
  a non-obvious fix without its rationale tends to get "cleaned up" back into
  a bug later.
- **All blocking I/O goes on a `QThread`.** The GUI thread never waits on a
  servo, a camera or a render. See
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#threading-model).
- **Panels are dumb.** UI widgets emit signals and render what they're given;
  `MainWindow` owns state and arbitration. A panel reaching into a worker is a
  design smell.
- **Never bypass the safety clamp.** Commanded positions go through
  `write_goals_deg()`. If you add a control source, route it through
  `_drive_joint_programmatically()`.
- Line length 110, enforced by ruff. `python -m ruff check --fix .` handles
  import ordering.

## Adding a control source

1. Add it to `SOURCES` in `ui/control_source_panel.py` with a radio button.
2. Drive joints via `MainWindow._drive_joint_programmatically()` — it handles
   clamping, state and the widget echo.
3. Gate your tick on `self.control_source == "<yours>"` **and**
   `self._playback_index is None`, so playback can't be fought.

`_keyboard_jog_tick()` in `ui/main_window.py` is the smallest complete example.

## Pull requests

- One logical change per PR.
- Say what you tested, and be explicit about what you **couldn't** test —
  "verified on hardware" and "compiles and looks right" are very different
  claims for a robotics tool, and nobody will think less of the latter.
- If you changed behaviour a user would notice, update the relevant doc under
  `docs/` and add a `CHANGELOG.md` entry under *Unreleased*.
- If you touched anything that writes to a servo's EEPROM (ids, baudrates,
  homing offsets, position limits), say so prominently. Those writes are
  permanent until changed again.

## Reporting bugs

Use the issue template. The most important attachment is the session log — the
app writes a timestamped markdown file to `logs/` every run, with every
connect, disconnect, error and mode change plus a 2 Hz position feed. Its path
is in the status bar.

## Code of conduct

Be decent to each other. Assume the person asking an obvious question is
exactly who this project was built for.
