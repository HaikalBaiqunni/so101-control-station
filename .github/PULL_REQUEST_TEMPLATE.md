## What this changes

<!-- One paragraph. What behaviour is different afterwards? -->

## Why

<!-- The problem being solved. Link an issue if there is one. -->

## How it was tested

<!--
Be explicit about what you could NOT test. "Compiles and looks right" and
"verified on a real arm" are very different claims for a robotics tool, and
nobody will think less of the former - stating it plainly is what lets a
reviewer know what still needs checking.
-->

- [ ] `python -m pytest` passes
- [ ] `python -m ruff check .` passes
- [ ] Verified on real hardware
- [ ] GUI-only change, no hardware path touched

## Safety checklist

<!-- Delete any line that genuinely doesn't apply. -->

- [ ] Commanded positions still go through `write_goals_deg()`'s calibrated-range clamp
- [ ] Any new control source is gated on `control_source` **and** `_playback_index is None`
- [ ] No new blocking I/O on the GUI thread
- [ ] **This writes to servo EEPROM** (ids, baudrates, homing offsets, position limits) — flagged here because those writes are permanent until changed again

## Docs

- [ ] Updated the relevant file under `docs/` if user-visible behaviour changed
- [ ] Added a `CHANGELOG.md` entry under *Unreleased*
