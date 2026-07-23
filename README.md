# SO-101 Control Station

A standalone Python GUI for controlling/teleoperating/calibrating a Feetech
STS3215-based SO-ARM100 / SO-101 arm, with a live MuJoCo digital twin,
optional camera feed, and optional gamepad input.

Built deliberately **independent of the full LeRobot package** (no PyTorch,
no dataset/training stack) so it stays light enough to hand to anyone on a
bare machine and have it running in a couple of minutes.

![screenshot](../SO101_AeroHand_Combined/screenshots/1_so101_only.png)

## Features

- **Two tabs**: **Control** (jog/teleoperate/monitor) and **Calibration**
  (run a full calibration from scratch, on either arm).
- **Three interchangeable control sources**, selected with a radio button -
  only one drives the arm at a time so inputs never fight each other:
  - **Manual** - the joint sliders.
  - **Gamepad** - standard Xbox-style controller.
  - **Leader arm** - connect a second SO-101 (as leader) and it teleoperates
    the connected arm live, same as `lerobot-teleoperate`, from inside this GUI.
- **Digital twin** - a MuJoCo render of the SO-101 (or SO-101 + Aero Hand, or
  any other MJCF you point it at) that mirrors whatever position is
  currently commanded/measured, live, regardless of which control source is active.
- **Camera panel** - any USB webcam via OpenCV, independent of everything else.
- **Full in-GUI calibration** - reproduces `lerobot-calibrate`'s exact
  sequence (reset -> half-turn homing -> record range of motion ->
  wrist_roll left as a full continuous turn -> write limits) with step
  buttons and a live min/pos/max table, for either **Follower** or **Leader**
  role. Saves a calibration `.json` in the same format and, by default, the
  same folder LeRobot's own CLI uses - so files are interchangeable both ways.
- **Hard safety clamp** - every commanded position is clamped to the
  calibrated `range_min`/`range_max` for that joint before it's ever sent to
  a servo. The Control tab refuses to connect without a calibration file - it
  will not move a joint it doesn't know the safe range for.

## Requirements

- Python 3.10+
- A LeRobot-format calibration file per arm (produce one either with
  `lerobot-calibrate`, or with this app's own **Calibration** tab - see below).

## Install

```bash
python -m venv venv
# Windows: venv\Scripts\activate   |   macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

### Control tab - jogging one arm

1. **Connection panel**: pick the serial port, browse to that arm's
   calibration `.json`, click **Connect**.
2. **Torque ON** before trying to move it, **Torque OFF** when handling it by hand.
3. **Control Source panel**: leave on **Manual** to drive it with the sliders below.
4. **Digital Twin panel**: browse to an MJCF `scene.xml` and click **Load** - starts mirroring live.
5. **Camera panel**: pick a device index, **Start**. Fully independent of the rest.
6. **Gamepad panel**: plug in a controller, **Enable gamepad**, then select
   **Gamepad** as the control source to actually let it drive the arm.

### Control tab - teleoperating with a leader arm

1. Connect the **follower** as above (Connection panel, top-left) and set its **Torque ON**.
2. In **Control Source**, pick **Leader arm (teleoperate)** - a second
   port/calibration form appears.
3. Fill in the *leader's* port + calibration file, **Connect Leader**. The
   leader's torque is automatically disabled (it's meant to be moved by hand).
4. Move the leader - the follower (and the digital twin) follow live. Switch
   back to **Manual** any time to hand control back to the sliders.

### Calibration tab - from scratch, either arm

1. Pick **Role** (Follower/Leader - only changes the suggested save path/folder).
2. Pick the port, **Connect**.
3. **1. Reset motors** - clears any prior homing/limits, disables torque, sets position mode.
4. Move the arm by hand to roughly the middle of every joint's travel, then **2. Set middle**.
5. **3. Start recording range of motion**, slowly move every joint (except
   `wrist_roll`, which is a full continuous turn by design) through its
   complete range, watching the live Min/Pos/Max table.
6. **4. Stop recording** once you've covered the full range of every joint.
7. **5. Finish & Save...** - writes the limits to the servos and prompts you
   for where to save the `.json` (defaults to LeRobot's own cache path, so
   `lerobot-teleoperate`/`lerobot-record` can find it too).

## Architecture

```
core/
  servo_bus.py          - Feetech STS3215 register-level driver (feetech-servo-sdk + pyserial only)
  calibration_worker.py  - runs the calibrate sequence step-by-step, in the background
  digital_twin.py         - MuJoCo offscreen renderer wrapper
  workers.py                - QThread workers: robot polling loop, camera capture, gamepad polling
ui/
  main_window.py             - wires everything together, arbitrates control source
  control_source_panel.py     - Manual / Gamepad / Leader selector + leader connection form
  calibration_panel.py          - Calibration tab: connect, 5 step buttons, live table
  joint_panel.py                  - slider rows
  connection_panel.py               - port/calibration/connect/torque controls (Control tab)
  camera_panel.py                    - camera view
  twin_panel.py                       - digital twin view
  gamepad_panel.py                     - gamepad status/legend
  style.py                              - dark industrial theme (QSS)
```

All hardware I/O (serial, camera, joystick) runs on background `QThread`s and
only ever talks to the GUI thread through Qt signals - the window never
blocks waiting on a servo or a camera frame.

The register map, sign-magnitude encoding, and calibration algorithm (reset /
half-turn homing / range-of-motion recording / wrist_roll special-case) were
verified directly against LeRobot's own `lerobot/motors/{motors_bus.py,
feetech/{tables.py,feetech.py}}` and `lerobot/robots/so_follower/so_follower.py`
(Apache-2.0) and re-implemented standalone here rather than imported,
specifically to avoid pulling in the full `lerobot` + `torch` dependency chain.

## Known limitations / good next steps

- **Gamepad mapping is fixed** (edit `DEFAULT_AXIS_MAP` /
  `DEFAULT_BUTTON_MAP` in `ui/gamepad_panel.py` to remap) - an on-screen
  mapping editor would be a reasonable v2.
- **One camera at a time** in the UI, though nothing stops running a second
  `CameraPanel` instance if you want a wrist + overhead view side by side.
- **No dataset recording yet** - this app is for jogging/teleoperating/
  calibrating/visualizing, not for capturing training episodes; use
  `lerobot-record` for that (it'll happily reuse calibration files saved here).

## License

MIT (adjust to taste before publishing) - this project intentionally avoids
GPL/heavy dependencies to keep it easy to fold into other people's robot
tooling.
