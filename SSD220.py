# -*- coding: utf-8 -*-
"""
Created on Thu Nov 28 16:00:03 2024

@author: BenWatts, Steven Li
"""
import time
from typing import Literal

import pyvisa
from pyvisa.resources import MessageBasedResource


Axis = Literal["X", "Y", "Z", "U", "V", "W"]
Direction = Literal["CW", "CCW"]
Point = dict[str, str]

# Axis-keyed dictionaries keep multi-axis movement explicit and avoid relying
# on list position meanings such as `[x, y]`.
Pulse = dict[str, str | list[str]]

AXES: tuple[Axis, ...] = ("X", "Y", "Z", "U", "V", "W")

AXIS_INDEX: dict[str, str] = {
    "X": "0",
    "Y": "1",
    "Z": "2",
    "U": "3",
    "V": "4",
    "W": "5",
}

SPEED_TABLE_INDEX = "0"

# Positive/negative pulse signs are converted to the controller's direction words.
POSITIVE_DIRECTION: Direction = "CW"
NEGATIVE_DIRECTION: Direction = "CCW"
REVERSED_DIRECTION_AXES: set[Axis] = {"V"}

DEFAULT_FAST_SPEED: dict[str, str] = {
    "X": "70000",
    "Y": "1000",
    "Z": "100000",
    "U": "1000",
    "V": "5000",
    "W": "10000",
}

DEFAULT_LOW_SPEED: dict[str, str] = {
    "X": "5000",
    "Y": "500",
    "Z": "5000",
    "U": "500",
    "V": "1000",
    "W": "5000",
}


def set_res_gpib(address: str) -> MessageBasedResource:
    """Open the SSD220 controller at the requested GPIB address.

    Args:
        address: GPIB address number as a string, such as `"4"`.

    Returns:
        Open PyVISA message-based instrument resource for the controller.

    Raises:
        ValueError: If the requested GPIB resource is not connected.
        TypeError: If the opened resource does not support message commands.
    """
    rm = pyvisa.ResourceManager()
    resource_name = f"GPIB1::{address}::INSTR"
    resources = rm.list_resources()

    print(resources)
    if resource_name not in resources:
        raise ValueError(
            f"Could not find {resource_name}. Available resources: {resources}"
        )

    inst = rm.open_resource(resource_name)
    if not isinstance(inst, MessageBasedResource):
        raise TypeError(f"{resource_name} is not a message-based VISA resource.")
    inst.timeout = 10000
    print(inst.query("*IDN?"))

    return inst


def _get_axis_pos(inst: MessageBasedResource, axis: Axis) -> str:
    """Return the current controller position for one axis.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        axis: Axis to query.

    Returns:
        Current controller position for the selected axis as a string.
    """
    return inst.query(f"AXI{axis}:POS?")


def get_pos(inst: MessageBasedResource, axes: list[Axis] | None = None) -> Point:
    """Return the current controller position for selected axes.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        axes: Axes to query. If omitted, queries all configured axes.

    Returns:
        Current positions as a dictionary such as `{"X": "0", "Y": "0"}`.
    """
    axes = list(AXES) if axes is None else axes
    return {axis: _get_axis_pos(inst, axis).strip() for axis in axes}


def _mot_wait(
    inst: MessageBasedResource,
    axis: Axis = "X",
    poll_delay: float = 0.1,
    reaction_time: float = 0.5,
) -> None:
    """Wait until the selected axis reports that motion is complete.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        axis: Axis to poll for motion status.
        poll_delay: Seconds to wait between motion-status queries.
        reaction_time: Extra seconds to wait after the axis reports that
            motion is complete.

    Returns:
        None.
    """
    while int(inst.query(f"AXI{axis}:Motion?").strip()) == 1:
        time.sleep(poll_delay)
    time.sleep(reaction_time)


def _move_axis(
    inst: MessageBasedResource,
    axis: Axis,
    pulse_value: str,
    fast_speed: str = "5000",
    low_speed: str = "2000",
) -> bool:
    """Move one axis by a relative pulse distance.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        axis: Axis to move.
        pulse_value: Signed pulse distance as a string. Negative values are
            converted to the controller's negative direction.
        fast_speed: Fast speed sent to the controller.
        low_speed: Low/start speed sent to the controller.

    Returns:
        `True` if a movement command was sent, or `False` if `pulse_value` is
        zero and no movement was needed.
    """
    pulse = int(pulse_value)
    if pulse == 0:
        return False

    # The controller expects a positive pulse count plus a separate direction.
    direction = POSITIVE_DIRECTION if pulse > 0 else NEGATIVE_DIRECTION
    if axis in REVERSED_DIRECTION_AXES:
        direction = NEGATIVE_DIRECTION if direction == POSITIVE_DIRECTION else POSITIVE_DIRECTION

    cmd = (
        f"AXI{axis}:F{SPEED_TABLE_INDEX} {fast_speed}:"
        f"L{SPEED_TABLE_INDEX} {low_speed}:"
        f"PULS {abs(pulse)}:GO {direction}"
    )
    inst.write(cmd)
    return True


