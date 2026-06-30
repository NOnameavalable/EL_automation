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
from SSD220 import convert_axis_pulse
from SSD220 import get_pos
from SSD220 import move_with_control
from SSD220 import move_to_position
from SSD220 import set_res_gpib
from pyvisa.resources import MessageBasedResource


Point = dict[str, str]

# Use micrometers as the base unit because it is the smallest unit used by the
# stage calibration, which avoids repeated unit conversion in movement code.
X_UM_PER_PULSE = 0.05
Y_UM_PER_PULSE = 10
U_UM_PER_PULSE = 1.0
V_UM_PER_PULSE = 0.45
DEFAULT_STANDALONE_Z_DOWN = 30000
AXIS_UM_PER_PULSE = {
    "X": X_UM_PER_PULSE,
    "Y": Y_UM_PER_PULSE,
    "U": U_UM_PER_PULSE,
    "V": V_UM_PER_PULSE,
}


@dataclass(frozen=True)
class DieLayout:
    """Describe the physical die arrangement on the prober stage.

    The layout uses die 1 as the reference position.
    X controls movement down between die rows, and Y controls movement along a row.
    X and V apply the second-row die-upside-down offset. U applies the original
    second-row row correction. Y and U apply the center offset on the second row.
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
        second_row_die_upside_down_offset: One-time X/V offset applied to every
            second-row coordinate.
        second_row_u_row_offset: One-time U row correction applied to every
            second-row coordinate.
        second_row_center_offset: One-time Y/U center offset applied to every
            second-row coordinate.
    """

    dies_per_row: int
    dies_per_group: int
    die_spacing: int
    group_gap: int
    row_spacing: int
    second_row_die_upside_down_offset: int = 0
    second_row_u_row_offset: int = 0
    second_row_center_offset: int = 0

    def die_positions(self) -> dict[str, Point]:
        """Return die positions for the full layout.

        Die `1` is the reference position. X controls the down movement between
        rows, Y controls movement along each row, X/V carry the die-upside-down
        second-row offset, U carries the row correction, and Y/U carry the
        second-row center offset.

        Returns:
            Dictionary mapping die number strings to axis micrometer positions.
        """
        positions = {}
        total_dies = self.dies_per_row * 2

        for die_number in range(1, total_dies + 1):
            row = 0 if die_number % 2 == 1 else 1
            position_in_row = (die_number - 1) // 2
            group_index = position_in_row // self.dies_per_group

            x = row * (self.row_spacing + self.second_row_die_upside_down_offset)
            y = (
                position_in_row * self.die_spacing
                + group_index * self.group_gap
                + row * self.second_row_center_offset
            )
            u = row * (self.second_row_u_row_offset + self.second_row_center_offset)
            v = row * self.second_row_die_upside_down_offset

            positions[str(die_number)] = {
                "X": str(x),
                "Y": str(y),
                "U": str(u),
                "V": str(v),
            }

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
    """Return the relative pulse move from one micrometer position to another.

    Args:
        current: Current axis positions in micrometers.
        target: Target axis positions in micrometers.

    Returns:
        Relative movement by axis in pulses.
    """
    return {
        axis: _um_to_pulse(
            int(target[axis]) - int(current[axis]),
            AXIS_UM_PER_PULSE[axis],
        )
        for axis in target
    }


"""================ Main Sequence Methods ================"""


