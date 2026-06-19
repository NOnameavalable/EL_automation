# -*- coding: utf-8 -*-
"""
Created on Thu Nov 28 15:54:42 2024

@author: BenWatts, Steven Li
"""

from dataclasses import dataclass
from collections.abc import Callable
import time
from threading import Event
from typing import Optional
from SSD220 import div
from SSD220 import get_pos
from SSD220 import move
from SSD220 import move_with_control
from SSD220 import move_to_origin
from SSD220 import set_res_gpib
from pyvisa.resources import MessageBasedResource


Point = list[str]

# Use micrometers as the base unit because it is the smallest unit used by the
# stage calibration, which avoids repeated unit conversion in movement code.
X_UM_PER_PULSE = 0.05
Y_UM_PER_PULSE = 10
DEFAULT_STANDALONE_Z_DOWN = 30000


@dataclass(frozen=True)
class DieLayout:
    """Describe the physical die arrangement on the prober stage.

    The layout uses die 1 as the reference position `[0, 0]`.
    X controls movement down between die rows, and Y controls movement along a row.
    Dies are grouped along each row, with an extra gap after each group.
    The coordinate convention follows stepper motion, not the visual direction
    on the stage. Die 3 is physically left of die 1. To place die 3 under the
    fixed probe, the stepper moves right, so movement along a die row increases
    Y. Die 2 is physically above die 1. To place die 2 under the fixed probe,
    the stepper moves down, and down is positive X pulse movement.

    Args:
        dies_per_row: Number of dies in each row.
        dies_per_group: Number of dies in one row group before a larger gap.
        die_spacing: Micrometer spacing between neighboring dies in the same group.
        group_gap: Extra micrometer spacing added between row groups.
        row_spacing: Micrometer spacing between the odd-die row and even-die row.
        second_row_y_offset: One-time Y offset applied to every second-row
            coordinate when transitioning from the first row.
    """

    dies_per_row: int
    dies_per_group: int
    die_spacing: int
    group_gap: int
    row_spacing: int
    second_row_y_offset: int = 0

    def die_positions(self) -> dict[str, Point]:
        """Return die positions for the full layout.

        Die `1` is the reference position `[0, 0]`. X controls the down
        movement between rows, and Y controls movement along each row.

        Returns:
            Dictionary mapping die number strings to `[x, y]` micrometer positions.
        """
        positions = {}
        total_dies = self.dies_per_row * 2

        for die_number in range(1, total_dies + 1):
            row = 0 if die_number % 2 == 1 else 1
            position_in_row = (die_number - 1) // 2
            group_index = position_in_row // self.dies_per_group

            x = row * self.row_spacing
            y = (
                position_in_row * self.die_spacing
                + group_index * self.group_gap
                + row * self.second_row_y_offset
            )

            positions[str(die_number)] = [str(x), str(y)]

        return positions


def die_travel_order(die_count: int) -> list[str]:
    """Return die travel order as odds first, then evens.

    Args:
        die_count: Total number of dies in the layout.

    Returns:
        Die numbers as strings ordered like `1, 3, ..., 31, 2, 4, ..., 32`.
    """
    odd_dies = [str(die) for die in range(1, die_count + 1, 2)]
    even_dies = [str(die) for die in range(2, die_count + 1, 2)]
    return odd_dies + even_dies[::-1]


def _um_to_pulse(distance_um: int, um_per_pulse: float) -> str:
    """Convert a signed micrometer distance to signed motor pulses.

    Args:
        distance_um: Signed movement distance in micrometers.
        um_per_pulse: Axis calibration in micrometers per pulse.

    Returns:
        Signed pulse distance as a string.
    """
    return str(round(distance_um / um_per_pulse))


def _relative_pulse(current: Point, target: Point) -> dict[str, str]:
    """Return the relative pulse move from one micrometer point to another.

    Args:
        current: Current `[x, y]` position in micrometers.
        target: Target `[x, y]` position in micrometers.

    Returns:
        Relative movement as `{"X": x_pulse, "Y": y_pulse}`.
    """
    x_um = int(target[0]) - int(current[0])
    y_um = int(target[1]) - int(current[1])
    return {"X": _um_to_pulse(x_um, X_UM_PER_PULSE), "Y": _um_to_pulse(y_um, Y_UM_PER_PULSE)}


