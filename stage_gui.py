"""Simple stage-control GUI skeleton.

This first version only creates triangle movement buttons. Hardware movement
will be connected after the GUI layout is agreed on.
"""

import tkinter as tk
from tkinter import filedialog
import threading
from collections.abc import Callable
from typing import Literal, Optional

import pandas as pd
from pyvisa.resources import MessageBasedResource

from Keithley2400 import (
    close_keithley,
    set_keithley_current,
    set_keithley_output,
    setup_keithley,
)
from SSD220 import (
    Axis,
    AXES,
    DEFAULT_FAST_SPEED,
    DEFAULT_LOW_SPEED,
    get_pos,
    move,
    set_all_axes_speed_table,
    set_res_gpib,
)
from el_station import LucamStreamApp
from YeloModuleImageCapture import main as run_yelo_main

PAD_BACKGROUND = "#dedede"
WINDOW_BACKGROUND = "#f4f4f4"
PAD_HEIGHT = 168
DirectionSign = Literal["1", "-1"]
GPIB_ADDRESS = "3"
KEITHLEY_GPIB_BUS = "0"
MOTOR_GPIB_BUS = "0"
LIGHT_KEITHLEY_ADDRESS = "13"
PROBE_KEITHLEY_ADDRESS = ""  # Set this before enabling automated EL capture.
LIGHT_COMPLIANCE_V = 15.0
PROBE_COMPLIANCE_V = 2.5
KEITHLEY_CURRENT_RANGE_A = 1.0
JOG_PULSE = "1000000"
STOP_MODE = "0"
REFERENCE_SLIDER_SPEED = 50
MIN_SPEED_MULTIPLIER = 0.1
DIE_CONFIG_HEADER_ROW = 14
DIE_CONFIG_DATA_ROWS = 16
MAX_FAST_SPEED = 999999
MAX_LOW_SPEED = 9999

# These axes are mounted opposite to the current button intuition. Invert their
# signs so pressing left/up moves the hardware left/up from the user's view.
REVERSED_BUTTON_AXES: set[Axis] = {"Y", "Z", "W"}


class TrianglePad(tk.Frame):
    """Four-button triangle pad arranged like a gamepad directional control."""

    def __init__(
        self,
        master: tk.Misc,
        horizontal_axis: Axis,
        vertical_axis: Axis,
        on_press: Callable[[Axis, DirectionSign], None],
    ) -> None:
        """Create a triangle button pad.

        Args:
            master: Parent Tk widget.
            horizontal_axis: Axis controlled by the left/right triangles.
            vertical_axis: Axis controlled by the up/down triangles.
            on_press: Callback receiving the selected axis and signed direction.
        """
        super().__init__(master, bg=PAD_BACKGROUND)
        self._horizontal_axis = horizontal_axis
        self._vertical_axis = vertical_axis
        self._on_press = on_press

        # Arrange the four triangle buttons like a directional gamepad.
        self._add_triangle("up", row=0, column=1)
        self._add_triangle("left", row=1, column=0)
        self._add_triangle("right", row=1, column=2)
        self._add_triangle("down", row=2, column=1)

        # Reserve the center cell so this pad matches the B pad, which has the
        # Z control placed in the center.
        self.grid_rowconfigure(1, minsize=72)
        self.grid_columnconfigure(1, minsize=72)

    def _add_triangle(self, direction: str, row: int, column: int) -> None:
        """Add one canvas triangle button."""
        canvas = tk.Canvas(
            self,
            width=48,
            height=48,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            bg=self.cget("bg"),
        )
        canvas.grid(row=row, column=column)

        # Canvas widgets are rectangular, but only the polygon is drawn and
        # bound to clicks, so the button behaves like a triangle.
        points = self._triangle_points(direction)
        triangle = canvas.create_polygon(
            points,
            fill="#2f6fed",
            outline="#17439b",
            width=2,
            activefill="#17439b",
        )

        # Pressing a triangle reports which configured axis should move.
        canvas.tag_bind(
            triangle,
            "<ButtonPress-1>",
            lambda _event: self._handle_press(direction),
        )

    def _handle_press(self, direction: str) -> None:
        """Convert one pad direction into an axis and direction sign."""
        axis = (
            self._horizontal_axis
            if direction in {"left", "right"}
            else self._vertical_axis
        )
        sign: DirectionSign = "1" if direction in {"right", "down"} else "-1"
        if axis in REVERSED_BUTTON_AXES:
            sign = "-1" if sign == "1" else "1"
        self._on_press(axis, sign)

    @staticmethod
    def _triangle_points(direction: str) -> tuple[int, int, int, int, int, int]:
        """Return triangle polygon points for a direction."""
        if direction == "up":
            return (24, 6, 6, 42, 42, 42)
        if direction == "down":
            return (6, 6, 42, 6, 24, 42)
        if direction == "left":
            return (6, 24, 42, 6, 42, 42)
        return (6, 6, 42, 24, 6, 42)


