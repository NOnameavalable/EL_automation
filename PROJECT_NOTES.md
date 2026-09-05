# EL Automation Project Notes

## 2026-05-14 Script Cleanup

### Goal

Clean up `SSD220.py` and `YeloModuleImageCapture.py` around a coordinate-based die-prober workflow, where stage positions are represented as X/Y points and movement commands are reliable after each stepper startup.

### Description

The project controls a die-prober stage. The stage should move in X/Y so each die is positioned under the probe, while the prober can repeat a simpler up/down action at each die location.

The complete hardware setup has five controlled axes across two machines:

- Machine A is the left stage.
- Machine B is the right stage/prober side.
- X controls Machine A horizontal movement.
- Y controls Machine A vertical movement.
- Z controls Machine B upward movement.
- U controls Machine B vertical movement.
- W controls Machine B horizontal movement.

The electrical setup uses two Keithley 2400 SourceMeters, both configured as
current sources:

- The wafer-probe Keithley controls current through the probed wafer/device and
  should use a voltage compliance of `2.5 V`.
- The light/EL Keithley controls the illumination or EL current used during
  image capture and should use a voltage compliance of `15 V`.
- The user should be able to set the current level for each Keithley from the
  GUI/configuration before output is enabled.
- When the main stage GUI opens, connect to both Keithleys and configure their
  fixed operating settings: current-source mode, the appropriate source and
  measurement ranges, front/rear terminals, output-off mode, and voltage
  compliance (`2.5 V` for the wafer probe and `15 V` for EL illumination).
- Startup configuration must leave both outputs off and use a `0 A` current
  level until the user enters a valid current level. Entering a level must not
  enable either output; the workflow enables each output only when required.

For each die, the Keithley output sequence is:

1. Move X/Y to the die and move Z down to the recorded contact position.
2. Turn on the wafer-probe Keithley after contact is established.
3. Take the visible-light image while the probe Keithley remains on.
4. Turn off the light Keithley.
5. Take the EL image while the probe Keithley remains on.
6. Turn off the probe Keithley before retracting Z or moving to another die.

The probe output must also be turned off during stop, cancellation, and error
cleanup paths.

The workflow does not need to record Keithley voltage/current measurements for
each die. Keithley queries may still be used transiently for output-state,
compliance, or error checks, but measurement readings are not saved with the
die images or results.

The intended workflow is:

1. Start the stepper/controller.
2. Use the GUI jog controls to move the stage/prober to the desired reference location.
3. Click `Set Home` to record the current X/Y location as the workflow home.
4. Use the GUI jog controls to move Z until the probe contacts the die.
5. Click `Set Contact` to record the current Z location as the contact height.
6. Run the die sequence from the GUI.
7. Return to the recorded home after the sequence completes.

Important note: each time the stepper/controller starts, it sets its current physical location to controller coordinate `[0, 0]`. Absolute coordinates are therefore relative to the startup position, not necessarily to a fixed mechanical home unless a separate homing/calibration step is added.

### Files Changed

- `SSD220.py`
- `PROJECT_NOTES.md`

### Suggested Workflow

1. Pick a reliable physical origin on the stage.
2. Use the GUI jog controls to align the probe/stage with the first die location.
3. Click `Set Home` and treat that recorded X/Y location as the reference for later die movements.
4. Use the GUI jog controls to move Z to the die contact height.
5. Click `Set Contact` so the probing sequence can return Z to that contact height when needed.
6. While the program is running, use the recorded home/contact positions as the workflow references.

In this workflow, the die map can be stored relative to the first die:

```text
die_1 = [0, 0]
die_2 = [x_offset, y_offset]
die_3 = [x_offset, y_offset]
```

The second die row uses coordinated offsets on four axes rather than a single
Y offset. Each generated second-row die position includes:

- X: `row_spacing + second_row_die_upside_down_offset`.
- Y: the normal within-row position plus `second_row_center_offset`.
- U: `second_row_u_row_offset + second_row_center_offset`.
- V: `second_row_die_upside_down_offset`.

