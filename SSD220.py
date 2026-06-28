# -*- coding: utf-8 -*-
"""
Created on Thu Nov 28 16:00:03 2024

@author: BenWatts, Steven Li
"""
import time
from collections.abc import Callable
from threading import Event
from typing import Literal, Optional, Union

import pyvisa
from pyvisa.resources import MessageBasedResource


Axis = Literal["X", "Y", "Z", "U", "V", "W"]
Direction = Literal["CW", "CCW"]
Point = dict[str, str]

# Axis-keyed dictionaries keep multi-axis movement explicit and avoid relying
# on list position meanings such as `[x, y]`.
Pulse = dict[str, Union[str, list[str]]]

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

# Assign the controller direction used for a positive logical pulse on each
# axis. A negative logical pulse automatically uses the opposite direction.
POSITIVE_DIRECTION_BY_AXIS: dict[Axis, Direction] = {
    "X": "CCW",
    "Y": "CCW",
    "Z": "CCW",
    "U": "CCW",
    "V": "CCW",
    "W": "CCW",
}
CONTROLLER_POSITIVE_DIRECTION: Direction = "CW"
CONTROLLER_NEGATIVE_DIRECTION: Direction = "CCW"

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


def set_res_gpib(address: str, bus: str = "0") -> MessageBasedResource:
    """Open a message-based instrument at the requested GPIB address.

    Args:
        address: GPIB address number as a string, such as `"4"`.
        bus: GPIB bus number as a string.

    Returns:
        Open PyVISA message-based instrument resource.

    Raises:
        ValueError: If the requested GPIB resource is not connected.
        TypeError: If the opened resource does not support message commands.
    """
    rm = pyvisa.ResourceManager()
    resource_name = f"GPIB{bus}::{address}::INSTR"
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


def get_pos(inst: MessageBasedResource, axes: Optional[list[Axis]] = None) -> Point:
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


