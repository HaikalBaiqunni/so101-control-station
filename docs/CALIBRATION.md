# What calibration actually does

Calibration is the step people most often treat as a magic incantation. It
isn't — it's three numbers per joint, and knowing what they mean makes every
downstream behaviour (why a joint won't reach somewhere, why the twin is
offset, why the leader's gripper doesn't match the follower's) obvious rather
than mysterious.

## The problem being solved

An STS3215 reports its position as a **raw 12-bit encoder count**, 0–4095 over
a full turn. That count is an absolute physical fact about the servo horn, and
it knows nothing about:

- which direction *your* arm considers positive,
- where "straight up" is for the joint it's bolted to,
- how far that joint can travel before it hits a mechanical stop.

Calibration supplies exactly those three, per joint.

## The three numbers

Each joint's entry in the calibration `.json`:

```json
"shoulder_pan": {
    "id": 1,
    "drive_mode": 0,
    "homing_offset": -412,
    "range_min": 917,
    "range_max": 3180
}
```

### `homing_offset`

Written into the servo's own EEPROM (register 31). It shifts what the servo
*reports*, so that the pose you parked it in during **Set middle** reads back
as the half-turn centre, 2047.

Set by `set_half_turn_homing()`:

```
offset = (raw position right now) - 2047
```

This is why step 2 matters and why the app shows you a reference pose first —
whatever the arm is physically doing at the instant you click OK becomes that
joint's zero. Park it badly and every subsequent angle is skewed by however
far off you were.

### `range_min` / `range_max`

The lowest and highest raw counts observed during the recording pass, i.e.
the joint's real mechanical travel *as you demonstrated it*. Written into the
servos' Min/Max_Position_Limit registers **and** kept in the file.

Two consequences worth internalising:

- **They're only as good as your recording pass.** If you didn't push a joint
  all the way to both stops, the app will refuse to command it there — not
  because it can't, but because nobody ever told it that range was safe.
- **`wrist_roll` is special-cased** to the full 0–4095. It's a continuous-turn
  joint by design, so "range of motion" is meaningless for it. This matches
  LeRobot's own `so_follower.calibrate()`.

### `id`

Which servo on the bus this joint is. Set during the Setup stage, not during
calibration — see [GETTING_STARTED.md](GETTING_STARTED.md#3-give-each-servo-an-id).

### `drive_mode`

Always `0` here. Present for format compatibility with LeRobot.

## How degrees are derived

Everything the GUI shows in degrees comes from this, in `ServoBus`:

```python
mid = (range_min + range_max) / 2
degrees = (raw - mid) * 360 / 4095
```

So **0° is the midpoint of the range you recorded**, not the servo's own
2047 centre and not any absolute mechanical reference. That's a deliberate
choice — it makes the sliders symmetric around a pose that's meaningful for
your arm — but it has two knock-on effects that surprise people:

### Why the digital twin uses fractions, not degrees

The MJCF model has its own joint zero-references, defined by whoever authored
the model. They do not line up with a calibration's midpoint (the gripper is
the clearest case: a typical MJCF gripper range is −10°…100°, whose zero is
nowhere near its centre).

So the twin is driven by **fraction of travel** instead:

```
fraction = (degrees - range_lo) / (range_hi - range_lo)   # 0..1
model_angle = model_lo + fraction * (model_hi - model_lo)
```

"Half open in real life" maps to "half open in the model", and the two
zero-references never have to agree.

### Why leader→follower teleop converts through fractions too

Leader and follower are calibrated **independently**. Their degree scales
agree on nothing — squeezing the leader's gripper fully closed does not
produce the same degree value the follower calls fully closed. Relaying raw
degrees 1:1 silently assumes it does.

The same fraction round-trip fixes it: leader-fully-closed always means
follower-fully-closed, regardless of how the two calibrations differ in
magnitude. See `_leader_deg_to_follower_deg()` in `ui/main_window.py`.

## Safety: the clamp

Every commanded position passes through `write_goals_deg()`, which converts to
raw ticks and then clamps to `range_min`/`range_max` **before** anything is
sent to a servo. A GUI bug, a wild slider drag or a runaway jog integrator
cannot command a joint past its recorded limits.

On top of that, the Control tab **refuses to connect without a calibration
file at all**. There is no "just let me move it" mode — an uncalibrated joint
is one whose safe range is unknown, and the app declines to guess.

The jog paths (keyboard/gamepad) additionally clamp *their own accumulator*
to the same range, so holding a key against a limit doesn't build up a phantom
offset that has to be unwound before the joint responds again.

## Interoperability with LeRobot

The file format is byte-for-byte the same as `lerobot-calibrate` produces, and
by default this app saves to the same location LeRobot's CLI reads from:

```
~/.cache/huggingface/lerobot/calibration/robots/so_follower/<id>.json
~/.cache/huggingface/lerobot/calibration/teleoperators/so_leader/<id>.json
```

So files are interchangeable **both ways**:

- calibrate here → `lerobot-teleoperate` / `lerobot-record` will find it,
- calibrate with `lerobot-calibrate` → point this app's Control tab at it.

The calibration algorithm itself (reset → half-turn homing → record range →
`wrist_roll` full-turn → write limits) was verified against LeRobot's
`lerobot/robots/so_follower/so_follower.py` and re-implemented standalone,
rather than imported, to avoid pulling in the full `lerobot` + `torch`
dependency chain. See [ARCHITECTURE.md](ARCHITECTURE.md).

## When to re-calibrate

- After any mechanical work — re-tightening a horn, replacing a servo,
  reprinting a part.
- If a joint starts refusing to reach somewhere it visibly can.
- If the digital twin is consistently offset from the real arm on one joint.
- After changing a servo's id (the id is stored in the file).

Re-calibrating is cheap and non-destructive; it overwrites the servos'
homing/limit registers and writes a fresh file.
