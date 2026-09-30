# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **GUI redesign (stage-first).** The four tabs are replaced by a top bar, a
  full-bleed stage and a Setup hub. No control behaviour changed.
  - Top bar: robot picker, Follower / Leader / Torque / source chips, buttons for
    Waypoints, Telemetry, Tune (B601-DM only) and Setup, and an always-visible
    red **Stop** (follower torque off + control back to Manual, so a live
    leader relay cannot make the arm jump when torque is re-enabled).
  - Stage: the digital twin fills the window with floating **View**, **Jog** and
    connection / control-source cards; the camera is a card toggled from View.
  - Twin renders at the view's own resolution (was a fixed 480x360 upscaled with
    black bars).
  - Setup hub: Motors and ids, Calibration (SO-101 wizard, or B601-DM cards that
    launch the gripper / leader sweeps), Inputs, Data and logs.
  - Tune drawer: motion mode and MIT kp/kd moved out of the id workflow.
  - Softer palette, rounded cards and pill buttons; minimum window 1100x700.
  - The top bar floats over the page as rounded pills; toggle switches in the View card;
    outlined pill buttons with solid accent only on the primary action; cards can be
    dragged (position saved, *Reset layout* in View); the camera card is resizable.
  - Cards **minimise to a tab** (the - button) and expand when the tab is clicked, and they
    **auto-adjust**: they never overlap each other or sit under the top bar (a card is slid to
    the nearest free spot), and View / Camera fold into their tab by themselves when a small
    window or an open drawer leaves no room.
  - **Command palette** (Ctrl+K or the search button): navigation, view toggles, control-source
    choice and the calibration dialogs - nothing that moves an arm. **Ctrl+Shift+Space** = Stop.
  - Every joint row has a range bar (position fill, cyan tick for the commanded target) and Joint
    mode shows the live TCP pose.
  - **Engage teleop**: choosing *Leader arm* no longer moves the follower. Tracking starts
    only after an explicit Engage, allowed when both arms are live, follower torque is on
    and every arm joint is within a tolerance (default 10 deg, saved). The leader pose is
    shown as the ghost while standing by; teleop drops on Stop, torque off, source change
    and either arm disconnecting.
  - **Tune drawer** (B601-DM): named MIT gain presets, a commanded-vs-measured tracking chart
    with peak error / settle / overshoot, and +/-5 deg nudge (torque on, Manual only).
  - **Speed slider** now also caps the Damiao POS_VEL velocity (30 % = the old 0.3 rad/s,
    ceiling 0.8 rad/s), so it slows teleop and waypoint playback too.

### Added

- **SO-101 digital twin is bundled** (`models/so101/`, TheRobotStudio's MJCF, Apache-2.0,
  with its licence and a NOTICE): the SO-101 twin now loads on first launch. A model you
  picked with Browse still wins.

- **JAKA-style Jog panel** replaces the per-joint sliders on the Control tab.
  - **Joint / World / Tool** modes with press-and-hold **−/+** buttons, a speed
    setting (default 30 %), and *Continuous* or fixed-**Step** moves.
  - **Cartesian jogging** — X/Y/Z and Rx/Ry/Rz in the **World** frame (fixed to
    the base) or the **Tool** frame (turning with the gripper) — via the new
    `core/kinematics.py`: MuJoCo forward kinematics and Jacobian on the twin's
    own MJCF, resolved-rate damped least squares with damping only near
    singularities, joint-limit sliding and a joint-speed cap. Live TCP pose
    readout in the world frame.
  - **Per-axis reachability**: a 5-joint SO-101 cannot make every direction, so
    partly-reachable axes are drawn dashed with a tooltip instead of pretending.
  - Typed entry in the joint rows now commits on Enter, not on every keystroke
    (typing `120` used to command 1, then 12, then 120).
- **Ghost arm** in the Digital Twin: a translucent copy at the target — the
  waypoint being played, the target a jog is driving toward while the real arm
  catches up, or the selected waypoint. Toggle: *Ghost*.
- **World / Tool axis triads** drawn in the twin, the active jog frame thick and
  the other faint. Toggle: *Axes*.
- **Telemetry has its own tab** (**4 · Telemetry**), so the Control tab can give
  the twin and camera the whole window.
- `core/robot_profiles.py`: `arm_joints`, `tcp_site`, `tcp_body`, `tcp_offset`
  per profile (which joints place the tool, and where the tool centre is).
- 80+ new tests: kinematics (FK, Jacobian vs finite differences, frames, limits,
  5-DoF reachability, the bundled 6-DoF reBot) and the jog panel/engine driven by
  a fake clock.