def convert_axis_pulse(
    axis: Axis,
    pulse_value: Union[str, int],
) -> str:
    """Convert between logical and controller-coordinate pulse signs.

    The conversion is its own inverse because it only changes the sign. It is
    used both before movement and for deltas calculated from `POS?`.
    """
    multiplier = 1 if POSITIVE_DIRECTION_BY_AXIS[axis] == "CW" else -1
    return str(int(pulse_value) * multiplier)


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
        pulse_value: Signed pulse distance in controller coordinates.
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
    direction = (
        CONTROLLER_POSITIVE_DIRECTION
        if pulse > 0
        else CONTROLLER_NEGATIVE_DIRECTION
    )

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
    fast_speed: Optional[dict[str, str]] = None,
    low_speed: Optional[dict[str, str]] = None,
    wait: bool = True,
    reaction_time: float = 0.5,
    read_position: bool = True,
) -> Point:
    """Move by signed pulse distances and return current axis positions.

    Public pulse signs use physical directions: positive means right for X/V/W
    and up for Y/Z/U. SSD220 translates that logical sign into controller
    coordinates before sending a positive pulse count plus CW/CCW direction.
    Each axis can receive one pulse value or a list to execute in order.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        pulse: Signed logical relative movement. Use a dictionary such as
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
            controller_pulse = convert_axis_pulse(axis, axis_pulse)
            if _move_axis(
                inst,
                axis,
                controller_pulse,
                fast_speed[axis],
                low_speed[axis],
            ):
                if wait:
                    _mot_wait(inst, axis=axis, reaction_time=reaction_time)

    return get_pos(inst) if read_position else {}


def move_with_control(
    inst: MessageBasedResource,
    pulse: Pulse,
    fast_speed: Optional[dict[str, str]] = None,
    low_speed: Optional[dict[str, str]] = None,
    stop_requested: Optional[Callable[[], bool]] = None,
    resume_allowed: Optional[Event] = None,
    stop_mode: str = "0",
    poll_delay: float = 0.1,
    reaction_time: float = 0.5,
    poll_callback: Optional[Callable[[], None]] = None,
) -> bool:
    """Move by signed pulses while checking pause and stop controls.

    Movement is started with ``wait=False`` so this helper can poll the
    controller and react while an axis is still moving. If pause is requested,
    the active axis is stopped, the helper waits for resume, recalculates the
    remaining pulse distance, and continues toward the original target.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        pulse: Signed logical relative movement by axis.
        fast_speed: Optional fast speeds as an axis dictionary.
        low_speed: Optional low/start speeds as an axis dictionary.
        stop_requested: Optional callback returning ``True`` when motion should
            stop immediately.
        resume_allowed: Optional event that is set while motion may continue.
        stop_mode: SSD220 stop mode sent with ``AXI<axis>:STOP``.
        poll_delay: Seconds to wait between motion-status checks.
        reaction_time: Extra seconds to wait after each axis completes.
        poll_callback: Optional callback run during polling, useful for GUI
            event processing when this helper is called on Tk's main thread.

    Returns:
        ``True`` when all movement finishes normally, or ``False`` when stop is
        requested.
    """
    fast_speed = DEFAULT_FAST_SPEED if fast_speed is None else fast_speed
    low_speed = DEFAULT_LOW_SPEED if low_speed is None else low_speed

    for axis, axis_pulses in pulse.items():
        axis_pulses = [axis_pulses] if isinstance(axis_pulses, str) else axis_pulses
        for axis_pulse in axis_pulses:
            if not _move_axis_with_control(
                inst,
                axis,
                str(axis_pulse),
                fast_speed[axis],
                low_speed[axis],
                stop_requested=stop_requested,
                resume_allowed=resume_allowed,
                stop_mode=stop_mode,
                poll_delay=poll_delay,
                reaction_time=reaction_time,
                poll_callback=poll_callback,
            ):
                return False

    return True


def _move_axis_with_control(
    inst: MessageBasedResource,
    axis: Axis,
    pulse_value: str,
    fast_speed: str,
    low_speed: str,
    stop_requested: Optional[Callable[[], bool]],
    resume_allowed: Optional[Event],
    stop_mode: str,
    poll_delay: float,
    reaction_time: float,
    poll_callback: Optional[Callable[[], None]],
) -> bool:
    """Move one axis to its converted controller target with pause/stop."""
    controller_pulse = convert_axis_pulse(axis, pulse_value)
    target_position = int(_get_axis_pos(inst, axis).strip()) + int(controller_pulse)

    while True:
        current_position = int(_get_axis_pos(inst, axis).strip())
        remaining_pulse = target_position - current_position
        if remaining_pulse == 0:
            return True

        if not _move_axis(inst, axis, str(remaining_pulse), fast_speed, low_speed):
            return True

        while int(inst.query(f"AXI{axis}:Motion?").strip()) == 1:
            if _stop_is_requested(stop_requested):
                _stop_axis(inst, axis, stop_mode, poll_delay, poll_callback)
                return False

            if resume_allowed is not None and not resume_allowed.is_set():
                _stop_axis(inst, axis, stop_mode, poll_delay, poll_callback)
                if not _wait_for_resume(
                    stop_requested,
                    resume_allowed,
                    poll_delay,
                    poll_callback,
                ):
                    return False
                break

            _run_poll_callback(poll_callback)
            time.sleep(poll_delay)
        else:
            time.sleep(reaction_time)
            return True


def _stop_is_requested(stop_requested: Optional[Callable[[], bool]]) -> bool:
    """Return whether a stop callback exists and is currently active."""
    return stop_requested is not None and stop_requested()


def _wait_for_resume(
    stop_requested: Optional[Callable[[], bool]],
    resume_allowed: Event,
    poll_delay: float,
    poll_callback: Optional[Callable[[], None]],
) -> bool:
    """Wait for resume while still checking for stop requests."""
    while not resume_allowed.is_set():
        if _stop_is_requested(stop_requested):
            return False
        _run_poll_callback(poll_callback)
        time.sleep(poll_delay)
    return True


def _stop_axis(
    inst: MessageBasedResource,
    axis: Axis,
    stop_mode: str,
    poll_delay: float,
    poll_callback: Optional[Callable[[], None]],
) -> None:
    """Stop one axis and wait until the controller reports motion complete."""
    inst.write(f"AXI{axis}:STOP {stop_mode}")
    while int(inst.query(f"AXI{axis}:Motion?").strip()) == 1:
        _run_poll_callback(poll_callback)
        time.sleep(poll_delay)


def _run_poll_callback(poll_callback: Optional[Callable[[], None]]) -> None:
    """Run an optional polling callback without letting GUI errors kill motion."""
    if poll_callback is None:
        return
    try:
        poll_callback()
    except Exception:
        pass


def move_to_origin(
    inst: MessageBasedResource,
    axes: Optional[list[Axis]] = None,
    fast_speed: Optional[dict[str, str]] = None,
    low_speed: Optional[dict[str, str]] = None,
    wait: bool = True,
    reaction_time: float = 0.5,
) -> Point:
    """Move selected axes back to their startup origin.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        axes: Axes to move back to zero. If omitted, all configured axes are
            moved.
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
    return move_to_position(
        inst,
        {axis: "0" for axis in axes},
        axes=axes,
        fast_speed=fast_speed,
        low_speed=low_speed,
        wait=wait,
        reaction_time=reaction_time,
    )


def move_to_position(
    inst: MessageBasedResource,
    target_position: Point,
    axes: Optional[list[Axis]] = None,
    fast_speed: Optional[dict[str, str]] = None,
    low_speed: Optional[dict[str, str]] = None,
    wait: bool = True,
    reaction_time: float = 0.5,
    read_position: bool = True,
) -> Point:
    """Move selected axes to recorded controller-coordinate positions.

    Args:
        inst: Open PyVISA instrument resource for the SSD220 controller.
        target_position: Axis positions as returned by `get_pos()`, such as
            `{"X": "0", "Y": "0", "Z": "1000"}`.
        axes: Axes to move. If omitted, moves every axis in `target_position`.
        fast_speed: Optional fast speeds as an axis dictionary.
        low_speed: Optional low/start speeds as an axis dictionary.
        wait: If `True`, wait for each moved axis to finish before returning.
        reaction_time: Extra seconds to wait after each moved axis reports
            that motion is complete.
        read_position: If `True`, query and return current positions after
            movement. If `False`, skip position readback.

    Returns:
        Current positions for all configured axes if `read_position` is `True`.
        Otherwise, an empty dictionary.
    """
    axes = list(target_position) if axes is None else axes
    current = get_pos(inst, axes)
    pulse_to_target = {
        axis: convert_axis_pulse(
            axis,
            int(target_position[axis]) - int(current[axis]),
        )
        for axis in axes
    }
    return move(
        inst,
        pulse_to_target,
        fast_speed=fast_speed,
        low_speed=low_speed,
        wait=wait,
        reaction_time=reaction_time,
        read_position=read_position,
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