def _move_to_home(stage: MessageBasedResource, home_position: dict[str, str]) -> None:
    """Move every recorded axis back to its home position."""
    axes = list(home_position)
    current_position = get_pos(stage, axes)
    pulse_to_home = {
        axis: str(int(home_position[axis]) - int(current_position[axis]))
        for axis in axes
    }
    move(stage, pulse_to_home, read_position=False)


"""================ Main Sequence Methods ================"""


def main(
    stage: MessageBasedResource,
    home_position: dict[str, str],
    contact_z: str,
    capture_el: Optional[Callable[[str], object]] = None,
    stop_requested: Optional[Callable[[], bool]] = None,
    resume_allowed: Optional[Event] = None,
) -> None:
    """Run the die probing movement sequence.

    Args:
        stage: Existing SSD220 instrument connection.
        home_position: Recorded workflow home position as `{"X": x, "Y": y}`.
        contact_z: Recorded Z contact position.
        capture_el: Optional callback called after Z moves down at each die.
        stop_requested: Optional callback returning `True` when Stop is clicked.
        resume_allowed: Optional event that is set while the sequence may run.

    Returns:
        None.
    """
    layout = DieLayout(
        dies_per_row=16,
        dies_per_group=4,
        die_spacing=9000,
        group_gap=12500,
        row_spacing=32500,
        second_row_y_offset=-5000,
    )
    die_positions = layout.die_positions()
    travel_order = die_travel_order(32)
    current_position = die_positions[travel_order[0]]

    div(stage, "X", "7")
    div(stage, "Y", "7")

    print("Start position:", get_pos(stage, ["X", "Y", "Z"]))
    print("Home position:", home_position)
    print("Contact Z:", contact_z)
    for die in travel_order:
        target_position = die_positions[die]
        xy_pulse = _relative_pulse(current_position, target_position)
        current_z = get_pos(stage, ["Z"])["Z"]
        z_down = str(int(contact_z) - int(current_z))
        z_up = str(-int(z_down))
        print(
            f"Moving to die {die}: position={target_position}, "
            f"xy_pulse={xy_pulse}, z_down={z_down}"
        )

        if not move_with_control(
            stage,
            xy_pulse,
            stop_requested=stop_requested,
            resume_allowed=resume_allowed,
        ):
            print("Stop requested during XY movement. Moving to home.")
            _move_to_home(stage, home_position)
            return

        if not move_with_control(
            stage,
            {"Z": z_down},
            stop_requested=stop_requested,
            resume_allowed=resume_allowed,
        ):
            print("Stop requested during Z-down movement. Moving to home.")
            _move_to_home(stage, home_position)
            return

        if capture_el is not None:
            if stop_requested is not None and stop_requested():
                print("Stop requested before EL capture. Moving to home.")
                _move_to_home(stage, home_position)
                return
            if not capture_el(die):
                print("EL capture failed or was cancelled. Moving to home.")
                _move_to_home(stage, home_position)
                return

        if not move_with_control(
            stage,
            {"Z": z_up},
            stop_requested=stop_requested,
            resume_allowed=resume_allowed,
        ):
            print("Stop requested during Z-up movement. Moving to home.")
            _move_to_home(stage, home_position)
            return

        current_position = target_position

    print("Returning to home.")
    _move_to_home(stage, home_position)
    print("End position:", get_pos(stage, ["X", "Y", "Z"]))


if __name__ == "__main__":

    stage1 = set_res_gpib("3")
    move_to_origin(stage1)
    try:
        home_position = get_pos(stage1, ["X", "Y", "Z"])
        main(
            stage=stage1,
            home_position=home_position,
            contact_z=str(int(home_position["Z"]) + DEFAULT_STANDALONE_Z_DOWN),
        )
    finally:
        stage1.close()
