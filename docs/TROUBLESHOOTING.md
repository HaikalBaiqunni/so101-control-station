# Troubleshooting

Symptoms, in rough order of how often they hit a first-time user. Every entry
here corresponds to something that actually went wrong during development, not
a hypothetical.

Before anything else: the app writes a timestamped markdown log to `logs/` on
every run, and the status bar shows its path. Every connect, disconnect, mode
change and error is in there, interleaved with a 2 Hz position feed. If you
open an issue, attach it.

---

## Nothing is found / connection problems

### The port dropdown is empty, or the arm isn't in it

- The USB serial adapter isn't plugged in, or its driver isn't installed.
  Windows usually needs a **CH340/CH343** driver for the adapter shipped with
  most SO-101 kits; it does not always arrive via Windows Update.
- Confirm the OS sees it: Device Manager → *Ports (COM & LPT)* on Windows,
  `ls /dev/ttyUSB* /dev/ttyACM*` on Linux, `ls /dev/tty.usb*` on macOS.
- Click **Refresh** in the app after plugging it in.

### The port is there, but scanning finds no servos

**Check the 5 V power supply first.** This is by far the most common cause.
USB powers the *adapter*, not the *servos* — the port enumerates perfectly and
every scan comes back empty. There is no way for the software to tell this
apart from "no servos attached".

Then:

- Check the 3-pin chain is fully seated at every joint, including the link
  back to the adapter.
- A servo previously configured by another tool may be on a different
  baudrate. **Scan bus (all baudrates)** on the Setup tab sweeps all eight.
- A servo may be at an id above 20 (outside the default sweep). Use **Deep
  scan (ids 0–253)** — slower, but exhaustive at the current baudrate.

### `Could not open port COM5` / `Permission denied: /dev/ttyUSB0`

Something else already has the port open.

- **Inside this app:** only one tab can hold the port at a time. Disconnect on
  Setup/Calibration/Control before connecting on another. The app blocks this
  with a message rather than failing cryptically.
- **Outside this app:** close any other serial terminal, the Feetech vendor
  tool, an Arduino IDE serial monitor, or a `lerobot-*` CLI still running.
- **Linux only:** you're probably not in the `dialout` group.
  `sudo usermod -a -G dialout $USER`, then log out and back in.

### `There is no status packet!`

A servo didn't reply in time. Usually one of:

- Two servos share the same id, so their replies collide. Scan on the Setup
  tab — if the count is lower than the number of servos physically attached,
  this is it.
- Loose 3-pin connector.
- Marginal power — a servo browning out mid-move stops answering.

If it happens sporadically during a *calibration* write specifically, it's
usually EEPROM write timing; the driver already retries three times with a
settle delay, so persistent failures point at wiring rather than timing.

---

## Setup tab

### "Assign this id" is greyed out

By design. It only enables when the scan sees **exactly one** servo. With
several on the bus, every one of them would take the new id at the same
instant and stay indistinguishable — which is a worse state than you started
in. Rescan with just the servo you're configuring attached.

### After assigning an id, the servo disappeared

The servo adopts its new id the moment the write lands, so its reply to that
write comes back from an address the SDK is no longer listening for. The app
handles this by pinging the *new* id to confirm. If confirmation failed:

1. Power-cycle the arm (unplug the 5 V supply, wait a second, plug back in).
2. **Scan bus (all baudrates)** again.

The write has usually succeeded — the servo is just sitting at its new id.

### A joint says "found, but at 115200 baud"

The rest of the app talks at 1 Mbps exclusively. With **only that servo** on
the bus, pick `1 000 000 baud` and click **Set servo baudrate**.

---

## Calibration tab

### The step buttons are greyed out

They unlock one at a time, in order, because the sequence is stateful on the
servos themselves. The hint line under the buttons says what's next. Step 1
(**Reset motors**) stays available throughout — that's the way to start over.

### `Cannot finish: no range recorded for …`

