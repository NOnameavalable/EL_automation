# Project Context

## Goal

This app controls an EL probing workflow for die-by-die imaging. It coordinates
stage movement, EL station camera capture, focus checking/autofocus, and Keithley
source meter output during the main probing sequence.

## Main Files

- `stage_gui.py`: top-level operator GUI and workflow coordinator.
- `YeloModuleImageCapture.py`: die layout math and main die-to-die movement
  sequence.
- `el_station.py`: Lucam camera control, EL snapshots, preview window, focus
  overlay, focus score, and autofocus.
- `SSD220.py`: shared SSD220 motor movement helpers, including controlled
  movement with stop/pause support.
- `Keithley2400.py`: Keithley source meter setup, current, voltage, and output
  helpers.

## Current Architecture

- `stage_gui.py` owns the operator workflow and passes callbacks into
  `YeloModuleImageCapture.main()`.
- `YeloModuleImageCapture.py` owns movement sequencing and die layout math.
- EL capture, focus scoring, and autofocus remain in `el_station.py`.
- Low-level motor movement goes through `SSD220.py`, especially
  `move_with_control()` for interruptible moves.
- Keithley output state is prepared by the GUI before the main sequence and
  shut down when the sequence finishes.

## Main Workflow

1. Operator sets Home.
2. `stage_gui.py` records `home_position` and builds `DieLayout` positions.
3. Operator sets Contact Z.
4. Operator loads a non-empty die configuration CSV.
5. Operator may select a configured starting die; the first available die is
   selected automatically.
6. Operator opens EL Station.
7. Operator checks focus; the current focus score becomes the reference score.
8. Operator applies light and probe current levels.
9. Starting the main sequence turns the light output on and prepares probe
   current.
10. `_run_yelo_main()` lifts Z back to Home Z, then calls
   `YeloModuleImageCapture.main()`.
11. For each configured die at or after the selected starting die, the main
   sequence moves XYUV, checks focus score
   against the reference, optionally refocuses, moves Z down to contact,
   captures EL, then moves Z back up.
12. Stop only stops the running work. It does not automatically move home.
13. End/error cleanup disables Keithley outputs and restores GUI controls.

## Die Layout And Calibration

- Die positions are stored as micrometer coordinates before conversion to motor
  pulses.
- Axis calibration constants live in `YeloModuleImageCapture.py`.
- Current calibration:
  - X: `0.05 um/pulse`
  - Y: `10 um/pulse`
  - U: `1.0 um/pulse` (`10000 pulses = 10 mm`)
  - V: `0.4 um/pulse` (`10000 pulses = 4 mm`)
- Current layout values from `stage_gui.py`:
  - `dies_per_row=16`
  - `dies_per_group=4`
  - `die_spacing=9000`
  - `group_gap=12500`
  - `row_spacing=32500`
  - `second_row_die_upside_down_offset=5000`
  - `second_row_u_row_offset=5000`
  - `second_row_center_offset=250`
- For second-row dies:
  - X gets `row_spacing + second_row_die_upside_down_offset`.
  - Y gets the normal row position plus `second_row_center_offset`.
  - U gets `second_row_u_row_offset + second_row_center_offset`.
  - V gets `second_row_die_upside_down_offset`.

## Key Vocabulary

- Home: operator-recorded safe/reference stage location.
- Contact Z: operator-recorded Z position for probe contact.
- EL Station: camera/focus UI and Lucam control window.
- Focus reference score: score captured before the sequence starts.
- Focus threshold: EL Station ratio used to decide whether score drop requires
  autofocus.
- Die-upside-down offset: second-row X/V offset for flipped die orientation.
- U row correction: preserved second-row correction that used to be a Y offset.
- Center offset: smaller second-row Y/U alignment offset.