class SplitCircleButton(tk.Canvas):
    """Two-part circular button for Z-axis up/down control."""

    def __init__(
        self,
        master: tk.Misc,
        on_press: Callable[[Axis, DirectionSign], None],
    ) -> None:
        """Create a split circular button.

        Args:
            master: Parent Tk widget.
            on_press: Callback receiving Z and a signed direction.
        """
        super().__init__(
            master,
            width=72,
            height=72,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            bg=master.cget("bg"),
        )
        self._on_press = on_press

        # The circle is split into two independent click regions. The top half
        # controls Z up and the bottom half controls Z down.
        top_half = self.create_arc(
            6,
            6,
            66,
            66,
            start=0,
            extent=180,
            fill="#2f6fed",
            outline="#17439b",
            width=2,
            style="pieslice",
            activefill="#17439b",
        )
        bottom_half = self.create_arc(
            6,
            6,
            66,
            66,
            start=180,
            extent=180,
            fill="#2f6fed",
            outline="#17439b",
            width=2,
            style="pieslice",
            activefill="#17439b",
        )
        self.create_line(6, 36, 66, 36, fill="#17439b", width=2)
        self.create_polygon(36, 18, 28, 30, 44, 30, fill="white")
        self.create_polygon(28, 42, 44, 42, 36, 54, fill="white")

        # Z is reversed so the upper half sends the negative sign and moves up.
        self.tag_bind(top_half, "<ButtonPress-1>", lambda _event: self._on_press("Z", "-1"))
        self.tag_bind(
            bottom_half,
            "<ButtonPress-1>",
            lambda _event: self._on_press("Z", "1"),
        )