Step 5 was reached without a recording pass covering every joint. Run steps
3–4 and make sure each joint moved (watch the Min/Pos/Max table actually
change) before finishing.

### The arm won't reach a position it clearly can reach

The recorded range is too narrow — during step 4, that joint wasn't moved all
the way to both of its mechanical stops. Every commanded position is clamped
to the recorded range, so anything outside it is unreachable by design.
Re-run the calibration and be thorough on that joint.

### The gripper closes but immediately lets go

That's position control doing exactly what it was told. A recorded contact
position is an equilibrium with near-zero position error, so there's no spare
closing force behind it. Use **Record Grip** instead of **Record Waypoint**
for that pose — it snaps the recorded gripper value to its calibrated limit,
so playback keeps commanding a target the object physically prevents, and that
persistent position error *is* the grip force.

---

## Control tab

### "A LeRobot calibration .json is required"

Not a bug — the app refuses to move a joint whose safe range it doesn't know.
Produce one on the **2 · Calibration** tab (or with `lerobot-calibrate`; both
write the same format).

### The arm lurched violently the moment I clicked Torque ON

This should no longer happen: enabling torque seeds `Goal_Position` with the
arm's *current* measured pose first, so it holds still instead of snapping to
whatever stale target was last written. If you see it anyway, please open an
issue with the session log — that's a regression.

### Keyboard jog does nothing

- Select **Keyboard jog** as the control source. Enabling a source and
  *selecting* it are separate steps, deliberately — only one source drives the
  arm at a time.
- Click the main window background first. If a slider or text field has focus,
  it eats the keystrokes.
- Keys held when the window loses focus (alt-tab) are released automatically,
  so a joint can't run away.

### Gamepad isn't detected

Click **Enable gamepad** *and* select **Gamepad** as the control source. If
the status still reads "no gamepad detected", pygame found no joystick at
index 0 — check it's paired/plugged before launching the app.

### Leader teleop works, but the gripper range doesn't match

It should map correctly: leader and follower are calibrated independently, so
the relay converts through *fraction of each arm's own range* rather than raw
degrees. If it's still off, one of the two calibrations doesn't cover that
joint's real travel — re-run calibration on the arm that feels wrong.

---

## Digital twin / camera

### MuJoCo fails to load with `[WinError 6] The handle is invalid`

Already handled — `main.py` sets `MUJOCO_GL=wgl` before importing MuJoCo,
which avoids the console-handle probing that trips this. If you're importing
`core.digital_twin` from your own script, set that env var **before** the
import.

### The twin loads but doesn't move

The twin mirrors *fractions of each joint's calibrated range*, so it needs a
calibration loaded — connect on the Control tab first. Joint names in your
MJCF must match: `shoulder_pan`, `shoulder_lift`, `elbow_flex`, `wrist_flex`,
`wrist_roll`, `gripper`. Unrecognised names are silently ignored.

### The whole app crashes when I switch cameras

Known OpenCV/Windows behaviour, not something this app can paper over:
releasing one physical camera and opening a *different* one in the same
process crashes reliably. The device dropdown is locked while a camera runs
for exactly this reason. **Stop → pick the other device → Start.**

Related: the camera worker deliberately never calls
`cap.set(CAP_PROP_FRAME_WIDTH/HEIGHT)`. Forcing a resolution was confirmed by
A/B testing to be what crashed reopening. Don't add it back.

### The camera takes 15 seconds to open

On Windows, OpenCV's default Media Foundation backend is slow with some
webcams. The app already forces DirectShow (`CAP_DSHOW`), which opens the same
device in well under a second. If you see the delay anyway, you're on a
non-Windows platform where `CAP_ANY` is used.

---

## Still stuck?

Open an issue at
<https://github.com/HaikalBaiqunni/so101-control-station/issues> with:

- what you were doing and which tab you were on,
- the exact error text (status bar or terminal),
- your OS and `python --version`,
- the `logs/session_*.md` file from that run.
