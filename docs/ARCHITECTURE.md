# Architecture

For contributors, and for anyone wanting to reuse a piece of this in their own
tooling. For a narrative engineering write-up with diagrams, see
[technical-report.html](technical-report.html) (bilingual EN/JA).

## Design constraint that shaped everything

**No LeRobot dependency.** The register map, sign-magnitude encoding and
calibration algorithm were verified directly against LeRobot's own
`lerobot/motors/motors_bus.py`, `lerobot/motors/feetech/{tables.py,feetech.py}`
and `lerobot/robots/so_follower/so_follower.py` (Apache-2.0), then
**re-implemented standalone** rather than imported.

The reason is the target audience: someone learning physical AI on a bare
machine. `pip install lerobot` pulls PyTorch and a full dataset/training
stack. This app installs in a couple of minutes and needs
`feetech-servo-sdk` + `pyserial` to talk to hardware.

The cost is that upstream register-table changes don't propagate
automatically. That trade is deliberate. Calibration files stay
byte-compatible in both directions, which is the interop that actually
matters.

## Module map

```
core/
  servo_bus.py         Feetech STS3215 register-level driver. Register table,
                       sign-magnitude codec, GroupSyncRead/Write batching,
                       degree<->tick conversion, the safety clamp, the
                       calibration primitives, and first-time id/baud setup.
  workers.py           RobotWorker (60 Hz control loop), CameraWorker,
                       GamepadWorker.
  calibration_worker.py  The reset -> homing -> record -> write sequence,
                       driven step-by-step by the GUI instead of blocking
                       input() prompts.
  setup_worker.py      Bus scanning and servo id/baudrate assignment - the
                       in-GUI equivalent of lerobot-setup-motors.
  digital_twin.py      MuJoCo model/render wrapper. Degree- AND fraction-based
                       joint setters (see CALIBRATION.md for why both).
  twin_worker.py       The twin's render loop on its own QThread, 15 fps.
  camera_enum.py       Human-readable camera names (pygrabber, cv2 fallback).
  session_logger.py    Timestamped markdown debug log under logs/.

ui/
  main_window.py       Wires everything together. Owns control-source
                       arbitration, the teach/playback state machine, the
                       telemetry CSV writer and the calibration guidance
                       dialog.
  setup_panel.py       Tab 1. Also exports describe_ports(), used by every
                       other port dropdown.
  calibration_panel.py Tab 2. Sequential step gating + live min/pos/max table.
  connection_panel.py  Tab 3: port/calibration/connect/torque.
  control_source_panel.py  Manual/Gamepad/Leader/Keyboard selector.
  joint_panel.py       Slider rows.
  teaching_panel.py    Waypoint list + record/reorder/play/save.
  keyboard_jog_panel.py  Key-cap layout that lights up while keys are held.
  gamepad_panel.py     Gamepad status + mapping legend.
  telemetry_panel.py   Table/Graph tabs, unit conversion, CSV toggle.
  twin_panel.py        Twin display.
  camera_panel.py      Camera display + device picker.
  style.py             Dark industrial-HMI QSS theme.

tests/                 Pure-logic tests: encoding, unit conversion, waypoint
                       validation. No hardware required.
```

## Threading model

**Every piece of blocking I/O runs on its own `QThread`.** The GUI thread never
waits on a servo, a camera or a render.

```
RobotWorker (follower)  ──positions_updated (60 Hz)──┐
RobotWorker (leader)    ──positions_updated (60 Hz)──┤
                                                     ├──> MainWindow.current_positions
                                                     │        (cheap dict merge only)
                                                     │
                    QTimer @ 30 Hz ──_refresh_ui()───┘
                                          │
                                          ├──> JointPanel widgets
                                          └──> TwinWorker.set_fractions()

TwinWorker   ──frame_ready (15 fps)──> TwinPanel
CameraWorker ──frame_ready (20 fps)──> CameraPanel
RobotWorker  ──telemetry_updated (10 Hz)──> TelemetryPanel + CSV
```

### Why the rates are what they are

| Loop | Rate | Reason |
|---|---|---|
| Robot poll/write | 60 Hz | Matches `lerobot-teleoperate`. Only achievable because reads/writes are batched into single `GroupSyncRead`/`GroupSyncWrite` transactions; one round trip per joint at 20 Hz was the visible lag against the CLI. |
| UI refresh | 30 Hz | The custom-styled sliders make each `setValue()` a real repaint (~5 ms for all six). Decoupling "data arrives" from "widgets repaint" is what stops the main thread falling behind with both a leader and a follower streaming. |
| Twin render | 15 fps | A single `render()` costs 40–90 ms. On the main thread it would stall the event loop and make everything feel laggy even though the control loop is fine. |
| Telemetry | 10 Hz | Diagnostics. Its two extra bus transactions have no business competing with the control loop. |
| Playback tick | 30 Hz | One interpolated goal per tick, quintic minimum-jerk eased. |

### Worker shutdown

Stopping a worker only flips a flag; the OS thread stays alive briefly after.
Dropping the last Python reference to a still-running `QThread` makes PySide6
destroy it out from under itself — a hard Qt abort. `MainWindow._retire_worker()`
holds a reference in `_shutting_down_workers` until `finished` actually fires.

It never calls `wait()` while the app is running: on a flaky USB connection a
read can sit blocked inside the SDK until it times out, and waiting for that
on the GUI thread is a freeze. **Except at shutdown** — `closeEvent()` does
wait, because there's no event loop left to deliver the `finished` signals
that would otherwise release the references.

## Control-source arbitration

Four sources can command the arm: manual sliders, gamepad, keyboard jog,
leader arm. **Exactly one is live at a time**, selected by radio button, so
inputs can never fight each other.

They funnel into one place, `MainWindow.current_positions`, which is also what
the twin, the telemetry watch and waypoint recording read from. That's why
"record a waypoint" doesn't care how the arm got to the pose.

Playback locks out all four while a sequence runs.

## Safety layers

1. **The Control tab refuses to connect without a calibration file.** No "just
   let me move it" escape hatch.
2. **`write_goals_deg()` clamps** every commanded position to the calibrated
   `range_min`/`range_max`, in raw ticks, before anything reaches a servo.
3. **Jog accumulators clamp too**, so holding a key against a limit doesn't
   build a phantom offset.
4. **Torque-on seeds `Goal_Position` with the current measured pose** first,
   so re-enabling torque holds still instead of lurching toward a stale target
   at the servo firmware's own uncontrolled max speed.
5. **Id assignment requires exactly one servo on the bus**, enforced by the UI.
6. **Calibration steps are gated in order**, and `finish` refuses to run
   without a completed recording pass.

## Adding a control source

1. Add it to `SOURCES` in `ui/control_source_panel.py` and give it a radio
   button.
2. Feed positions through `MainWindow._drive_joint_programmatically()` — it
   handles clamping, state update and the widget echo.
3. Gate your tick on `self.control_source == "<yours>"` **and**
   `self._playback_index is None`.

`_keyboard_jog_tick()` is the smallest complete example.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

They cover encoding/scaling maths, telemetry unit conversion and waypoint
validation — everything that's easy to get subtly wrong and impossible to
notice from the GUI. No hardware needed; `tests/conftest.py` pins Qt to the
offscreen platform.

Anything requiring a real servo is out of scope for CI and has to be verified
by hand on an arm.