class StageGui(tk.Tk):
    """Main window for stage-control GUI experiments."""

    def __init__(self) -> None:
        """Create the GUI window."""
        super().__init__()

        """================ Window / State Set ================"""
        self.title("Stage Control")
        self.geometry("760x540")
        self.configure(bg=WINDOW_BACKGROUND)

        self._stage_inst: Optional[MessageBasedResource] = None
        self._light_keithley: Optional[MessageBasedResource] = None
        self._probe_keithley: Optional[MessageBasedResource] = None
        self._active_axis: Optional[Axis] = None
        self._el_window: Optional[tk.Toplevel] = None
        self.el_app: Optional[LucamStreamApp] = None
        self._speed_slider_value = REFERENCE_SLIDER_SPEED
        self._main_running = False
        self._stop_requested = threading.Event()
        self._resume_allowed = threading.Event()
        self._resume_allowed.set()
        self.home_position: dict[str, str] = {axis: "0" for axis in AXES}
        self._home_is_set = False
        self.contact_z: Optional[str] = None
        self.die_config_csv_path = tk.StringVar()
        self.die_config: dict[str, tuple[str, str]] = {}
        self.light_current_ma = tk.StringVar()
        self.probe_current_ma = tk.StringVar()
        self._light_current_a: Optional[float] = None
        self._probe_current_magnitude_a: Optional[float] = None
        self._probe_polarity = 1
        self.light_output_button: Optional[tk.Button] = None
        self.probe_output_button: Optional[tk.Button] = None

        """================ Frame Set ================"""
        content = tk.Frame(self, bg=WINDOW_BACKGROUND)
        content.pack(fill="both", expand=True)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        # Stage controls live in their own section so additional GUI sections
        # can be added later without mixing layout code together.
        stage_section = tk.Frame(content, bg=WINDOW_BACKGROUND)
        stage_section.grid(row=0, column=0)

        pad_frame = tk.Frame(stage_section, bg=WINDOW_BACKGROUND)
        pad_frame.pack()

        setup_frame = tk.Frame(pad_frame, bg=WINDOW_BACKGROUND)
        setup_frame.grid(row=1, column=0, padx=12)

        """================ Button Set ================"""
        set_home_button = tk.Button(
            setup_frame,
            text="Set Home",
            width=12,
            height=2,
            command=self._set_home,
        )
        set_home_button.pack(pady=(0, 8))

        set_contact_button = tk.Button(
            setup_frame,
            text="Set Contact",
            width=12,
            height=2,
            command=self._set_contact,
        )
        set_contact_button.pack(pady=(0, 8))

        move_home_button = tk.Button(
            setup_frame,
            text="Move Home",
            width=12,
            height=2,
            command=self._move_to_home,
        )
        move_home_button.pack()

        open_el_button = tk.Button(
            setup_frame,
            text="Open EL Station",
            width=12,
            height=2,
            command=self._open_el_station,
        )
        open_el_button.pack(pady=(8, 0))

        start_button = tk.Button(
            pad_frame,
            text="Start",
            width=14,
            height=2,
            command=self._start_yelo_main,
        )
        start_button.grid(row=2, column=1, pady=(12, 0), padx=(0, 6))

        self.pause_button = tk.Button(
            pad_frame,
            text="Pause",
            width=14,
            height=2,
            command=self._toggle_pause,
        )
        self.pause_button.grid(row=2, column=2, pady=(12, 0), padx=6)

        stop_button = tk.Button(
            pad_frame,
            text="Stop",
            width=14,
            height=2,
            command=self._request_stop,
        )
        stop_button.grid(row=2, column=3, pady=(12, 0), padx=(6, 0))

        """================ Die Config CSV Set ================"""
        csv_frame = tk.Frame(pad_frame, bg=WINDOW_BACKGROUND)
        csv_frame.grid(row=3, column=0, columnspan=4, pady=(10, 0), sticky="ew")

        tk.Label(csv_frame, text="Die Config CSV:", bg=WINDOW_BACKGROUND).pack(side=tk.LEFT)
        tk.Entry(csv_frame, textvariable=self.die_config_csv_path, width=58).pack(side=tk.LEFT, padx=5)
        tk.Button(csv_frame, text="Browse", command=self._select_die_config_csv).pack(side=tk.LEFT)

        """================ Keithley Current Set ================"""
        keithley_frame = tk.Frame(pad_frame, bg=WINDOW_BACKGROUND)
        keithley_frame.grid(row=4, column=0, columnspan=4, pady=(10, 0))

        tk.Label(keithley_frame, text="Light current (mA):", bg=WINDOW_BACKGROUND).pack(side=tk.LEFT)
        tk.Entry(keithley_frame, textvariable=self.light_current_ma, width=9).pack(side=tk.LEFT, padx=5)
        tk.Button(
            keithley_frame,
            text="Apply Light",
            command=self._apply_light_current,
        ).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(keithley_frame, text="Probe current (mA):", bg=WINDOW_BACKGROUND).pack(side=tk.LEFT)
        tk.Entry(keithley_frame, textvariable=self.probe_current_ma, width=9).pack(side=tk.LEFT, padx=5)
        tk.Button(
            keithley_frame,
            text="Apply Probe",
            command=self._apply_probe_current,
        ).pack(side=tk.LEFT)

        output_frame = tk.Frame(pad_frame, bg=WINDOW_BACKGROUND)
        output_frame.grid(row=5, column=0, columnspan=4, pady=(8, 0))
        self.light_output_button = tk.Button(
            output_frame,
            text="Light Output: OFF",
            command=lambda: self._toggle_keithley_output("light"),
        )
        self.light_output_button.pack(side=tk.LEFT, padx=5)
        self.probe_output_button = tk.Button(
            output_frame,
            text="Probe Output: OFF",
            command=lambda: self._toggle_keithley_output("probe"),
        )
        self.probe_output_button.pack(side=tk.LEFT, padx=5)

        """================ Machine Movement Pad Set ================"""
        # Machine A pad: X/Y movement for the left stage.
        machine_a = TrianglePad(
            pad_frame,
            horizontal_axis="X",
            vertical_axis="Y",
            on_press=lambda axis, sign: self._start_jog(
                "Machine A",
                axis,
                sign,
            ),
        )
        machine_a.grid(row=1, column=1, padx=12)

        # Machine B pad: V/U movement for the right machine. The Z up/down
        # control is added into the center of this same pad below.
        machine_b = TrianglePad(
            pad_frame,
            horizontal_axis="V",
            vertical_axis="U",
            on_press=lambda axis, sign: self._start_jog(
                "Machine B",
                axis,
                sign,
            ),
        )
        machine_b.grid(row=1, column=2, padx=12)

        z_button = SplitCircleButton(
            machine_b,
            lambda axis, sign: self._start_jog("Machine B", axis, sign),
        )
        z_button.grid(row=1, column=1)

        # EL camera movement is handled by the EL Station window on axis W.

        """================ Speed Slider Set ================"""
        # Visual-only speed slider demo. It does not control motor speed yet.
        speed_demo = tk.Scale(
            pad_frame,
            from_=100,
            to=0,
            orient="vertical",
            length=PAD_HEIGHT,
            label="Speed",
            showvalue=True,
            bg=WINDOW_BACKGROUND,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            troughcolor=PAD_BACKGROUND,
            command=self._record_speed_demo,
        )
        speed_demo.set(50)
        speed_demo.grid(row=1, column=3, padx=12)

        """================ Monitor Window Set ================"""
        self.monitor = tk.Text(
            pad_frame,
            width=80,
            height=7,
            state="disabled",
            bg="white",
            wrap="word",
        )
        self.monitor.grid(row=0, column=0, columnspan=4, pady=(0, 12), sticky="ew")

        self.status = tk.StringVar(value="Ready")
        status_label = tk.Label(
            self,
            textvariable=self.status,
            anchor="w",
            bg="#e8e8e8",
            font=("Segoe UI", 10),
            padx=12,
            pady=8,
        )
        status_label.pack(fill="x", side="bottom")

        """================ Command Operation Set ================"""
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.bind_all("<ButtonRelease-1>", self._stop_jog)
        self._log(f"Initial home: {self.home_position}")
        try:
            self._get_stage_inst()
        except Exception as exc:
            self._log(f"Startup connection error: {exc}")
        self._initialize_keithleys()

    """================ Jogging Control Methods ================"""

    def _start_jog(
        self,
        machine: str,
        axis: Axis,
        sign: DirectionSign,
    ) -> None:
        """Start a large pulse move to approximate continuous jogging."""
        try:
            stage_inst = self._get_stage_inst()
            pulse = JOG_PULSE if sign == "1" else f"-{JOG_PULSE}"
            fast_speed, low_speed = self._jog_speed(axis)
            move(
                stage_inst,
                {axis: pulse},
                fast_speed=fast_speed,
                low_speed=low_speed,
                wait=False,
                read_position=False,
            )
        except Exception as exc:
            self._active_axis = None
            self._log(f"Jog start error: {exc}")
            return

        self._active_axis = axis
        self._log(f"{machine}: jogging {axis} {sign}, speed {self._speed_slider_value}")

    def _stop_jog(self, _event: Optional[tk.Event] = None) -> None:
        """Stop the active large-pulse jog when the mouse is released."""
        if self._stage_inst is None or self._active_axis is None:
            return

        try:
            self._stage_inst.write(f"AXI{self._active_axis}:STOP {STOP_MODE}")
        except Exception as exc:
            self._log(f"Jog stop error: {exc}")
            return

        self._log(f"Stopped {self._active_axis}")
        self._active_axis = None

    def _move_to_home(self) -> None:
        """Move all configured axes back to the recorded home position."""
        try:
            stage_inst = self._get_stage_inst()
            current_position = get_pos(stage_inst)
            pulse_to_home = {
                axis: str(int(self.home_position[axis]) - int(current_position[axis]))
                for axis in AXES
            }
            move(stage_inst, pulse_to_home, read_position=False)
        except Exception as exc:
            self._log(f"Move home error: {exc}")
            return

        self._log(f"Moving to home: {self.home_position}")

    def _request_stop(self) -> None:
        """Request the automated Yelo sequence to move home and return."""
        self._set_keithley_outputs_off()
        if not self._main_running:
            self._log("No main sequence is running")
            return

        self._stop_requested.set()
        self._resume_allowed.set()
        self.pause_button.config(text="Pause")
        self._log("Stop requested; sequence will move home at the next safe point")

    def _toggle_pause(self) -> None:
        """Pause or resume the automated Yelo sequence."""
        if not self._main_running:
            self._log("No main sequence is running")
            return

        if self._resume_allowed.is_set():
            self._resume_allowed.clear()
            self.pause_button.config(text="Resume")
            self._log("Pause requested")
        else:
            self._resume_allowed.set()
            self.pause_button.config(text="Pause")
            self._log("Main sequence resumed")

    def _jog_speed(self, axis: Axis) -> tuple[dict[str, str], dict[str, str]]:
        """Return axis speed dictionaries scaled from the slider value."""
        multiplier = max(
            self._speed_slider_value / REFERENCE_SLIDER_SPEED,
            MIN_SPEED_MULTIPLIER,
        )
        fast_speed = min(round(int(DEFAULT_FAST_SPEED[axis]) * multiplier), MAX_FAST_SPEED)
        low_speed = min(round(int(DEFAULT_LOW_SPEED[axis]) * multiplier), MAX_LOW_SPEED)
        return {axis: str(fast_speed)}, {axis: str(low_speed)}

    """================ Main Function Methods ================"""

    def _start_yelo_main(self) -> None:
        """Start the Yelo main sequence from the GUI."""
        if self._main_running:
            self._log("Main sequence already running")
            return
        if not self._home_is_set:
            self._log("Set Home before starting")
            return
        if self.contact_z is None:
            self._log("Set Contact before starting")
            return
        if self.el_app is None:
            self._log("Open EL Station before starting")
            return
        if self._light_keithley is None or self._probe_keithley is None:
            self._log("Connect both Keithleys before starting")
            return
        if self._light_current_a is None or self._probe_current_magnitude_a is None:
            self._log("Apply both Keithley current levels before starting")
            return

        try:
            self._set_keithley_outputs_off()
            set_keithley_current(self._light_keithley, self._light_current_a)
            set_keithley_current(self._probe_keithley, self._probe_current_magnitude_a)
            self._probe_polarity = 1
        except Exception as exc:
            self._log(f"Keithley preparation error: {exc}")
            return

        self._stop_requested.clear()
        self._resume_allowed.set()
        self.pause_button.config(text="Pause")
        self._main_running = True
        self.light_output_button.config(state=tk.DISABLED)
        self.probe_output_button.config(state=tk.DISABLED)
        if not self.die_config:
            self._log("Starting main sequence without die configuration CSV")
        self._log("Starting main sequence...")
        threading.Thread(target=self._run_yelo_main, daemon=True).start()

    def _run_yelo_main(self) -> None:
        """Run Yelo main in a background thread so Tk stays responsive."""
        try:
            stage_inst = self._get_stage_inst()
            current_z = get_pos(stage_inst, ["Z"])["Z"]
            home_z = self.home_position["Z"]
            move(
                stage_inst,
                {"Z": str(int(home_z) - int(current_z))},
                read_position=False,
            )
            run_yelo_main(
                stage=stage_inst,
                home_position=self.home_position,
                contact_z=self.contact_z,
                capture_el=self._capture_el_for_die,
                stop_requested=self._stop_requested.is_set,
                resume_allowed=self._resume_allowed,
            )
        except Exception as exc:
            self.after(0, lambda: self._finish_yelo_main(f"Main error: {exc}"))
            return

        self.after(0, lambda: self._finish_yelo_main("Main sequence finished"))

    def _finish_yelo_main(self, message: str) -> None:
        """Update GUI state after the Yelo main sequence exits."""
        self._set_keithley_outputs_off()
        self._main_running = False
        self.light_output_button.config(state=tk.NORMAL)
        self.probe_output_button.config(state=tk.NORMAL)
        self._resume_allowed.set()
        self.pause_button.config(text="Pause")
        self._log(message)

    """================ EL Station Methods ================"""

    def _capture_el_for_die(self, die: str) -> object:
        """Run EL capture on Tk's main thread and wait for it to finish."""
        done = threading.Event()
        result: dict[str, object] = {}

        def capture() -> None:
            try:
                if self.el_app is None:
                    raise RuntimeError("EL Station is not open")

                self._set_probe_polarity_for_die(die)

                die_info = self.die_config.get(die)
                if die_info is None:
                    self._log(f"No CSV configuration found for die {die}; using current EL fields")
                    result["value"] = self.el_app.take_el_snapshot(show_comparison=False)
                    return

                full_id, notes = die_info
                try:
                    design, lot, wafer, device_id = full_id.split("_")
                except ValueError:
                    self._log(f"Invalid Full ID for die {die}: {full_id}")
                    result["value"] = False
                    return

                device_id = self._format_device_id(device_id)
                self.el_app.set_image_info(design, lot, wafer, device_id)
                note_text = f", notes={notes}" if notes else ""
                self._log(f"Taking EL snapshot for die {die}: {full_id}{note_text}")
                result["value"] = self.el_app.take_el_snapshot(show_comparison=False)
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        self.after(0, capture)
        done.wait()

        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _open_el_station(self) -> None:
        """Open the EL station GUI in a second window."""
        if self._el_window is not None and self._el_window.winfo_exists():
            self._el_window.lift()
            self._el_window.focus_force()
            self._log("EL Station window already open")
            return

        try:
            self._el_window = tk.Toplevel(self)
            try:
                motor = self._get_stage_inst()
            except Exception as exc:
                motor = None
                self._log(f"EL Station opened without motor: {exc}")
            self.el_app = LucamStreamApp(
                self._el_window,
                motor=motor,
                stop_requested=self._stop_requested.is_set,
                resume_allowed=self._resume_allowed,
                light_keithley=self._light_keithley,
                probe_keithley=self._probe_keithley,
                output_state_changed=self._set_output_button_state,
            )
            self._el_window.protocol("WM_DELETE_WINDOW", self._on_el_station_close)
        except Exception as exc:
            self.el_app = None
            if self._el_window is not None and self._el_window.winfo_exists():
                self._el_window.destroy()
            self._el_window = None
            self._log(f"Open EL Station error: {exc}")
            return

        self._log("EL Station opened")

    def _on_el_station_close(self) -> None:
        """Close the EL station window and clear the stored app reference."""
        if self.el_app is not None:
            self.el_app.on_closing()
        elif self._el_window is not None and self._el_window.winfo_exists():
            self._el_window.destroy()

        self.el_app = None
        self._el_window = None
        self._log("EL Station closed")

    """================ Set Methods ================"""

    @staticmethod
    def _format_device_id(device_id: str) -> str:
        """Convert IDs like HG06FP to HGFP06 for image filenames."""
        if len(device_id) == 6 and device_id[:2].isalpha() and device_id[2:4].isdigit():
            return f"{device_id[:2]}{device_id[4:]}{device_id[2:4]}"
        return device_id

    def _select_die_config_csv(self) -> None:
        """Select and load the die configuration CSV for the Yelo workflow."""
        csv_path = filedialog.askopenfilename(
            title="Select Die Configuration CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not csv_path:
            return

        try:
            self.die_config = self._read_die_config_csv(csv_path)
        except Exception as exc:
            self._log(f"Load die config CSV error: {exc}")
            return

        self.die_config_csv_path.set(csv_path)
        self._log(f"Loaded die config CSV with {len(self.die_config)} die entries")

    def _read_die_config_csv(self, csv_path: str) -> dict[str, tuple[str, str]]:
        """Read die configuration rows as {die: (full_id, notes)}."""
        df = pd.read_csv(
            csv_path,
            header=DIE_CONFIG_HEADER_ROW,
            nrows=DIE_CONFIG_DATA_ROWS,
            dtype=str,
        ).fillna("")

        left = {
            str(int(float(row["Spot"]))): (
                row["Full ID"].strip(),
                row["Notes"].strip(),
            )
            for _, row in df.iterrows()
            if row["Spot"].strip() and row["Full ID"].strip()
        }
        right = {
            str(int(float(row["Spot.1"]))): (
                row["Full ID.1"].strip(),
                row["Unnamed: 5"].strip(),
            )
            for _, row in df.iterrows()
            if row["Spot.1"].strip() and row["Full ID.1"].strip()
        }

        die_config = {**left, **right}
        if not die_config:
            raise ValueError("no die entries found")

        return die_config

    def _record_speed_demo(self, value: str) -> None:
        """Record the jog speed slider value."""
        self._speed_slider_value = int(value)
        self._log(f"Speed: {value}")

    def _set_home(self) -> None:
        """Store the current stage location as the workflow home position."""
        try:
            self.home_position = get_pos(self._get_stage_inst())
        except Exception as exc:
            self._log(f"Set home error: {exc}")
            return

        self._home_is_set = True
        self.contact_z = None
        self._log(f"Home set: {self.home_position}")

    def _set_contact(self) -> None:
        """Store the current Z location as the contact position."""
        if not self._home_is_set:
            self._log("Set Home before Set Contact")
            return

        try:
            self.contact_z = get_pos(self._get_stage_inst(), ["Z"])["Z"]
        except Exception as exc:
            self._log(f"Set contact error: {exc}")
            return

        self._log(f"Contact Z set: {self.contact_z}")

    def _get_stage_inst(self) -> MessageBasedResource:
        """Return the connected stage instrument, opening it on first use."""
        if self._stage_inst is None:
            self._log("Connecting to stage...")
            self.update_idletasks()
            self._stage_inst = set_res_gpib(GPIB_ADDRESS, MOTOR_GPIB_BUS)
            set_all_axes_speed_table(self._stage_inst)
        return self._stage_inst

    def _initialize_keithleys(self) -> None:
        """Connect and safely configure both Keithley current sources."""
        try:
            light_keithley = set_res_gpib(
                LIGHT_KEITHLEY_ADDRESS,
                bus=KEITHLEY_GPIB_BUS,
            )
            setup_keithley(
                light_keithley,
                LIGHT_COMPLIANCE_V,
                current_range=KEITHLEY_CURRENT_RANGE_A,
            )
            self._light_keithley = light_keithley
            self._log("Light Keithley connected; output is off")
        except Exception as exc:
            self._light_keithley = None
            self._log(f"Light Keithley connection error: {exc}")

        if not PROBE_KEITHLEY_ADDRESS:
            self._log("Probe Keithley address is not configured")
            return

        try:
            probe_keithley = set_res_gpib(
                PROBE_KEITHLEY_ADDRESS,
                bus=KEITHLEY_GPIB_BUS,
            )
            setup_keithley(
                probe_keithley,
                PROBE_COMPLIANCE_V,
                current_range=KEITHLEY_CURRENT_RANGE_A,
            )
            self._probe_keithley = probe_keithley
            self._log("Probe Keithley connected; output is off")
        except Exception as exc:
            self._probe_keithley = None
            self._log(f"Probe Keithley connection error: {exc}")

    def _apply_light_current(self) -> None:
        """Apply only the user-entered light current level."""
        self._apply_keithley_current("light")

    def _apply_probe_current(self) -> None:
        """Apply only the user-entered probe current level."""
        self._apply_keithley_current("probe")

    def _apply_keithley_current(self, device: str) -> None:
        """Validate and apply one current level without enabling its output."""
        if self._main_running or (
            self.el_app is not None and self.el_app.capture_in_progress
        ):
            self._log("Current levels cannot be changed during a capture sequence")
            return

        if device == "light":
            instrument = self._light_keithley
            current_text = self.light_current_ma.get()
            display_name = "Light"
        elif device == "probe":
            instrument = self._probe_keithley
            current_text = self.probe_current_ma.get()
            display_name = "Probe"
        else:
            raise ValueError(f"Unknown Keithley device: {device}")

        if instrument is None:
            self._log(f"{display_name} Keithley is not connected")
            return

        try:
            current_a = float(current_text) / 1000.0
            if current_a <= 0:
                raise ValueError("Current level must be greater than 0 mA")

            set_keithley_output(instrument, False)
            self._set_output_button_state(device, False)
            set_keithley_current(instrument, current_a)
        except (TypeError, ValueError, RuntimeError) as exc:
            self._log(f"Invalid {display_name.lower()} current: {exc}")
            return
        except Exception as exc:
            self._log(f"Set {display_name.lower()} current error: {exc}")
            return

        if device == "light":
            self._light_current_a = current_a
        else:
            self._probe_current_magnitude_a = current_a
            self._probe_polarity = 1

        self._log(f"{display_name} current set to {current_a * 1000:g} mA; output remains off")

    def _toggle_keithley_output(self, device: str) -> None:
        """Toggle one Keithley output for manual hardware control."""
        if self._main_running or (
            self.el_app is not None and self.el_app.capture_in_progress
        ):
            self._log("Manual output control is disabled during capture")
            return

        if device == "light":
            instrument = self._light_keithley
            current_is_set = self._light_current_a is not None
            display_name = "Light"
        elif device == "probe":
            instrument = self._probe_keithley
            current_is_set = self._probe_current_magnitude_a is not None
            display_name = "Probe"
        else:
            raise ValueError(f"Unknown Keithley device: {device}")

        if instrument is None:
            self._log(f"{display_name} Keithley is not connected")
            return
        if not current_is_set:
            self._log(f"Apply the {display_name.lower()} current before enabling output")
            return

        try:
            output_is_on = int(float(instrument.query(":OUTP?").strip())) != 0
            set_keithley_output(instrument, not output_is_on)
        except Exception as exc:
            self._log(f"{display_name} output control error: {exc}")
            return

        self._set_output_button_state(device, not output_is_on)
        state_text = "ON" if not output_is_on else "OFF"
        self._log(f"{display_name} output turned {state_text}")

    def _set_output_button_state(self, device: str, enabled: bool) -> None:
        """Update one manual-output button label."""
        button = (
            self.light_output_button if device == "light" else self.probe_output_button
        )
        if button is not None:
            name = "Light" if device == "light" else "Probe"
            button.config(text=f"{name} Output: {'ON' if enabled else 'OFF'}")

    def _set_probe_polarity_for_die(self, die: str) -> None:
        """Use positive probe current on row one and negative on row two."""
        if self._probe_keithley is None or self._probe_current_magnitude_a is None:
            raise RuntimeError("Probe Keithley current is not configured")

        requested_polarity = 1 if int(die) % 2 == 1 else -1
        if requested_polarity == self._probe_polarity:
            return

        set_keithley_output(self._probe_keithley, False)
        set_keithley_current(
            self._probe_keithley,
            requested_polarity * self._probe_current_magnitude_a,
        )
        self._probe_polarity = requested_polarity
        self._log("Probe current polarity reversed for the second row")

    def _set_keithley_outputs_off(self) -> None:
        """Best-effort immediate output shutdown for both current sources."""
        errors = []
        for name, instrument in (
            ("light", self._light_keithley),
            ("probe", self._probe_keithley),
        ):
            if instrument is None:
                continue
            try:
                set_keithley_output(instrument, False)
                self._set_output_button_state(name, False)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if errors:
            self._log(f"Keithley output-off error: {'; '.join(errors)}")

    def _log(self, message: str) -> None:
        """Show the latest message and append it to the monitor window."""
        self.status.set(message)
        self.monitor.configure(state="normal")
        self.monitor.insert("end", f"{message}\n")
        self.monitor.see("end")
        self.monitor.configure(state="disabled")

    def _on_closing(self) -> None:
        """Clean up open hardware windows and stage connection before exit."""
        if self._el_window is not None and self._el_window.winfo_exists():
            self._on_el_station_close()

        for name, instrument in (
            ("light", self._light_keithley),
            ("probe", self._probe_keithley),
        ):
            if instrument is None:
                continue
            try:
                close_keithley(instrument)
            except Exception as exc:
                self._log(f"{name.title()} Keithley close error: {exc}")

        if self._stage_inst is not None:
            try:
                self._stage_inst.close()
            except Exception as exc:
                self._log(f"Stage close error: {exc}")

        self.destroy()


def main() -> None:
    """Run the stage-control GUI."""
    app = StageGui()
    app.mainloop()


if __name__ == "__main__":
    main()
