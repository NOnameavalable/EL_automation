# Project Rules

## Required Reading Rule

- Before working on this project, read `memory/MEMORY_INDEX.md` first and follow
  its required read order.

## Workflow Rules

- Do not start the main sequence unless Home, Contact Z, a non-empty die
  configuration CSV, EL Station, focus reference, both Keithleys, and both
  current levels are ready.
- The image must already be in focus before the main sequence starts.
- Starting the main sequence must turn the light output on; do not turn it off
  before calling the main sequence.
- Stop must stop work only. It must not automatically move the stage home.
- Main sequence cleanup may disable outputs and restore GUI controls.
- On each die, focus checking happens before contact Z and before EL capture.
- Refocus should only run when the current focus score drops below the reference
  by the EL Station threshold.
- After successful refocus, update the reference score to the new result.

## Movement Rules

- Prefer `SSD220.move_with_control()` for movement that must honor Stop/Pause.
- Avoid duplicating home/origin movement helpers. Use shared movement helpers and
  pass target positions.
- Z safety movement should be explicit and preserved when changing motion flow.
- `YeloModuleImageCapture.py` should sequence movement, not own camera or
  Keithley implementation details.
- Die layout positions are micrometer coordinates until converted by axis
  calibration.
- `die_travel_order()` must exclude dies absent from the die configuration
  before movement, focus checking, contact, polarity changes, or capture.
- A selected starting die must truncate the configured travel order without
  wrapping to earlier dies.
- Preserve current second-row layout behavior unless intentionally changing
  alignment:
  - X/V use `second_row_die_upside_down_offset`.
  - U also uses `second_row_u_row_offset`.
  - Y/U use `second_row_center_offset`.

## EL Station Rules

- Camera capture, focus score calculation, preview streaming, focus overlay, and
  autofocus logic belong in `el_station.py`.
- The Find Focus button and main-sequence refocus path should share autofocus
  logic.
- `stop_streaming()` should stop streaming without destroying the preview
  window. A separate close function should destroy the preview window.
- Focus overlay state must be able to remain available when streaming is stopped
  but the preview window remains open.
- Changing snapshot exposure should not unnecessarily reopen the preview window.

## Keithley Rules

- Light and probe current values must be explicitly applied before starting.
- Probe polarity may change per die through GUI-owned callback logic.
- Do not bury Keithley-specific behavior inside `YeloModuleImageCapture.py`.

## UI Rules

- Keep operator controls direct and workflow-oriented.
- The speed status label at the bottom was removed because the monitor window
  already provides status.
- Use buttons for required operator actions such as Set Home, Set Contact, Check
  Focus, and Start.
- Avoid adding explanatory UI text when the workflow can be expressed through
  clear controls and status messages.

## Data And Naming Rules

- Die IDs are strings when used as keys in die-position dictionaries.
- `DieLayout.die_positions()` returns a dictionary mapping die number strings to
  axis coordinate strings.
- Keep offset names descriptive:
  - `second_row_die_upside_down_offset`
  - `second_row_u_row_offset`
  - `second_row_center_offset`
- Avoid vague names such as `demo` for production speed or calibration values.

## Coding Constraints

- Keep changes scoped to the relevant workflow/module.
- Preserve existing operator workflow unless a requested behavior change requires
  a workflow update.
- Do not store secrets or credentials in project memory or source files.
- Update `memory/` after meaningful architecture, workflow, or behavior changes.
