"""Shared control helpers for the two Keithley 2400 current sources."""

from __future__ import annotations

import math
from typing import Optional

from pyvisa.resources import MessageBasedResource


DEFAULT_CURRENT_RANGE_A = 0.1
SUPPORTED_CURRENT_RANGES_A = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0)


def setup_keithley(
    keithley: MessageBasedResource,
    compliance_voltage: float,
    *,
    current_range: float = DEFAULT_CURRENT_RANGE_A,
    terminals: str = "FRON",
) -> None:
    """Configure an open Keithley 2400 as a safely disabled current source.

    The instrument is configured at 0 A with its output off. Setting a current
    later does not enable the output.

    Args:
        keithley: Open PyVISA message-based resource for the Keithley 2400.
        compliance_voltage: Maximum voltage, in volts, that the instrument may
            develop while trying to maintain the requested source current.
        current_range: Bipolar source-current range in amperes. For example,
            `0.1` selects the -100 mA to +100 mA range.
        terminals: Output connector set, either `"FRON"` or `"REAR"`.

    Returns:
        None.

    Raises:
        ValueError: If compliance, range, or terminal selection is invalid.
        RuntimeError: If the connected instrument is not a Keithley 2400.
        Exception: If a VISA query or SCPI configuration command fails. The
            function attempts to disable output and closes the session first.
    """
    compliance_voltage = float(compliance_voltage)
    current_range = float(current_range)
    terminals = terminals.upper()

    if not math.isfinite(compliance_voltage) or compliance_voltage <= 0:
        raise ValueError("Compliance voltage must be a positive finite value")
    if current_range not in SUPPORTED_CURRENT_RANGES_A:
        raise ValueError(
            "Current range must be one of "
            f"{', '.join(f'{value:g}' for value in SUPPORTED_CURRENT_RANGES_A)} A"
        )
    if terminals not in {"FRON", "REAR"}:
        raise ValueError("Terminals must be 'FRON' or 'REAR'")

    try:
        # Ask the instrument for manufacturer, model, serial, and firmware data.
        identity = keithley.query("*IDN?").strip()
        if "MODEL 2400" not in identity.upper():
            raise RuntimeError(
                f"Connected instrument is not a Keithley Model 2400: {identity}"
            )

        keithley.write(":ABOR")  # Stop any trigger model, sweep, or pending operation.
        keithley.write(":OUTP OFF")  # Disable source output before reconfiguration.
        keithley.write("*CLS")  # Clear the event registers and queued SCPI errors.
        keithley.write(f":ROUT:TERM {terminals}")  # Select front or rear connectors.
        keithley.write(":SYST:RSEN OFF")  # Use local 2-wire rather than remote 4-wire sense.
        keithley.write(":SOUR:FUNC CURR")  # Configure the source to produce current.
        keithley.write(":SOUR:CURR:MODE FIX")  # Use one fixed level rather than a sweep.
        keithley.write(f":SOUR:CURR:RANG {current_range:g}")  # Select current-source range.
        keithley.write(":SOUR:CURR:LEV 0")  # Start from a zero-amp source level.
        # Limit the voltage generated while the instrument maintains source current.
        keithley.write(f":SENS:VOLT:PROT {compliance_voltage:g}")
        keithley.write(':SENS:FUNC "VOLT", "CURR"')  # Enable voltage/current sensing.
        keithley.write(":SENS:VOLT:RANG:AUTO ON")  # Automatically select voltage measure range.
        keithley.write(":OUTP:SMOD NORM")  # Use normal output-off behavior, not high impedance.
        keithley.write(":OUTP OFF")  # Confirm output remains disabled after setup.
    except Exception:
        try:
            keithley.write(":OUTP OFF")  # Best-effort shutdown after setup failure.
        except Exception:
            pass
        keithley.close()
        raise


def set_keithley_current(
    keithley: MessageBasedResource,
    current_amps: float,
) -> None:
    """Set signed source current while requiring the output to be off.

    A positive value uses the normal HI-to-LO direction. A negative value
    reverses source-current polarity without physically swapping the leads.

    Args:
        keithley: Open and configured Keithley 2400 VISA resource.
        current_amps: Signed current level in amperes.

    Returns:
        None.

    Raises:
        ValueError: If the current is non-finite or outside the active range.
        RuntimeError: If output is enabled while changing the current level.
        Exception: If a VISA query or SCPI command fails.
    """
    current_amps = float(current_amps)
    if not math.isfinite(current_amps):
        raise ValueError("Current level must be finite")

    # Query whether source output is disabled (0) or enabled (1).
    if int(float(keithley.query(":OUTP?").strip())) != 0:
        raise RuntimeError("Turn the Keithley output off before changing current")

    # Query the active bipolar source-current range for bounds validation.
    current_range = abs(float(keithley.query(":SOUR:CURR:RANG?").strip()))
    if abs(current_amps) > current_range:
        raise ValueError(
            f"Current {current_amps:g} A exceeds the configured "
            f"{current_range:g} A source range"
        )

    # Program the signed fixed-current level; this does not enable output.
    keithley.write(f":SOUR:CURR:LEV {current_amps:g}")


def set_keithley_output(
    keithley: MessageBasedResource,
    enabled: bool,
) -> None:
    """Enable or disable the Keithley source output.

    Args:
        keithley: Open and configured Keithley 2400 VISA resource.
        enabled: `True` to energize the output or `False` to disable it.

    Returns:
        None.

    Raises:
        Exception: If the VISA write or SCPI output command fails.
    """
    # Close or open the source-output state using one explicit boolean control.
    keithley.write(":OUTP ON" if enabled else ":OUTP OFF")


def close_keithley(keithley: MessageBasedResource) -> None:
    """Force a safe source state and close the VISA session.

    Every cleanup command is attempted even if an earlier command fails. The
    VISA session is always closed afterward.

    Args:
        keithley: Open Keithley 2400 VISA resource to shut down and close.

    Returns:
        None.

    Raises:
        Exception: The first SCPI cleanup error, after all cleanup commands
            have been attempted and the VISA session has been closed.
    """
    first_error: Optional[Exception] = None
    try:
        for command in (
            ":OUTP OFF",  # Disable the electrical output immediately.
            ":ABOR",  # Stop any trigger model, sweep, or pending operation.
            ":SOUR:CURR:LEV 0",  # Leave the programmed current at zero amps.
        ):
            try:
                keithley.write(command)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
    finally:
        keithley.close()

    if first_error is not None:
        raise first_error