With the current GUI configuration, those additional second-row coordinates
are X `+37500 um`, Y `+250 um`, U `+5250 um`, and V `+5000 um`. Normal die
spacing and group gaps continue to determine the position along each row.

The wafer-probe current direction must also reverse for the second row. Keep
the physical HI/LO wiring fixed and represent the reversal with the sign of the
probe Keithley current level: use the configured current magnitude in the
first row and its negative value in the second row. Turn the probe output off
before changing polarity, and never change polarity while the probe output is
enabled.

### TODO

- [ ] Add a `Contact` destination to the move combobox after Contact has been set.
- [ ] Modify the `move_to_position()` axis-ordering logic.
- [ ] Check the polarity of the probes.
- [ ] Check the exported file name.
- [ ] Skip a die during the sequence when the probe is not in contact with that die.

### Interruptible Movement Idea

The current Yelo workflow checks pause/stop between major movement calls. A better design is to make movement itself interruptible:

```text
send move command with wait=False
while the axis is moving:
    if Stop is requested, send AXI<axis>:STOP 0 and move home/exit
    if Pause is requested, stop or wait at a safe point until Resume
    poll Motion? briefly
```

This would move the control logic into one helper, such as `move_with_control(...)`, instead of repeating stop/pause checks around every movement in `main()`. It should improve Stop responsiveness during long motor moves. Camera snapshot calls may still remain blocking unless the Lucam SDK provides a cancel/abort operation.

### Current SSD220 Direction

The application uses two coordinate conventions. Keep them distinct when
calculating movement or comparing recorded positions.

The workflow also uses two measurement units:

- `DieLayout.die_positions()` contains logical layout coordinates in
  micrometers relative to die 1. These values are neither pulse counts nor
  controller positions. Axis calibration is applied later when the difference
  between two die positions is converted into logical relative pulses.
- `move()` and `move_with_control()` accept logical relative pulse counts.
- `get_pos()` and `move_to_position()` use absolute controller counter values,
  which the current application treats as pulse coordinates.

The D220/SSD220 controller can be configured to report positions in other
units. The code currently assumes pulse units but does not set or verify the
controller's `UNIT` configuration, so that hardware setting must remain in
pulse mode or be explicitly validated during controller initialization.

#### Logical relative movement

`move()` and `move_with_control()` accept signed logical pulse distances. The
application defines positive movement as right for X/V/W and up for Y/Z/U. In
particular, positive logical Z retracts the probe upward, while negative logical
Z lowers it toward contact.

```python
move(inst, {"X": "1000", "Z": "-30000"})
```

`POSITIVE_DIRECTION_BY_AXIS` maps logical positive movement to a controller
direction. All currently configured axes map logical positive to `CCW`, so
`convert_axis_pulse()` reverses the sign for every axis. The low-level command
then sends a positive controller pulse as `GO CW` and a negative controller
pulse as `GO CCW`.

#### Controller absolute positions

`get_pos()` returns the controller's unconverted `POS?` counter values.
Recorded `home_position` and `contact_z` values therefore use controller
coordinates, and `move_to_position()` accepts targets in that same convention.
It converts the difference between the target and current controller positions
into logical relative movement before calling the movement API.

High-level functions that expose or retain controller absolute positions are:

- `get_pos()` returns controller positions directly.
- `move_to_position()` accepts controller-coordinate targets.
- `YeloModuleImageCapture.main()` receives `home_position` and `contact_z` in
  controller coordinates. Its `die_positions` argument instead contains layout
  coordinates in micrometers.
- `LucamStreamApp.find_focus_adaptive()` records focus positions from
  `get_pos()`, calculates target differences in controller coordinates, and
  converts each difference into a logical pulse before moving the focus axis.

For the current Z workflow, a larger controller Z position represents a lower
probe position, while a smaller controller Z position represents a higher,
more retracted position. The standalone fallback reflects this convention by
setting contact Z to `home Z + 30000`. This is the opposite sign from logical
relative Z movement: logical `+Z` moves up and logical `-Z` moves down.

