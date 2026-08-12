# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-12

The "first hour shouldn't hurt" release. Closes the gap that made a brand-new
arm unusable without dropping to LeRobot's CLI, and hardens the paths a
beginner is most likely to stumble into.

### Added

- **Setup tab** — first-time motor configuration, the in-GUI equivalent of
  `lerobot-setup-motors`. Previously there was no way to assign servo ids from
  this app at all, which meant a brand-new arm (every servo answering to id 1)
  could not be calibrated or driven without external tooling.
  - Bus scan across all eight Feetech baudrates over ids 0–20, plus an opt-in
    0–253 deep scan.
  - Per-joint arm-status checklist: present / missing / wrong baudrate.
  - Id assignment, enabled only when exactly one servo is on the bus.
  - Baudrate repair for servos left at something other than 1 Mbps.
- `ServoBus` support for `ID`, `Baud_Rate` and `Lock` registers, baudrate
  sweeping, ping sweeps and guarded id/baudrate assignment.
- **Sequential gating of the calibration steps.** All five buttons used to
  enable at once on connect, so clicking 5 before 3 was possible and wrote
  garbage limits. They now unlock in order with a hint line for the next
  action; step 1 stays available as the start-over path.
- Serial port dropdowns now show the USB descriptor (`COM5 - USB-SERIAL
  CH340`) instead of a bare device name, in every tab.
- Test suite (47 tests) covering sign-magnitude encoding, register-table
  invariants, degree conversion, telemetry unit conversion, encoder wraparound
  and waypoint validation. No hardware required.
- Documentation: `LICENSE` (MIT), `docs/GETTING_STARTED.md`,
  `docs/TROUBLESHOOTING.md`, `docs/CALIBRATION.md`, `docs/ARCHITECTURE.md`,
  `CONTRIBUTING.md`, this changelog, GitHub issue/PR templates, and a CI
  workflow running ruff + pytest.

### Fixed

- **Playback crashed if the follower disconnected mid-sequence** — the next
  timer tick dereferenced a `None` worker. The sequence now ends deliberately
  with a status message.
- **Jog accumulators were unclamped.** Holding a keyboard or gamepad jog
  against a joint limit built up an unbounded phantom offset that had to be
  unwound before the joint would respond to a reversal. Now clamped to the
  calibrated range, matching the bus-level clamp.
- **Waypoint files were loaded without validation**, so a malformed `.json`
  loaded silently and failed much later, mid-playback, with the arm already
  moving. Files are now validated at load time with a message naming the
  offending waypoint.
- **Telemetry CSV logged raw register counts while the GUI showed converted
  units**, so a capture silently disagreed with the table it was read from.
  The CSV now logs both, with units in the column names.
- **Reset during a calibration recording pass left recording enabled**, so the
  min/max it had just cleared immediately started re-widening.
- **Finishing a calibration without a recording pass** raised a `KeyError`
  partway through writing limits, leaving a half-configured arm. Refused up
  front now.
- Stale calibrated ranges were kept after disconnect, so the jog clamp and the
  twin's mapping stayed scaled to an arm that was no longer attached — and a
  failed reconnect kept using them indefinitely.
- `closeEvent` did not wait for the robot/leader/calibration threads. With no
  event loop left to deliver their `finished` signals, the process could exit
  with live `QThread`s still touching serial ports.
- Only one tab may hold the serial port at a time; connecting on a second one
  now explains that instead of failing with an OS-level access error.

### Changed

- Tabs are ordered and numbered by the order they must be done in — **1 ·
  Setup**, **2 · Calibration**, **3 · Control** — and the app opens on Setup.
  Landing a first-time user on Control, where nothing works until two earlier
  stages are complete, was the original confusion.
- `ui.telemetry_panel._convert` is now the public `convert_telemetry`, shared
  with the CSV writer so the two can't disagree.
- `ServoBus.read_telemetry()`'s docstring no longer claims the load/current
  encodings are undecodable — that was written before the conversions were
  cross-checked and moved into `convert_telemetry()`.

## [0.1.0]

Initial version: Control and Calibration tabs, four control sources, MuJoCo
digital twin, camera panel, waypoint teach & playback, servo telemetry with
live graph, session logging.

[Unreleased]: https://github.com/HaikalBaiqunni/so101-control-station/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/HaikalBaiqunni/so101-control-station/releases/tag/v0.2.0