def move(
    inst: MessageBasedResource,
    pulse: Pulse,
    fast_speed: dict[str, str] | None = None,
    low_speed: dict[str, str] | None = None,
    wait: bool = True,
    reaction_time: float = 0.5,
    read_position: bool = True,
) -> Point:
    """Move by signed pulse distances and return current axis positions.

    Negative input pulses are converted to a controller direction. The SSD220
    command still receives a positive pulse count. Each axis can receive either
    one pulse value or a list of pulse values to execute in order.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        pulse: Signed relative pulse movement. Use a dictionary such as
            `{"X": "1000", "Z": ["10000", "-10000"]}`.
        fast_speed: Optional fast speeds as an axis dictionary. If omitted,
            uses `DEFAULT_FAST_SPEED`.
        low_speed: Optional low/start speeds as an axis dictionary. If omitted,
            uses `DEFAULT_LOW_SPEED`.
        wait: If `True`, wait for each moved axis to finish before returning.
        reaction_time: Extra seconds to wait after each moved axis reports
            that motion is complete.
        read_position: If `True`, query and return current positions after
            movement. If `False`, skip position readback.

    Returns:
        Current positions for all configured axes if `read_position` is `True`.
        Otherwise, an empty dictionary.
    """
    fast_speed = DEFAULT_FAST_SPEED if fast_speed is None else fast_speed
    low_speed = DEFAULT_LOW_SPEED if low_speed is None else low_speed

    for axis, axis_pulses in pulse.items():
        axis_pulses = [axis_pulses] if isinstance(axis_pulses, str) else axis_pulses
        for axis_pulse in axis_pulses:
            if _move_axis(
                inst,
                axis,
                axis_pulse,
                fast_speed[axis],
                low_speed[axis],
            ):
                if wait:
                    _mot_wait(inst, axis=axis, reaction_time=reaction_time)

    return get_pos(inst) if read_position else {}


def move_to_origin(
    inst: MessageBasedResource,
    axes: list[Axis] | None = None,
    fast_speed: dict[str, str] | None = None,
    low_speed: dict[str, str] | None = None,
    wait: bool = True,
    reaction_time: float = 0.5,
) -> Point:
    """Move selected axes back to their startup origin.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        axes: Axes to move back to zero. If omitted, only X/Y are moved for
            backward compatibility with the die-stage workflow.
        fast_speed: Optional fast speeds as an axis dictionary. If omitted,
            `move()` uses its default speeds.
        low_speed: Optional low/start speeds as an axis dictionary. If omitted,
            `move()` uses its default speeds.
        wait: If `True`, wait for each moved axis to finish before returning.
        reaction_time: Extra seconds to wait after each moved axis reports
            that motion is complete.

    Returns:
        Current positions for all configured axes.
    """
    axes = list(AXES) if axes is None else axes
    current = get_pos(inst, axes)
    pulse_to_origin = {axis: str(-int(current[axis])) for axis in axes}
    return move(
        inst,
        pulse_to_origin,
        fast_speed=fast_speed,
        low_speed=low_speed,
        wait=wait,
        reaction_time=reaction_time,
    )


def div(inst: MessageBasedResource, axis: Axis = "X", div_n: str = "7") -> None:
    """Set the driver division value for one axis.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        axis: Axis whose driver division should be changed.
        div_n: Driver division value sent to the controller.

    Returns:
        None.
    """
    inst.write(f"AXI{axis}:DRDIV {div_n}")


def set_all_axes_speed_table(
    inst: MessageBasedResource,
    speed_table_index: str = SPEED_TABLE_INDEX,
) -> None:
    """Select one speed table for every configured axis."""
    for axis in AXES:
        inst.write(f"AXI{axis}:SELSP {speed_table_index}")


if __name__ == "__main__":
    inst1 = set_res_gpib("3")

    print("Before:", get_pos(inst1))

    move(
        inst1,
        pulse={"Y": "-300000"},
        fast_speed={"Y": "10000"},
        low_speed={"Y": "5000"},
    )

    print("After:", get_pos(inst1))