### Manual Move Destination

After `Set Home`, the stage GUI enables a destination combobox and Move button.
The destination list contains `Home` and the dies present in the loaded CSV.
Selecting `Home` moves to the recorded controller Home position. Selecting a
die first returns Z to Home Z, then `_relative_pulse()` reads the current
X/Y/U/V controller positions and calculates the direct logical pulse movement
to that die's logical micrometer position. A manual die move remains retracted
at Home Z; it does not establish probe contact.

### SSD220 Speed Table Convention

The D220/SSD220 controller has speed table entries numbered `0` through `9`. Each table entry contains:

- `LspeedN` / `LN`: low or start speed for table `N`.
- `FspeedN` / `FN`: fast or drive speed for table `N`.
- `RateN` / `RN`: acceleration/deceleration rate for table `N`.

The controller also supports `SELSP N` to select which speed table entry an axis should use.

To avoid confusion between axis numbers and speed table numbers, this project currently standardizes on speed table `0` for every configured axis:

- `SSD220.SPEED_TABLE_INDEX = "0"`.
- `stage_gui.py` calls `set_all_axes_speed_table()` once after opening the stage controller, which sends `AXI<axis>:SELSP 0` for every configured axis.
- `SSD220.move()` writes movement speed using `F0` and `L0` for every axis, regardless of whether the axis is X, Y, Z, U, V, or W.

Example relative move command under this convention:

```text
AXIV:F0 100000:L0 5000:PULS 10000:GO CW
```

Here `V` is the axis, while `F0` and `L0` refer to speed table entry `0`, not axis index `0`.

### Command Notes

Absolute move command format:

```text
AXI<Axis>:F<SpeedTableIndex> <FastSpeed>:L<SpeedTableIndex> <LowSpeed>:GOABS <Position>
```

Examples:

```text
AXIX:F0 10000:L0 5000:GOABS 0
AXIY:F0 10000:L0 5000:GOABS 10000
```

Relative move command format from early testing:

```text
AXI<Axis>:F<SpeedTableIndex> <FastSpeed>:L<SpeedTableIndex> <LowSpeed>:PULS <Distance>:GO <Direction>
```

Examples:

```text
AXIX:F0 10000:L0 5000:PULS 10000:GO CW
AXIY:F0 10000:L0 5000:PULS 10000:GO CCW
```

Configured axes used by `SSD220.py`:

```text
X, Y, Z, U, V, W
```

Important: the number after `F` and `L` is the speed table index, not the axis index. The project currently uses speed table `0` for every axis.

### Test Observations

- Use `write()` for movement commands because they do not return a response.
- Use `query()` for commands that ask for a response, such as `*IDN?`, `AXIX:POS?`, or `AXIY:Motion?`.
- A test X-axis move confirmed that the controller accepted commands because the position changed:

```text
Before: -692286
Motion: 1
After: -692319
```

- `Motion: 1` means the axis is still moving.
- The final position should be checked after polling `Motion?` until it returns `0`.
- Speed values should remain positive. Direction should be changed with `GO CW` or `GO CCW`, not by using negative speed.
- Port W does not appear to respond to speed changes through the current `F`/`L` command settings.
- Because startup position becomes `[0, 0]`, the stage must be placed at a reliable physical origin before starting the stepper/controller. If startup happens too close to a travel limit, later program-controlled movement may not be able to return safely or reliably to `[0, 0]`.

### Open Questions

- Confirm whether Y-axis `CW` and `CCW` match the expected physical directions.
- Confirm whether travel limits, alarm state, or motor enable state affect Y-axis movement.
- Confirm the correct pulse-to-distance conversion for each axis before using large moves.
- Decide whether the project needs a fixed mechanical home/calibration routine, or whether startup-origin `[0, 0]` is sufficient for the die layout workflow.
- Decide how `YeloModuleImageCapture.py` should consume the new point-based `SSD220.py` API.
