# Next Steps

## Immediate Queue

- Confirm real-machine behavior of the latest second-row offsets:
  - X adds `5000 um` die-upside-down offset.
  - V adds `5000 um` die-upside-down offset.
  - U adds `5000 um` row correction plus `250 um` center offset.
  - Y adds `250 um` center offset.
- Confirm the V calibration update on hardware:
  - `V_UM_PER_PULSE = 0.4`
  - `10000 pulses = 4 mm`
- Confirm the U calibration on hardware:
  - `U_UM_PER_PULSE = 1.0`
  - `10000 pulses = 10 mm`

## Possible Implementation Tasks

- Refactor motor access behind the centralized pause/stop stage-control layer
  described in `memory/DECISIONS.md`, removing repeated control callbacks from
  Yelo movement call sites.
- Add focused tests or a lightweight verification script for `DieLayout` output
  without importing hardware modules.
- Consider isolating pure layout/calibration code from hardware imports so
  `DieLayout` can be tested with the default Python environment.
- Review whether `Y_UM_PER_PULSE = 10` is still correct for all layout movement.
- Verify autofocus behavior around edge peaks and threshold checks on real EL
  images.

## Open Questions

- Should the second-row U row correction remain exactly `5000 um`, or should it
  be expressed as a named physical measurement in operator terms?
- Should X and V die-upside-down offsets always share one value, or should they
  become separate values if hardware alignment requires it?
- Should Y and U center offsets always share one value, or should they become
  separate values again if calibration diverges?

## Maintenance Reminders

- Keep `memory/` files current when movement rules, calibration, autofocus, or
  Keithley behavior changes.
- Remove stale offset descriptions immediately after changing layout math.
- Before committing workflow changes, check `memory/PROJECT_RULES.md` for
  behavior that must be preserved.
- Leave unrelated untracked files out of commits unless the user explicitly asks
  to include them.
