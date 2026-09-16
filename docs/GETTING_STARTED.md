# Getting started with a brand-new SO-101

This is the "I just unboxed the kit and nothing works yet" guide. It assumes
no prior robotics experience and no familiarity with LeRobot.

Follow it in order. Each stage is a hard prerequisite for the next one:

| Stage | Tab | What it produces | Skip it and… |
|---|---|---|---|
| [1. Install](#1-install) | — | a working `python main.py` | nothing runs |
| [2. Wire it up](#2-wire-the-arm) | — | power + USB to the PC | no serial port appears |
| [3. Give each servo an ID](#3-give-each-servo-an-id) | **1 · Setup** | six servos at ids 1–6 | the bus is unusable — six servos all answer to id 1 and their replies collide |
| [4. Calibrate](#4-calibrate) | **2 · Calibration** | a calibration `.json` | the Control tab refuses to connect |
| [5. Move it](#5-move-the-arm) | **3 · Control** | a moving arm | — |

If something goes wrong at any point, check
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) before assuming the hardware is
broken. Almost every first-time failure is one of about eight things.

---

## 1. Install

You need **Python 3.10 or newer**. Check with `python --version`.

```bash
git clone https://github.com/HaikalBaiqunni/so101-control-station.git
cd so101-control-station
python -m venv venv
```

Activate the virtual environment — this differs per OS:

```bash
venv\Scripts\activate
```

```bash
source venv/bin/activate
```

Then:

```bash
pip install -r requirements.txt
```

Verify it starts:

```bash
python main.py
```

You should get a dark window with three tabs: **1 · Setup**, **2 ·
Calibration**, **3 · Control**. Nothing will work yet — that's expected, no
hardware is connected.

> **Linux users:** you will also need permission to open serial ports.
> `sudo usermod -a -G dialout $USER`, then log out and back in.

---

## 2. Wire the arm

1. Chain the servos together with the 3-pin cables. Order along the chain
   does **not** matter electrically — what matters is the id each servo is
   given in stage 3.
2. Connect the servo bus to the **USB serial adapter** (the small board that
   came with the kit).
3. Plug in the **5 V power supply**. This is separate from USB.
   **USB alone does not power the servos** — the adapter will enumerate and
   the port will appear, but every scan will come back empty. This is the
   single most common "my arm is dead" report.
4. Plug the adapter's USB cable into the PC.

**Order matters on first setup:** for stage 3 you want exactly **one** servo
on the bus at a time. Either unplug the chain and add servos one by one, or
configure each servo before assembling the arm. Doing it before assembly is
much easier.

---

## 3. Give each servo an ID

**Why this exists:** every Feetech STS3215 leaves the factory answering to
**id 1**. Six of them wired onto one bus are electrically fine but logically
identical — a read addressed to id 1 gets six simultaneous replies that
collide into garbage. Nothing else in this app can work until each servo has
its own address.

This is the same job as LeRobot's `lerobot-setup-motors`, done from the GUI.

Open the **1 · Setup** tab.

1. **Connect exactly one servo** to the bus (powered).
2. Pick the port. The dropdown shows a description alongside the device name —
   look for `CH340`, `CH343`, `CP210x` or `FTDI`. That's the arm's adapter.
   A port with no description, or one named "Bluetooth", is not it.
3. Click **Connect**.
4. Click **Scan bus (all baudrates)**. It sweeps ids 0–20 across all eight
   baudrates the servo could be set to, so it finds the servo even if someone
   previously reconfigured it.
5. The **Arm status** table on the right shows which of the six joints are
   present. A brand-new servo shows up as a stray id 1.
6. In **Give the connected servo its id**, the dropdown has already
   pre-selected the first joint still missing. Confirm it names the joint this
   physical servo actually is, then click **Assign this id to the connected
   servo** and confirm.

   > **Assign is only enabled when the scan sees exactly one servo.** This is
   > deliberate: with several on the bus they would all take the new id
   > simultaneously and stay indistinguishable.

7. Add the next servo to the chain, **Quick rescan**, and repeat.

The id each joint needs:

| Joint | ID |
|---|---|
| `shoulder_pan` | 1 |
| `shoulder_lift` | 2 |
| `elbow_flex` | 3 |
| `wrist_flex` | 4 |
| `wrist_roll` | 5 |
| `gripper` | 6 |

You are done with this stage when all six rows read **OK**.

If a row says *"found, but at 115200 baud"*, that servo is on the wrong
baudrate — the rest of this app talks at 1 Mbps only. With just that servo on
the bus, pick `1 000 000 baud` and click **Set servo baudrate**.

**Disconnect on this tab before moving on.** Only one tab can hold the serial
port at a time; the app will tell you if you forget.

---

## 4. Calibrate

**Why this exists:** the servos report position as a raw 0–4095 encoder
count with no idea where your arm's joints physically stop. Calibration
records two things per joint — where "middle" is, and how far it can travel —
and writes them both into the servos and into a `.json` file. The Control tab
**refuses to connect without one**, because it will not command a joint whose
safe range it doesn't know.

See [CALIBRATION.md](CALIBRATION.md) for what the resulting numbers actually
mean and how they interoperate with LeRobot.

Open the **2 · Calibration** tab.

1. Pick **Role** — *Follower* for the arm you'll drive, *Leader* for a second
   arm used as a hand-held controller. This only changes the suggested save
   folder.
2. Pick the port and **Connect**.
3. The five step buttons unlock **one at a time, in order**, with a hint line
   telling you what to do next. Step 1 stays available throughout so you can
   always start over.

   1. **Reset motors** — clears any previous homing offsets and limits, turns
      torque off, forces position mode.
   2. **Set middle** — first move *every* joint by hand to roughly the middle
      of its intended travel. A dialog shows the digital twin's own zero pose
      as a visual target (load a twin on the Control tab first if you want the
      picture). Only click OK once the real arm is parked there. Whatever
      position the arm is in when you confirm becomes each joint's 0°.
   3. **Start recording range of motion**.
   4. Now **slowly move every joint through its full range**, both directions,
      watching the Min/Pos/Max table fill in. Do not force anything past its
      mechanical stop. Skip `wrist_roll` — it's treated as a full continuous
      turn automatically. Then **Stop recording**.
   5. **Finish & Save…** — writes the limits to the servos and prompts for
      where to save the `.json`. The default path is LeRobot's own cache
      folder, so `lerobot-teleoperate` and `lerobot-record` will find it too.

**Keep that file.** You will point the Control tab at it every session.

---

## 5. Move the arm

Open the **3 · Control** tab.

1. **Connection panel** (top left): pick the port, **Browse** to the
   calibration `.json` you just saved, **Connect**.
2. Click **Torque ON**. The arm will now hold position — it becomes stiff.
   Click **Torque OFF** whenever you want to move it by hand.
3. Under **Control source**, leave **Manual (sliders)** selected and drag a
   joint slider. The arm should follow.

That's the whole loop. Everything below is optional.

### Optional: digital twin

In the **Digital twin** panel, browse to an MJCF `scene.xml` and click
**Load**. It mirrors whatever the arm is doing, live, whichever control source
is active. Any SO-101 MuJoCo model works — the app doesn't ship one, so point
it at whichever you already have.

### Optional: other control sources

Only one source drives the arm at a time, so inputs can never fight.

- **Keyboard jog** — click the window to focus it, then hold `Q`/`A`, `W`/`S`,
  `E`/`D`, `R`/`F`, `T`/`G`, `Y`/`H`. The on-screen keycaps light up while
  held. Multiple keys at once is fine.
- **Gamepad** — plug in an Xbox-style pad, click **Enable gamepad**, then
  select **Gamepad** as the source. Mapping is in `ui/gamepad_panel.py`.
- **Leader arm** — connect a second, independently calibrated SO-101 and move
  it by hand; the follower mirrors it. The relay maps by *fraction of each
  arm's own calibrated range*, so the two arms don't need to agree on where
  zero is.

### Optional: teach a sequence

1. Get the arm to a pose — **Torque OFF** and hand-guide it is the most
   intuitive way.
2. **Record Waypoint**. Repeat for each pose.
   Use **Record Grip** instead for a waypoint that must actually *hold*
   something — it records the gripper at its calibrated limit rather than at
   the contact position, so playback keeps pushing and there is real closing
   force behind it.
3. **Play Sequence** drives through the waypoints at a capped, smoothed speed.
4. **Save…** keeps the sequence as a `.json`.

This is scripted point-to-point playback, like an industrial teach pendant —
not imitation learning. For dataset recording, use `lerobot-record`; it reads
the same calibration files.

### Optional: telemetry

The bottom panel streams each servo's current, load, velocity, voltage and
temperature at ~10 Hz, as a table or a live graph, and can log to CSV. Useful
for questions like "how much current does the gripper actually pull when it's
holding something?".

---

## Where to go next

- Something not working → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- What the calibration numbers mean → [CALIBRATION.md](CALIBRATION.md)
- How the code is put together → [ARCHITECTURE.md](ARCHITECTURE.md)
