# Decisions

## Keep Camera And Focus Logic In EL Station

Reason: Lucam snapshots, focus scoring, preview streaming, focus overlay, and
autofocus already live in `el_station.py`.

Implications:
- `YeloModuleImageCapture.py` receives callbacks for capture, focus score, and
  refocus.
- Camera implementation details should not be moved into the movement sequencer.

## Use Callbacks For Main Sequence Side Effects

Reason: The main sequence needs EL capture, focus checking, and refocus while
remaining responsible only for movement order.

Implications:
- `stage_gui.py` owns GUI-thread-safe callback wrappers.
- `YeloModuleImageCapture.main()` can remain testable at the sequencing level.

## Require Focus Check Before Start

Reason: The workflow assumes the initial image is already in focus and uses that
score as the reference for later die checks.

Implications:
- Start is blocked until Check Focus records a reference score.
- The focus reference updates after successful refocus.

## Use EL Station Threshold For Focus Drop

Reason: Focus threshold behavior belongs with focus scoring and autofocus
parameters, not as a separate movement-sequence policy.

Implications:
- Main sequence receives the threshold ratio from GUI/EL Station context.
- Future threshold changes should stay consistent with EL Station autofocus
  behavior.

## Preserve Preview Window During Stream Restarts

Reason: EL snapshots and exposure changes should not make the camera preview
window close and reopen.

Implications:
- `stop_streaming()` stops acquisition without destroying the preview window.
- A separate close-window path is responsible for destroying the preview UI.
- Focus overlay state can remain available while streaming is stopped.

## Stop Does Not Move Home

Reason: Stop should be an immediate operator safety/control action, not a hidden
movement command.

Implications:
- Stop requests end active work.
- Moving home must be an explicit command or sequence behavior, not part of the
  Stop button itself.

## Store Die Positions When Home Is Set

Reason: Die coordinates should be tied to the operator-recorded Home position
and remain stable through the run.

Implications:
- `stage_gui.py` creates `DieLayout` during Set Home.
- The resulting die-position dictionary is passed into
  `YeloModuleImageCapture.main()`.

## Use Micrometers As Layout Units

Reason: Layout dimensions are easier to reason about as physical distances, then
converted to pulses through axis calibration constants.

Implications:
- `DieLayout` stores X/Y/U/V positions as micrometer strings.
- `_relative_pulse()` converts by `AXIS_UM_PER_PULSE`.
- Calibration constants must be kept accurate for each axis.

## Current Second-Row Offset Model

Reason: The old second-row Y correction was intentionally moved to U, while new
alignment offsets were added for flipped second-row dies.

Implications:
- X and V use `second_row_die_upside_down_offset=5000`.
- U uses `second_row_u_row_offset=5000`.
- Y and U use `second_row_center_offset=250`.
- Current second-row U coordinate is `5000 + 250 = 5250 um`.

## Current U And V Calibration

Reason: Hardware calibration was corrected from earlier assumptions.

Implications:
- U uses `1.0 um/pulse`, meaning `10000 pulses = 10 mm`.
- V uses `0.4 um/pulse`, meaning `10000 pulses = 4 mm`.
- The V `5000 um` offset converts to `12500 pulses`.