- **Multi-robot support: reBot B601-DM (Damiao) — real hardware control.** A
  Robot selector above the tabs switches the app between SO-101 (Feetech)
  and the reBot B601-DM (Damiao CAN motors).
  - Digital Twin auto-loads the B601-DM's own MuJoCo model and follows the
    Jog panel (Joint and Cartesian both), including its gripper's
    two-finger mimic joint.
  - **1 · Setup** has a dedicated CAN id assignment panel for Damiao
    motors — connect one at a time, probe, assign a unique id + master id,
    save to flash, then **Verify All** with the whole arm wired at once.
  - **3 · Control** drives the real Damiao motors: Connect, torque-on
    seeded with the motor's own current position, Joint/Cartesian jogging
    via `core/damiao_bus.py` + `core/dm_robot_worker.py` — same
    `request_goal`/`request_torque` shape as the Feetech worker, so the
    existing jog/kinematics code needed no changes to drive it.
  - **Two motion-control modes**: POS_VEL (safe default, the motor's own
    onboard loop) and MIT (per-command `kp`/`kd`, more responsive and
    compliant, needs live tuning) — selectable on the Setup tab, and MIT
    gains now retune a **connected** arm immediately, no reconnect needed.
  - **Leader teleoperation via Seeed's Star Arm 102** — a UART FashionStar
    smart-servo device (`core/fashionstar_bus.py`,
    `core/fashionstar_leader_worker.py`), a completely different
    bus/protocol from the Damiao follower. Relayed by fraction of each
    arm's own calibrated range, same reconciliation as the SO-101
    leader/follower pairing, plus a gripper-direction invert checkbox.
  - **Two sweep-based calibration dialogs** (`core/sweep_recorder.py`):
    `Calibrate leader…` measures the Star Arm 102's real per-joint range
    (its vendor defaults turned out to be wrong on real hardware, by up to
    ~1.75× for `wrist_roll`), and `Calibrate gripper range…` measures the
    follower's real jaw travel by hand with torque off — added directly
    after an assumed default let a held jog stall the gripper motor past
    its real mechanical stop and blow a fuse.
  - `core/robot_profiles.py` (new) is the single source of truth for a
    profile's joint order, bundled twin path and hardware availability;
    `DigitalTwin`/`TwinWorker` take joint names as a parameter instead of a
    hardcoded SO-101-only list.

### Changed

- The Control Source option **Manual (sliders)** is now **Manual (jog panel)**.
- The left control column is wider (minimum 426 px, was 360) and the Teaching
  and Keyboard-jog panels wrap to fit it. Together with the port dropdowns
  below, this removes the horizontal scrollbar that hid the right-hand buttons
  of every panel in the column.

### Fixed

- **"CONNECTED" stayed red.** The connection status labels (follower, leader, Setup,
  Calibration, CAN setup) pick their colour from their object name, and Qt only
  re-reads that after a repolish, so a successful connect kept showing the red
  "disconnected" colour. They now repolish; a pixel test covers it.
- **View card came back as an empty bar** after minimise -> expand (it was measured while
  its contents were still hidden).
- **Port dropdowns forced the whole control column wider than its viewport.**
  Since the descriptive port labels (`COM5 - USB-Enhanced-SERIAL CH343 (COM5)`)
  a combo box sized itself to its longest entry, pushing the Refresh/Browse
  buttons out of view. Port combos now have a small minimum width; the full text
  stays in the popup and tooltip.
- A `TwinWorker` startup race where `stop()` arriving while the twin's
  MuJoCo scene was still compiling could be silently overwritten the moment
  the render loop actually started, leaving the old worker running forever
  instead of retiring — visible as a stale frame from the previous robot
  profile never clearing.
- A Qt queued-connection race where a retiring `TwinWorker`'s last in-flight
  frame could still land on the twin panel after the GUI had already moved
  on to a new profile; frames are now matched against the currently-tracked
  worker before being displayed.
- The B601-DM gripper's follower finger never visually moved — MuJoCo only
  enforces an `<equality>` mimic constraint through simulated stepping
  (`mj_step`), which the twin's kinematic-preview `render()` never runs.
  `DigitalTwin` now reads the model's own equality constraints and syncs
  the follower joint directly, generically (not hardcoded per robot).
  - Also fixed: the collision-derived geoms MuJoCo's URDF importer keeps
    alongside the material-split visual meshes were rendering on top of
    them (both left on the same default group), washing out the B601-DM's
    real accent colors. Re-grouped and hidden from the twin's render.
- A joint whose real-world range is in metres rather than degrees (the
  B601-DM gripper) collapsed the Joint Control slider to zero usable ticks
  (`int(0.05 * SLIDER_SCALE) == 0`), making it undraggable.
- The same `stop()`-during-`connect()` race already fixed once for
  `TwinWorker` was still present in `RobotWorker`, `CalibrationWorker`,
  `CameraWorker` and `GamepadWorker` — fixed identically across all four.
- **B601-DM follower gripper (`finger_left`) read as permanently frozen at
  0** once real hardware testing began: `RobotProfile.preview_ranges`' gripper
  convention (0..100, "percent open") shares no numeric overlap with the
  Damiao library's raw motor-degree readings, so every value clamped
  silently out of range. `core/damiao_bus.py`/`core/fashionstar_bus.py` now
  fraction-remap from the native reading onto whatever range was declared,
  the same contract `deg_limits()` already implied.
- **The Setup tab had no scrollbar.** `DmSetupPanel`'s own content (CAN-id
  rows plus, since MIT mode landed, a 7-row kp/kd grid) could exceed the
  window's visible height with no way to reach the rest.
- **The digital twin's gripper direction could render backwards** even when
  real hardware control was correct — a calibration sweep only ever records
  numeric min/max, with no way to know which physical extreme is open vs.
  closed, so the fraction fed to the twin's MJCF had a 50/50 chance of
  disagreeing with that model's own convention. `RobotProfile.
  twin_gripper_fraction_inverted` flips only what the twin renders, never
  anything sent to real hardware.
- The Setup tab's gripper-direction invert checkbox checked the literal
  string `"gripper"`, so it silently did nothing for the B601-DM's actual
  gripper joint (`finger_left`) — generalized to whichever joint is last in
  the active profile's `joint_order`.
- The leader-teleop preview (shown when no follower is connected yet) used
  to display raw, unconverted leader degrees, skipping the gripper-invert
  override and relay trim entirely — it now runs through the same
  leader→follower conversion the live relay uses, so preview always matches
  what teleoperating would actually send.

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