def main(
    stage: MessageBasedResource,
    home_position: dict[str, str],
    die_positions: dict[str, Point],
    contact_z: str,
    capture_el: Optional[Callable[[str], object]] = None,
    focus_reference_score: Optional[float] = None,
    get_focus_score: Optional[Callable[[str], float]] = None,
    refocus: Optional[Callable[[str], bool]] = None,
    focus_threshold_ratio: float = 0.05,
    stop_requested: Optional[Callable[[], bool]] = None,
    resume_allowed: Optional[Event] = None,
) -> None:
    """Run the die probing movement sequence.

    Args:
        stage: Existing SSD220 instrument connection.
        home_position: Recorded workflow home position as `{"X": x, "Y": y}`.
        die_positions: Die positions generated when workflow home was set.
        contact_z: Recorded Z contact position.
        capture_el: Optional callback called after Z moves down at each die.
        focus_reference_score: Optional starting focus score reference.
        get_focus_score: Optional callback returning the current focus score.
        refocus: Optional callback that runs autofocus and returns success.
        focus_threshold_ratio: Score-drop ratio that triggers autofocus.
        stop_requested: Optional callback returning `True` when Stop is clicked.
        resume_allowed: Optional event that is set while the sequence may run.

    Returns:
        None.
    """
    travel_order = die_travel_order(len(die_positions))
    current_position = die_positions[travel_order[0]]

    div(stage, "X", "7")
    div(stage, "Y", "7")

    print("Start position:", get_pos(stage, ["X", "Y", "Z"]))
    print("Home position:", home_position)
    print("Contact Z:", contact_z)
    if focus_reference_score is not None:
        if get_focus_score is None or refocus is None:
            raise ValueError("focus score checking requires get_focus_score and refocus callbacks")
        print("Focus reference score:", f"{focus_reference_score:.2f}")

    for die in travel_order:
        target_position = die_positions[die]
        xy_pulse = _relative_pulse(current_position, target_position)
        current_z = get_pos(stage, ["Z"])["Z"]
        z_down = convert_axis_pulse(
            "Z",
            int(contact_z) - int(current_z),
        )
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
            print("Stop requested during XY movement. Stopping sequence.")
            return

        if focus_reference_score is not None:
            if stop_requested is not None and stop_requested():
                print("Stop requested before focus check. Stopping sequence.")
                return

            try:
                current_focus_score = get_focus_score(die)
            except Exception as exc:
                print(f"Focus score check failed for die {die}: {exc}. Moving to home.")
                move_to_position(
                    stage,
                    home_position,
                    read_position=False,
                    stop_requested=stop_requested,
                    resume_allowed=resume_allowed,
                )
                return

            focus_threshold = abs(focus_reference_score) * focus_threshold_ratio
            minimum_focus_score = focus_reference_score - focus_threshold
            print(
                f"Focus check die {die}: score={current_focus_score:.2f}, "
                f"reference={focus_reference_score:.2f}, "
                f"minimum={minimum_focus_score:.2f}"
            )

            if current_focus_score < minimum_focus_score:
                print(f"Focus score below threshold for die {die}. Running autofocus.")
                try:
                    refocus_succeeded = refocus(die)
                except Exception as exc:
                    print(f"Autofocus failed for die {die}: {exc}. Moving to home.")
                    move_to_position(
                        stage,
                        home_position,
                        read_position=False,
                        stop_requested=stop_requested,
                        resume_allowed=resume_allowed,
                    )
                    return

                if not refocus_succeeded:
                    if stop_requested is not None and stop_requested():
                        print("Stop requested during autofocus. Stopping sequence.")
                        return
                    print("Autofocus failed or was cancelled. Moving to home.")
                    move_to_position(
                        stage,
                        home_position,
                        read_position=False,
                        stop_requested=stop_requested,
                        resume_allowed=resume_allowed,
                    )
                    return

                try:
                    focus_reference_score = get_focus_score(die)
                except Exception as exc:
                    print(f"Focus reference update failed for die {die}: {exc}. Moving to home.")
                    move_to_position(
                        stage,
                        home_position,
                        read_position=False,
                        stop_requested=stop_requested,
                        resume_allowed=resume_allowed,
                    )
                    return
                print("Updated focus reference score:", f"{focus_reference_score:.2f}")

        if not move_with_control(
            stage,
            {"Z": z_down},
            stop_requested=stop_requested,
            resume_allowed=resume_allowed,
        ):
            print("Stop requested during Z-down movement. Stopping sequence.")
            return

        if capture_el is not None:
            if stop_requested is not None and stop_requested():
                print("Stop requested before EL capture. Stopping sequence.")
                return
            if not capture_el(die):
                if stop_requested is not None and stop_requested():
                    print("Stop requested during EL capture. Stopping sequence.")
                    return
                print("EL capture failed or was cancelled. Moving to home.")
                move_to_position(
                    stage,
                    home_position,
                    read_position=False,
                    stop_requested=stop_requested,
                    resume_allowed=resume_allowed,
                )
                return

        if not move_with_control(
            stage,
            {"Z": z_up},
            stop_requested=stop_requested,
            resume_allowed=resume_allowed,
        ):
            print("Stop requested during Z-up movement. Stopping sequence.")
            return

        current_position = target_position

    print("Returning to home.")
    move_to_position(
        stage,
        home_position,
        read_position=False,
        stop_requested=stop_requested,
        resume_allowed=resume_allowed,
    )
    print("End position:", get_pos(stage, ["X", "Y", "Z"]))


if __name__ == "__main__":

    stage1 = set_res_gpib("3")
    move_to_position(stage1, {"X": "0", "Y": "0", "Z": "0"})
    try:
        home_position = get_pos(stage1, ["X", "Y", "Z"])
        main(
            stage=stage1,
            home_position=home_position,
            die_positions=DieLayout(
                dies_per_row=16,
                dies_per_group=4,
                die_spacing=9000,
                group_gap=12500,
                row_spacing=32500,
                second_row_die_upside_down_offset=5000,
                second_row_u_row_offset=5000,
                second_row_center_offset=250,
            ).die_positions(),
            contact_z=str(int(home_position["Z"]) + DEFAULT_STANDALONE_Z_DOWN),
        )
    finally:
        stage1.close()
