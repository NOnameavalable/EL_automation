import tkinter as tk
from lucam import Lucam, LucamNumCameras, LucamError, API
from tkinter import messagebox, filedialog
import os
from collections.abc import Callable
from threading import Event
from typing import Optional
import matplotlib.pyplot as plt
import numpy as np
import ctypes
from ctypes import wintypes
import cv2
import imutils
import time
from PIL import Image
from pyvisa.resources import MessageBasedResource
from Keithley2400 import set_keithley_output
from SSD220 import (
    convert_axis_pulse,
    get_pos,
    move_with_control,
    set_res_gpib,
)

EL_CAMERA_AXIS = "W"
LUCAM_CHILD_WINDOW_STYLE = 0x56000000  # WS_CHILD | WS_VISIBLE | clipping styles
FOCUS_ROI_BOX_SIZE = 30
FOCUS_ROI_OFFSET = 100
MIN_FOCUS_STEP = 50
MAX_FOCUS_STEP = 1000
MAX_FOCUS_ATTEMPTS = 10
MAX_FOCUS_REFINEMENTS = 6
FOCUS_SCORE_THRESHOLD_RATIO = 0.05

WNDENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM,
)


class LucamStreamApp:
    # ==================== Initialization ====================

    def __init__(
        self,
        master,
        motor=None,
        stop_requested: Optional[Callable[[], bool]] = None,
        resume_allowed: Optional[Event] = None,
        light_keithley: Optional[MessageBasedResource] = None,
        probe_keithley: Optional[MessageBasedResource] = None,
        output_state_changed: Optional[Callable[[str, bool], None]] = None,
    ):
        self.master = master
        master.title("Camera and Motor Control")
        
        # Camera setup
        self.lucam = None
        self.streaming = False
        self.display_window_created = False
        self.preview_window = None
        self.preview_frame = None
        self.lucam_frame = None
        self.focus_overlay_window = None
        self.focus_overlay_canvas = None
        self.focus_overlay_toolbar = None
        self.focus_overlay_toolbar_window = None
        self.focus_roi_center = None
        self.focus_roi_box_ids = {}
        self.preview_resize_in_progress = False
        self.capture_in_progress = False
        self.stream_duration = 0
        self.max_stream_time = 36001  # 10 hours of run-time and then it closes camera
        
        # Stream display settings
        self.frame_width = 0
        self.frame_height = 0
        self.frame_aspect = 1.0
        self.window_width = 0
        self.window_height = 0
        
        self.motor = motor
        self.stop_requested = stop_requested
        self.resume_allowed = resume_allowed
        self.light_keithley = light_keithley
        self.probe_keithley = probe_keithley
        self.output_state_changed = output_state_changed

        # Tk variables
        self.exposure_var = tk.DoubleVar(value=30)
        self.dir_path = tk.StringVar(value=os.getcwd())
        self.info_fields = ['DESIGN', 'LOT', 'WAFER', 'ID']
        self.info_vars = {field: tk.StringVar() for field in self.info_fields}
        self.steps_var = tk.StringVar(value="10000")
        self.fine_steps_var = tk.StringVar(value="1000")
        self.focus_score_var = tk.StringVar(value="Focus Score: 0")
        self.focus_roi_scale_var = tk.DoubleVar(value=1.0)

        # Widgets assigned during UI setup
        self.start_button = None
        self.stop_button = None
        self.snapshot_button = None
        self.snap_el_button = None
        self.status_label = None
        
        # Create UI
        self.setup_ui()
        
        # Initialize camera
        self.init_camera()
        
        # Bind window resize event
        self.master.bind("<Configure>", self.on_window_resize)

    # ==================== UI Setup ====================

    def setup_ui(self):
        # Exposure controls
        exposure_frame = tk.Frame(self.master)
        exposure_frame.pack(pady=5)
        
        tk.Label(exposure_frame, text="Exposure (ms):").pack(side=tk.LEFT)
        exposure_entry = tk.Entry(exposure_frame, textvariable=self.exposure_var, width=10)
        exposure_entry.pack(side=tk.LEFT, padx=5)
        tk.Button(exposure_frame, text="Set Exposure", command=self.set_exposure).pack(side=tk.LEFT)

        # Stream resize controls
        resize_frame = tk.Frame(self.master)
        resize_frame.pack(pady=5)
        
        tk.Label(resize_frame, text="Stream Size:").pack(side=tk.LEFT)
        
        # Size buttons
        tk.Button(resize_frame, text="25%", command=lambda: self.resize_preview_window(int(self.frame_width * 0.25))).pack(side=tk.LEFT, padx=5)
        tk.Button(resize_frame, text="50%", command=lambda: self.resize_preview_window(int(self.frame_width * 0.5))).pack(side=tk.LEFT, padx=5)
        tk.Button(resize_frame, text="75%", command=lambda: self.resize_preview_window(int(self.frame_width * 0.75))).pack(side=tk.LEFT, padx=5)
        tk.Button(resize_frame, text="100%", command=lambda: self.resize_preview_window(self.frame_width)).pack(side=tk.LEFT, padx=5)

        # Save directory controls
        dir_frame = tk.Frame(self.master)
        dir_frame.pack(pady=5)
        
        tk.Label(dir_frame, text="Save Directory:").pack(side=tk.LEFT)
        tk.Entry(dir_frame, textvariable=self.dir_path, width=40).pack(side=tk.LEFT, padx=5)
        tk.Button(dir_frame, text="Browse", command=self.select_directory).pack(side=tk.LEFT)

        # Image info fields
        info_frame = tk.Frame(self.master)
        info_frame.pack(pady=5)
        
        for label in self.info_fields:
            row = tk.Frame(info_frame)
            row.pack(pady=2)
            tk.Label(row, text=f"{label}:").pack(side=tk.LEFT)
            tk.Entry(row, textvariable=self.info_vars[label], width=20).pack(side=tk.LEFT)

        # Streaming controls
        streaming_frame = tk.Frame(self.master)
        streaming_frame.pack(pady=10)
        
        self.start_button = tk.Button(streaming_frame, text="Open Camera View", command=self.start_streaming)
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = tk.Button(streaming_frame, text="Close Camera View", command=self.stop_streaming, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        # Snapshot controls
        snapshot_frame = tk.Frame(self.master)
        snapshot_frame.pack(pady=10)
        
        self.snapshot_button = tk.Button(snapshot_frame, text="Take Snapshot", command=self.take_snapshot, state=tk.DISABLED)
        self.snapshot_button.pack(side=tk.LEFT, padx=5)
        
        self.snap_el_button = tk.Button(
            snapshot_frame,
            text="Take EL Snapshot",
            command=lambda: self.take_el_snapshot(show_comparison=True),
            state=tk.DISABLED,
        )
        self.snap_el_button.pack(side=tk.LEFT, padx=5)

        # Status display
        self.status_label = tk.Label(self.master, text="Camera not initialized", fg="red")
        self.status_label.pack(pady=10)

        # Fine motor controls
        motor_frame = tk.Frame(self.master)
        motor_frame.pack(pady=10)
        
        # Step size controls frame
        step_size_frame = tk.Frame(motor_frame)
        step_size_frame.pack(pady=5)
        
        # Regular step size
        tk.Label(step_size_frame, text="Regular Steps:").grid(row=0, column=0, padx=5, sticky=tk.W)
        tk.Entry(step_size_frame, textvariable=self.steps_var, width=10).grid(row=0, column=1, padx=5)
        
        # Fine step size
        tk.Label(step_size_frame, text="Fine Steps:").grid(row=1, column=0, padx=5, sticky=tk.W)
        tk.Entry(step_size_frame, textvariable=self.fine_steps_var, width=10).grid(row=1, column=1, padx=5)
        
        # Movement buttons frame
        move_buttons_frame = tk.Frame(motor_frame)
        move_buttons_frame.pack(pady=5)
        
        tk.Button(move_buttons_frame, text="Move Up", command=self.move_up).grid(row=0, column=0, padx=5, pady=5)
        tk.Button(move_buttons_frame, text="Move Up Fine", command=self.move_up_fine).grid(row=0, column=1, padx=5, pady=5)
        tk.Button(move_buttons_frame, text="Move Down Fine", command=self.move_down_fine).grid(row=1, column=1, padx=5, pady=5)
        tk.Button(move_buttons_frame, text="Move Down", command=self.move_down).grid(row=1, column=0, padx=5, pady=5)
        
        tk.Button(motor_frame, text="Find Focus", command=self.open_focus_overlay).pack(pady=5)
        
        # Focus score display
        tk.Label(motor_frame, textvariable=self.focus_score_var).pack(pady=5)

    # ==================== Motor Movement ====================

    def move_up(self):
        if not self.motor:
            messagebox.showerror("Error", "Motor not initialized")
            return
        try:
            steps = self.steps_var.get()
            if not self._move_motor(str(int(steps))):
                self.update_status("Move up cancelled", "red")
                return
            self.update_status("Moved up successfully", "green")
        except Exception as e:
            self.update_status(f"Move up error: {e}", "red")
    
    def move_down(self):
        if not self.motor:
            messagebox.showerror("Error", "Motor not initialized")
            return
        try:
            steps = self.steps_var.get()
            if not self._move_motor(str(-int(steps))):
                self.update_status("Move down cancelled", "red")
                return
            self.update_status("Moved down successfully", "green")
        except Exception as e:
            self.update_status(f"Move down error: {e}", "red")
            
    def move_up_fine(self):
        if not self.motor:
            messagebox.showerror("Error", "Motor not initialized")
            return
        try:
            steps = self.fine_steps_var.get()
            if not self._move_motor(str(int(steps))):
                self.update_status("Move up fine cancelled", "red")
                return
            self.update_status("Moved up fine successfully", "green")
        except Exception as e:
            self.update_status(f"Move up fine error: {e}", "red")

    def move_down_fine(self):
        if not self.motor:
            messagebox.showerror("Error", "Motor not initialized")
            return
        try:
            steps = self.fine_steps_var.get()
            if not self._move_motor(str(-int(steps))):
                self.update_status("Move down fine cancelled", "red")
                return
            self.update_status("Moved down fine successfully", "green")
        except Exception as e:
            self.update_status(f"Move down fine error: {e}", "red")

    def _move_motor(self, pulse: str) -> bool:
        """Move the EL camera axis while honoring shared stop/pause controls."""
        if self.motor is None:
            return False
        return move_with_control(
            self.motor,
            {EL_CAMERA_AXIS: pulse},
            stop_requested=self.stop_requested,
            resume_allowed=self.resume_allowed,
            poll_callback=self.master.update,
        )

    # ==================== Autofocus ====================

    def find_focus_adaptive(self) -> bool:
        if not self.motor or not self.lucam:
            messagebox.showerror("Error", "Motor or camera not initialized")
            return False

        try:
            center_position = int(float(get_pos(self.motor, [EL_CAMERA_AXIS])[EL_CAMERA_AXIS]))
            snapshot = self.lucam.TakeSnapshot()
            center_score = self.calculate_focus(snapshot)
            step_size = MAX_FOCUS_STEP
            self.focus_score_var.set(f"Focus Score: {center_score:.2f}")
            self.update_status(
                f"Adaptive focus start. Position: {center_position}, Score: {center_score:.2f}",
                "black",
            )
            self.master.update()

            def move_to_focus_position(target_position):
                current_position = int(float(get_pos(self.motor, [EL_CAMERA_AXIS])[EL_CAMERA_AXIS]))
                controller_delta = int(target_position - current_position)
                if controller_delta == 0:
                    return True
                logical_pulse = convert_axis_pulse(EL_CAMERA_AXIS, controller_delta)
                if not self._move_motor(logical_pulse):
                    self.update_status("Adaptive focus cancelled", "red")
                    return False
                return True

            def score_at_position(position):
                if not move_to_focus_position(position):
                    return None
                time.sleep(0.3)
                snapshot = self.lucam.TakeSnapshot()
                return self.calculate_focus(snapshot)

            def refine_focus(center_position, center_score, step_size, attempt, refinements):
                if attempt >= MAX_FOCUS_ATTEMPTS or refinements >= MAX_FOCUS_REFINEMENTS:
                    return center_position, center_score

                attempt += 1

                scan_positions = [
                    center_position - (2 * step_size),
                    center_position - step_size,
                    center_position,
                    center_position + step_size,
                    center_position + (2 * step_size),
                ]
                measured_points = []

                for target_position in scan_positions:
                    current_score = score_at_position(target_position)
                    if current_score is None:
                        return

                    measured_points.append((int(target_position), current_score))
                    self.focus_score_var.set(f"Focus Score: {current_score:.2f}")
                    self.update_status(
                        f"Focus scan {attempt}/{MAX_FOCUS_ATTEMPTS}: "
                        f"Step: {step_size}, Position: {target_position}, "
                        f"Score: {current_score:.2f}",
                        "black",
                    )
                    self.master.update()

                measured_best_position, measured_best_score = max(
                    measured_points,
                    key=lambda point: point[1],
                )
                positions = np.array([point[0] for point in measured_points], dtype=float)
                scores = np.array([point[1] for point in measured_points], dtype=float)
                window_min = min(scan_positions)
                window_max = max(scan_positions)
                candidate_position = measured_best_position
                peak_near_edge = measured_best_position in (window_min, window_max)
                fitted_candidate = False

                try:
                    a, b, c = np.polyfit(positions, scores, 2)
                    if a < 0:
                        fitted_peak = -b / (2 * a)
                        fitted_score = (a * fitted_peak * fitted_peak) + (b * fitted_peak) + c
                        fit_inside_scan = window_min <= fitted_peak <= window_max
                        peak_near_edge = (
                            peak_near_edge
                            or fitted_peak <= window_min + (step_size / 2)
                            or fitted_peak >= window_max - (step_size / 2)
                        )
                        # Trust the fitted peak only when it is a real in-window maximum
                        # and not sitting near the edge of the sampled scan range.
                        if fit_inside_scan and not peak_near_edge:
                            candidate_position = int(round(fitted_peak))
                            fitted_candidate = True
                except Exception:
                    pass

                # Fitted peaks must be verified with a real image. If no fit is
                # trusted, use the best score already measured during the scan.
                if fitted_candidate:
                    candidate_score = score_at_position(candidate_position)
                    if candidate_score is None:
                        return
                    candidate_reason = f"fitted peak ({fitted_score:.2f})"
                else:
                    candidate_score = measured_best_score
                    candidate_reason = "measured best"

                self.focus_score_var.set(f"Focus Score: {candidate_score:.2f}")
                score_delta = candidate_score - center_score
                score_threshold = abs(center_score) * FOCUS_SCORE_THRESHOLD_RATIO
                self.update_status(
                    f"Focus attempt {attempt}: {candidate_reason}, "
                    f"Step: {step_size}, Position: {candidate_position}, "
                    f"Score: {candidate_score:.2f}, "
                    f"Delta: {score_delta:.2f}, Threshold: {score_threshold:.2f}",
                    "black",
                )
                self.master.update()

                # The threshold is an uncertainty range: changes inside it are
                # treated as insignificant in either direction.
                if abs(score_delta) < score_threshold:
                    return center_position, center_score

                next_step = MAX_FOCUS_STEP if peak_near_edge else max(MIN_FOCUS_STEP, step_size // 2)
                if score_delta > 0:
                    next_refinements = refinements if peak_near_edge else refinements + 1
                    return refine_focus(
                        candidate_position,
                        candidate_score,
                        next_step,
                        attempt,
                        next_refinements,
                    )

                # A significantly worse candidate may be noise. Compare median
                # scores using the original score plus two fresh scores at each
                # position, then recurse from the median winner.
                candidate_scores = [candidate_score]
                for _ in range(2):
                    score = score_at_position(candidate_position)
                    if score is None:
                        return
                    candidate_scores.append(score)

                center_scores = [center_score]
                for _ in range(2):
                    score = score_at_position(center_position)
                    if score is None:
                        return
                    center_scores.append(score)

                candidate_median = float(np.median(candidate_scores))
                center_median = float(np.median(center_scores))
                if candidate_median > center_median:
                    chosen_position = candidate_position
                    chosen_score = candidate_median
                    chosen_refinements = refinements if peak_near_edge else refinements + 1
                else:
                    chosen_position = center_position
                    chosen_score = center_median
                    chosen_refinements = refinements

                self.focus_score_var.set(f"Focus Score: {chosen_score:.2f}")
                self.update_status(
                    f"Focus median check: Position: {chosen_position}, "
                    f"Score: {chosen_score:.2f}, Next step: {next_step}",
                    "black",
                )
                self.master.update()
                return refine_focus(
                    chosen_position,
                    chosen_score,
                    next_step,
                    attempt,
                    chosen_refinements,
                )

            focus_result = refine_focus(
                center_position,
                center_score,
                step_size,
                0,
                0,
            )
            if focus_result is None:
                return False
            best_position, best_score = focus_result

            if not move_to_focus_position(best_position):
                return False

            self.focus_score_var.set(f"Focus Score: {best_score:.2f}")
            self.update_status(
                f"Adaptive focus complete. Position: {best_position}, Score: {best_score:.2f}",
                "green",
            )
            self.master.update()
            return True
        except Exception as e:
            self.update_status(f"Focus finding error: {e}", "red")
            self.master.update()
            return False

    def open_focus_overlay(self):
        """Open a transparent overlay aligned to the visible Lucam image."""
        if not self.streaming or self.lucam_frame is None:
            messagebox.showerror("Error", "Open Camera View before selecting focus")
            return

        geometry = self._current_lucam_frame_geometry()
        if geometry is None:
            self.master.after(50, self.open_focus_overlay)
            return
        width, height, x, y = geometry

        if self.focus_overlay_window is None:
            overlay = tk.Toplevel(self.preview_window)
            overlay.withdraw()
            overlay.overrideredirect(True)
            overlay.transient(self.preview_window)
            overlay.attributes("-topmost", True)
            try:
                overlay.attributes("-transparentcolor", "magenta")
            except tk.TclError:
                pass

            canvas = tk.Canvas(
                overlay,
                bg="magenta",
                highlightthickness=0,
                bd=0,
            )
            canvas.pack(fill=tk.BOTH, expand=True)
            toolbar = tk.Frame(canvas, bg="SystemButtonFace")
            toolbar_window = canvas.create_window(0, 0, anchor=tk.NW, window=toolbar)

            left_controls = tk.Frame(toolbar, bg="SystemButtonFace")
            left_controls.pack(side=tk.LEFT, padx=4, pady=4)
            tk.Label(
                left_controls,
                textvariable=self.focus_score_var,
                relief=tk.SUNKEN,
                anchor=tk.W,
                width=18,
            ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Button(
                left_controls,
                text="Get Score",
                command=self.update_current_focus_score,
            ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Button(
                left_controls,
                text="Find Focus",
                command=self.find_focus_adaptive,
            ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(left_controls, text="Scale:").pack(side=tk.LEFT)
            scale_entry = tk.Entry(left_controls, textvariable=self.focus_roi_scale_var, width=5)
            scale_entry.pack(side=tk.LEFT, padx=(2, 2))
            scale_entry.bind("<Return>", lambda _event: self.apply_focus_roi_scale())
            tk.Button(
                left_controls,
                text="Apply",
                command=self.apply_focus_roi_scale,
            ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Button(
                left_controls,
                text="Up",
                command=lambda: self.move_focus_roi(0, -self._focus_roi_box_size()),
            ).pack(side=tk.LEFT, padx=(0, 2))
            tk.Button(
                left_controls,
                text="Down",
                command=lambda: self.move_focus_roi(0, self._focus_roi_box_size()),
            ).pack(side=tk.LEFT, padx=(0, 2))
            tk.Button(
                left_controls,
                text="Left",
                command=lambda: self.move_focus_roi(-self._focus_roi_box_size(), 0),
            ).pack(side=tk.LEFT, padx=(0, 2))
            tk.Button(
                left_controls,
                text="Right",
                command=lambda: self.move_focus_roi(self._focus_roi_box_size(), 0),
            ).pack(side=tk.LEFT)
            tk.Button(toolbar, text="Close", command=self.close_focus_overlay).pack(
                side=tk.RIGHT,
                padx=4,
                pady=4,
            )
            overlay.bind("<Escape>", lambda _event: self.close_focus_overlay())
            overlay.protocol("WM_DELETE_WINDOW", self.close_focus_overlay)
            self.focus_overlay_window = overlay
            self.focus_overlay_canvas = canvas
            self.focus_overlay_toolbar = toolbar
            self.focus_overlay_toolbar_window = toolbar_window

        self._position_focus_overlay(width, height, x, y)
        self.focus_overlay_window.deiconify()
        self.focus_overlay_window.lift()

    def _current_lucam_frame_geometry(self):
        if self.lucam_frame is None:
            return None

        self.lucam_frame.update_idletasks()
        width = self.lucam_frame.winfo_width()
        height = self.lucam_frame.winfo_height()
        x = self.lucam_frame.winfo_rootx()
        y = self.lucam_frame.winfo_rooty()
        if width <= 1 or height <= 1 or x <= 0 or y <= 0:
            return None
        return width, height, x, y

    def close_focus_overlay(self):
        if self.focus_overlay_window is not None:
            try:
                self.focus_overlay_window.destroy()
            except Exception:
                pass
        self.focus_overlay_window = None
        self.focus_overlay_canvas = None
        self.focus_overlay_toolbar = None
        self.focus_overlay_toolbar_window = None
        self.focus_roi_center = None
        self.focus_roi_box_ids = {}

    def _position_focus_overlay(self, width=None, height=None, x=None, y=None):
        if self.focus_overlay_window is None or self.focus_overlay_canvas is None:
            return
        if width is None or height is None or x is None or y is None:
            geometry = self._current_lucam_frame_geometry()
            if geometry is None:
                self.close_focus_overlay()
                return
            width, height, x, y = geometry

        self.focus_overlay_window.geometry(f"{width}x{height}+{x}+{y}")
        self.focus_overlay_canvas.config(width=width, height=height)
        self.focus_overlay_canvas.delete("roi")
        self.focus_roi_box_ids = {}

        if self.focus_roi_center is None:
            center_x = width // 2
            center_y = height // 2
        else:
            center_x, center_y = self.focus_roi_center
            center_x = min(max(center_x, 0), width)
            center_y = min(max(center_y, 0), height)
        self.focus_roi_center = (center_x, center_y)

        rect_offsets = (
            ("center", 0, 0),
            ("top", 0, -self._focus_roi_offset()),
            ("left", -self._focus_roi_offset(), 0),
            ("right", self._focus_roi_offset(), 0),
            ("bottom", 0, self._focus_roi_offset()),
        )
        if self.focus_overlay_toolbar is not None:
            self.focus_overlay_toolbar.config(width=width)
            if self.focus_overlay_toolbar_window is not None:
                self.focus_overlay_canvas.delete(self.focus_overlay_toolbar_window)
            self.focus_overlay_toolbar_window = self.focus_overlay_canvas.create_window(
                0,
                0,
                anchor=tk.NW,
                window=self.focus_overlay_toolbar,
                width=width,
            )
        for box_name, offset_x, offset_y in rect_offsets:
            rect_center_x = center_x + offset_x
            rect_center_y = center_y + offset_y
            box_size = self._focus_roi_box_size()
            x1 = rect_center_x - box_size / 2
            y1 = rect_center_y - box_size / 2
            x2 = x1 + box_size
            y2 = y1 + box_size
            self.focus_roi_box_ids[box_name] = self.focus_overlay_canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                outline="black",
                width=3,
                tags=("roi", box_name),
            )

    def _focus_roi_scale(self):
        try:
            return max(0.1, float(self.focus_roi_scale_var.get()))
        except (tk.TclError, ValueError):
            return 1.0

    def _focus_roi_box_size(self):
        return max(1, int(round(FOCUS_ROI_BOX_SIZE * self._focus_roi_scale())))

    def _focus_roi_offset(self):
        return max(1, int(round(FOCUS_ROI_OFFSET * self._focus_roi_scale())))

    def apply_focus_roi_scale(self):
        scale = self._focus_roi_scale()
        self.focus_roi_scale_var.set(scale)
        self._position_focus_overlay()

    def move_focus_roi(self, dx, dy):
        if self.focus_overlay_canvas is None or not self.focus_roi_box_ids:
            return
        self.focus_overlay_canvas.move("roi", dx, dy)
        center_coords = self.focus_overlay_canvas.coords(self.focus_roi_box_ids["center"])
        if len(center_coords) == 4:
            x1, y1, x2, y2 = center_coords
            self.focus_roi_center = ((x1 + x2) / 2, (y1 + y2) / 2)

    def update_current_focus_score(self):
        try:
            self.get_current_focus_score()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            self.focus_score_var.set("Focus Score: --")
            self.update_status(f"Focus score error: {exc}", "red")

    def get_current_focus_score(self) -> float:
        if not self.streaming or self.lucam is None:
            raise RuntimeError("Open Camera View before getting focus score")

        snapshot = self.lucam.TakeSnapshot()
        current_score = self.calculate_focus(snapshot)
        self.focus_score_var.set(f"Focus Score: {current_score:.2f}")
        return current_score

    def _focus_roi_center_from_box(self):
        if self.focus_overlay_canvas is None or "center" not in self.focus_roi_box_ids:
            raise RuntimeError("Focus ROI center box is not available")
        center_coords = self.focus_overlay_canvas.coords(self.focus_roi_box_ids["center"])
        if len(center_coords) != 4:
            raise RuntimeError("Focus ROI center box coordinates are not available")
        x1, y1, x2, y2 = center_coords
        return (x1 + x2) / 2, (y1 + y2) / 2

    def _focus_roi_display_bounds(self, center_x, center_y):
        half_box = self._focus_roi_box_size() / 2
        offset = self._focus_roi_offset()
        left = center_x - offset - half_box
        top = center_y - offset - half_box
        right = center_x + offset + half_box
        bottom = center_y + offset + half_box
        return left, top, right, bottom

    def _focus_roi_crop(self, image):
        image_height, image_width = image.shape[:2]
        geometry = self._current_lucam_frame_geometry()
        if geometry is None:
            raise RuntimeError("Focus ROI geometry is not available")
        display_width, display_height, _x, _y = geometry

        if display_width <= 0 or display_height <= 0:
            return image

        center_x, center_y = self._focus_roi_center_from_box()
        left, top, right, bottom = self._focus_roi_display_bounds(center_x, center_y)
        scale_x = image_width / display_width
        scale_y = image_height / display_height
        x1 = max(0, int(left * scale_x))
        y1 = max(0, int(top * scale_y))
        x2 = min(image_width, int((right * scale_x) + 0.999999))
        y2 = min(image_height, int((bottom * scale_y) + 0.999999))

        if x2 <= x1 + 1 or y2 <= y1 + 1:
            return image
        return image[y1:y2, x1:x2]

    def calculate_focus(self, image):
        roi_image = self._focus_roi_crop(image)
        resized = imutils.resize(roi_image, width=150)
        if len(resized.shape) == 3:
            resized = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
        return cv2.Laplacian(resized, cv2.CV_64F).var()
            
            
    # ==================== Image Metadata ====================

    def select_directory(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.dir_path.set(dir_path)

    def set_image_info(self, design: str, lot: str, wafer: str, device_id: str):
        self.info_vars["DESIGN"].set(design)
        self.info_vars["LOT"].set(lot)
        self.info_vars["WAFER"].set(wafer)
        self.info_vars["ID"].set(device_id)
    
    # ==================== Camera Setup And Preview ====================

    def on_window_resize(self, event):
        """Track main window size changes."""
        # Only update if the window size has actually changed
        if (self.window_width != event.width or self.window_height != event.height) and self.lucam and self.streaming:
            self.window_width = event.width
            self.window_height = event.height

    def init_camera(self):
        try:
            #check how many cameras are connected, if it cannot find any then it would indicate that
            num_cameras = LucamNumCameras()
            if num_cameras < 1:
                self.update_status("No cameras found", "red")
                return
            
            # Open the first camera
            self.lucam = Lucam(1)
            frameformat, _ = self.lucam.GetFormat()
            self.frame_width = int(frameformat.width)
            self.frame_height = int(frameformat.height)
            if self.frame_height > 0:
                self.frame_aspect = self.frame_width / self.frame_height
            
            # Enable snapshot buttons
            self.snapshot_button.config(state=tk.NORMAL)
            self.snap_el_button.config(state=tk.NORMAL)
            self.update_status("Camera initialized successfully", "green")
            
        except Exception as e:
            self.update_status(f"Camera initialization error: {e}", "red")

    def resize_preview_window(self, width):
        """Resize the preview from width while preserving camera frame aspect."""
        if (
            not self.lucam
            or not self.streaming
            or self.preview_window is None
            or self.preview_frame is None
            or self.lucam_frame is None
            or width <= 1
        ):
            return
        if self.preview_resize_in_progress:
            return

        try:
            self.preview_resize_in_progress = True
            width = int(width)
            height = int(width / self.frame_aspect)
            self.preview_window.geometry(f"{width}x{height}")
            self.preview_frame.config(width=width, height=height)
            self.lucam_frame.place(x=0, y=0, width=width, height=height)
            self.preview_frame.update_idletasks()
            self.lucam_frame.update_idletasks()
            if self.display_window_created:
                self.lucam.AdjustDisplayWindow(
                    b'Lucam Video Stream',
                    0,
                    0,
                    width,
                    height,
                )
                self._resize_native_preview_window(width, height)
                self._position_focus_overlay()
        except LucamError as exc:
            self.update_status(f"Failed to resize stream: {exc}", "red")
            return
        finally:
            self.preview_resize_in_progress = False

        self.update_status(f"Stream resized to {width}x{height}", "green")

    def _on_preview_frame_resize(self, event):
        """Force dragged preview width to determine the matching height."""
        self.resize_preview_window(event.width)

    def _resize_native_preview_window(self, width, height):
        """Resize the native Lucam display window hosted by the Tk frame."""
        if self.lucam_frame is None:
            return

        parent_hwnd = self.lucam_frame.winfo_id()

        def resize_child(hwnd, _lparam):
            ctypes.windll.user32.MoveWindow(hwnd, 0, 0, width, height, True)
            return True

        enum_proc = WNDENUMPROC(resize_child)
        ctypes.windll.user32.EnumChildWindows(parent_hwnd, enum_proc, 0)
    
    def start_streaming(self):
        if not self.lucam:
            messagebox.showerror("Error", "Camera not initialized")
            return
        
        try:
            if self.preview_window is not None:
                self.preview_window.lift()
                return

            width = int(self.frame_width * 0.5)
            height = int(width / self.frame_aspect)
            self.preview_window = tk.Toplevel(self.master)
            self.preview_window.title("Camera Preview")
            self.preview_window.geometry(f"{width}x{height}")
            self.preview_window.resizable(False, False)
            self.preview_window.bind("<Configure>", lambda _event: self._position_focus_overlay())
            self.preview_window.protocol("WM_DELETE_WINDOW", self.stop_streaming)
            self.preview_frame = tk.Frame(
                self.preview_window,
                width=width,
                height=height,
                bg="black",
            )
            self.preview_frame.pack(fill=tk.BOTH, expand=True)
            self.preview_frame.pack_propagate(False)
            self.preview_frame.bind("<Configure>", self._on_preview_frame_resize)
            self.lucam_frame = tk.Frame(self.preview_frame, bg="black")
            self.lucam_frame.place(x=0, y=0, width=width, height=height)
            self.preview_frame.update_idletasks()
            self.lucam_frame.update_idletasks()

            if not self.display_window_created:
                self.lucam.CreateDisplayWindow(
                    b'Lucam Video Stream',
                    style=LUCAM_CHILD_WINDOW_STYLE,
                    x=0,
                    y=0,
                    width=width,
                    height=height,
                    parent=self.lucam_frame.winfo_id(),
                )
                self.display_window_created = True

            # Start video streaming in the display window.
            self.lucam.StreamVideoControl('start_display')
            
            # Update UI state
            self.streaming = True
            self.stream_duration = 0
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            self.resize_preview_window(width)
            self._resize_native_preview_window(width, height)
            
            self.update_status("Streaming started", "green")
            
            # Start monitoring stream duration
            self.monitor_stream()
            
        except LucamError as e:
            if self.display_window_created:
                try:
                    self.lucam.DestroyDisplayWindow()
                except Exception:
                    pass
                self.display_window_created = False
            if self.preview_window is not None:
                try:
                    self.preview_window.destroy()
                except Exception:
                    pass
                self.preview_window = None
                self.preview_frame = None
                self.lucam_frame = None
            self.update_status(f"Streaming error: {e}", "red")
            
    def stop_streaming(self):
        if not self.lucam:
            return

        errors = []
        try:
            self.lucam.StreamVideoControl('stop_streaming')
        except Exception as exc:
            errors.append(str(exc))

        try:
            if self.display_window_created:
                self.lucam.DestroyDisplayWindow()
                self.display_window_created = False
        except Exception as exc:
            errors.append(str(exc))

        try:
            self.close_focus_overlay()
            if self.preview_window is not None:
                self.preview_window.destroy()
        except Exception as exc:
            errors.append(str(exc))

        self.streaming = False
        self.stream_duration = 0
        self.preview_window = None
        self.preview_frame = None
        self.lucam_frame = None
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        if errors:
            self.update_status(f"Close camera view error: {'; '.join(errors)}", "red")
        else:
            self.update_status("Camera view closed", "red")
    
    # ==================== Visible Snapshot ====================

    def take_snapshot(self):
        if not self.lucam:
            messagebox.showerror("Error", "Camera not initialized")
            return
        if not all(self.info_vars[field].get() for field in self.info_vars):
            messagebox.showerror("Error", "All fields must be filled in order to image")
            return
                
        try:
            # Get current format
            frameformat, framerate = self.lucam.GetFormat()
            
            # Set up custom color correction matrix
            # This is a 3x3 matrix for RGB color correction
            # The values can be adjusted to modify color balance
            custom_matrix = np.array([
                [1.625, 0.0, 0.0],      # Red channel gains
                [0.0, 1.5, 0.0],        # Green channel gains
                [0.0, 0.0, 2.975]       # Blue channel gains
            ], dtype=np.float32)
            
            # Apply the custom matrix to the camera
            self.lucam.SetupCustomMatrix(custom_matrix)
            
            # Take raw snapshot
            snapshot_settings = self.lucam.default_snapshot()
            raw_image = self.lucam.TakeSnapshot(snapshot_settings)
            
            # Get image dimensions
            height, width = raw_image.shape
            
            # Create buffer for RGB24 output
            rgb_image = np.empty((height, width, 3), dtype='uint8', order='C')
            
            # Setup conversion parameters
            conversion = API.LUCAM_CONVERSION(
                DemosaicMethod=API.LUCAM_DM_HIGHER_QUALITY,  # Use highest quality demosaicing
                CorrectionMatrix=API.LUCAM_CM_CUSTOM        # Use our custom matrix
            )
            
            # Convert raw to RGB24 using API
            if not API.LucamConvertFrameToRgb24(
                self.lucam._handle,
                rgb_image,
                raw_image.ctypes.data_as(API.pBYTE),
                width,
                height,
                frameformat.pixelFormat,
                ctypes.pointer(conversion)
            ):
                raise LucamError()
            
            # Convert from BGR to RGB format
            rgb_image = rgb_image[..., ::-1]  # Flip the color channels
            # rgb_image = np.flip(rgb_image, axis = (0,1))
            # rgb_image = np.flip(rgb_image, axis = (0,1))
            rgb_image = cv2.rotate(rgb_image, cv2.ROTATE_180)
            rgb_image = np.flip(rgb_image, axis=1)
            
            # plt.figure()
            # plt.imshow(rgb_image)
            
            # Create filename
            filename = "_".join(self.info_vars[field].get() for field in self.info_fields)
            file_path = os.path.join(self.dir_path.get(), f"{filename}.jpg")
            
            # Display both raw and converted images
            # fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))
            
            # ax1.imshow(raw_image, cmap='gray')
            # ax1.set_title('Raw Bayer Pattern')
            # ax1.axis('off')
            
            # ax2.imshow(rgb_image)
            # ax2.set_title('Converted RGB Image')
            # ax2.axis('off')
            
            # plt.tight_layout()
            # plt.show()
            
            # Save the RGB image
            plt.imsave(file_path, rgb_image)
            
            # Print the current matrix for verification
            current_matrix = self.lucam.GetCurrentMatrix()
            
            # print("Current color correction matrix:")
            # print(current_matrix)
            
            self.update_status("Snapshot taken and converted successfully", "green")
            return rgb_image
        except LucamError as e:
            messagebox.showerror("Snapshot Error", f"Failed to take snapshot: {e}")
            self.update_status(f"Snapshot error: {e}", "red")
    def monitor_stream(self):
        # check if still streaming
        if not self.streaming:
            return
        wait_duration = 1
        
        # Increment stream duration everytime the function is called
        self.stream_duration += wait_duration
        
        if self.stream_duration >= self.max_stream_time:
            self.stop_streaming()
            messagebox.showerror("stream limit",  f"Maximum stream time of {self.max_stream_time} seconds reached")
            return
        
        # Schedule next check in X number of seconds
        self.master.after((wait_duration * 1000), self.monitor_stream)
            
    # ==================== Status And Cleanup ====================

    def update_status(self, message, color="black"):
        self.status_label.config(text=message, fg=color)
    
    def on_closing(self):
        # cleanup when window is closed
        try:
            # stop streaming if active
            if self.streaming:
                self.stop_streaming()
            
            #Destroy display window
            if self.lucam:
                try:
                    if self.display_window_created:
                        self.lucam.DestroyDisplayWindow()
                        self.display_window_created = False
                except: pass
                try:
                    if self.preview_window is not None:
                        self.preview_window.destroy()
                        self.preview_window = None
                        self.preview_frame = None
                        self.lucam_frame = None
                except: pass

                #close the camera connection
                self.lucam.CameraClose()
        except Exception as e:
            print(f"cleanup error:{e}")
        
        # close tkinter window
        self.master.destroy()
        
    
    # ==================== Camera Settings ====================

    def set_exposure(self):
       if not self.lucam:
           return
       try:
           # Preserve whether the user had the camera preview open.
           was_streaming = self.streaming
           if was_streaming:
               self.stop_streaming()
           
           exposure_value = self.exposure_var.get()
           self.lucam.SetProperty('exposure', exposure_value)
           
           frameformat, _ = self.lucam.GetFormat()
           frame_rates = self.lucam.EnumAvailableFrameRates()
           max_fps = max(frame_rates)
           
           self.lucam.SetFormat(frameformat, framerate=max_fps)
           self.update_status(f"Exposure: {exposure_value}ms, Max FPS: {max_fps}", "green")
           
           if was_streaming:
               self.start_streaming()
           
       except LucamError as e:
           self.update_status(f"Failed to set exposure: {e}", "red")
    
    # ==================== Image Processing Helpers ====================

    def name_check(self):
            filename = "_".join(self.info_vars[field].get() for field in self.info_fields)
            file_path = os.path.join(self.dir_path.get(), f"{filename}.jpg")
            
            #Check if file exists in directory
            if os.path.exists(file_path):
                result = messagebox.askyesno(
                    "File Exists",
                    f"It seems like this file already exists, do you wish to override image?")
                return result
            return True
    
    def image_overlay(self, vis_img, el_img):
        blended = Image.blend(Image.fromarray(vis_img), Image.fromarray(el_img), 0.8)
        shifted_overlay = Image.new("RGBA", blended.convert("RGBA").size, (0, 0, 0, 0))
        shift_x = 5    # uPDATED NOV 12, BRW
        shift_y = -20    # UPDATED NOV 12, BRW
        shifted_overlay.paste(Image.fromarray(el_img).convert("RGBA"), (shift_x, shift_y))
        shifted_overlay = Image.blend(Image.fromarray(vis_img).convert("RGBA"), shifted_overlay.convert("RGBA"), 0.8)
        shifted_overlay = shifted_overlay.convert("RGB")
        base_filename = "_".join(self.info_vars[field].get() for field in self.info_fields)
        output_path = f"{base_filename}_overlay.jpg"
        file_path = os.path.join(self.dir_path.get(), output_path)
        shifted_overlay.save(file_path)
    
    @staticmethod
    def _show_el_comparison(vis_image, el_image) -> None:
        """Display visible and EL images for a user-requested manual capture."""
        _, (visible_axis, el_axis) = plt.subplots(1, 2, figsize=(12, 6))
        visible_axis.imshow(vis_image)
        visible_axis.set_title("Visible image")
        visible_axis.axis("off")
        el_axis.imshow(el_image)
        el_axis.set_title("EL image")
        el_axis.axis("off")
        plt.tight_layout()
        plt.show()

    def _turn_capture_outputs_off(self) -> bool:
        """Turn off both capture sources without skipping the second on error."""
        success = True
        for name, instrument in (
            ("light", self.light_keithley),
            ("probe", self.probe_keithley),
        ):
            if instrument is None:
                continue
            try:
                set_keithley_output(instrument, False)
                if self.output_state_changed is not None:
                    self.output_state_changed(name, False)
            except Exception as exc:
                success = False
                self.update_status(f"Failed to turn off {name} Keithley: {exc}", "red")
        return success

    # ==================== EL Snapshot Workflow ====================

    def take_el_snapshot(self, show_comparison: bool = False) -> bool:
        """Capture visible and EL images with safe source and motor cleanup."""
        el_pulse = 3700
        if not self.lucam or not self.motor:
            messagebox.showerror("Error", "Camera or motor not initialized")
            return False
        if self.light_keithley is None or self.probe_keithley is None:
            messagebox.showerror("Error", "Both Keithleys must be connected")
            return False
        try:
            light_current = float(
                self.light_keithley.query(":SOUR:CURR:LEV?").strip()
            )
            probe_current = float(
                self.probe_keithley.query(":SOUR:CURR:LEV?").strip()
            )
        except Exception as exc:
            messagebox.showerror("Error", f"Unable to verify Keithley currents: {exc}")
            return False
        if light_current == 0 or probe_current == 0:
            messagebox.showerror("Error", "Apply non-zero light and probe currents first")
            return False
        if not all(self.info_vars[field].get() for field in self.info_vars):
            messagebox.showerror("Error", "All fields must be filled in order to image")
            return False
        if not self.name_check():
            self.update_status("Existing files kept; capture skipped", "orange")
            return True

        original_exposure = self.exposure_var.get()
        initial_position = get_pos(self.motor, [EL_CAMERA_AXIS])[EL_CAMERA_AXIS]
        self.capture_in_progress = True
        camera_moved = False
        return_move_ok = True
        output_cleanup_ok = True
        el_snapshot_ok = False

        try:
            if self.stop_requested is not None and self.stop_requested():
                self.update_status("EL imaging stopped before capture", "red")
                return False

            # Use only the light source for the visible-light image.
            set_keithley_output(self.light_keithley, True)
            if self.output_state_changed is not None:
                self.output_state_changed("light", True)

            visible_image = self.take_snapshot()
            if visible_image is None:
                return False

            set_keithley_output(self.light_keithley, False)
            if self.output_state_changed is not None:
                self.output_state_changed("light", False)

            self.update_status("Moving camera for EL image...", "black")
            if not self._move_motor(str(el_pulse)):
                self.update_status("EL imaging cancelled during motor movement", "red")
                return False
            camera_moved = True
            time.sleep(1)

            self.lucam.set_properties(
                brightness=1.0,
                contrast=1.0,
                saturation=1.0,
                hue=0.0,
                gamma=1.0,
                exposure=800.0,
                gain=3.9,
            )
            self.exposure_var.set(800.0)
            self.set_exposure()

            frameformat, _ = self.lucam.GetFormat()
            snapshot_settings = self.lucam.default_snapshot()
            # Energize the die only after the light is off and immediately
            # before taking the EL image.
            set_keithley_output(self.probe_keithley, True)
            if self.output_state_changed is not None:
                self.output_state_changed("probe", True)
            raw_image = self.lucam.TakeSnapshot(snapshot_settings)
            set_keithley_output(self.probe_keithley, False)
            if self.output_state_changed is not None:
                self.output_state_changed("probe", False)
            height, width = raw_image.shape
            el_image = np.empty((height, width, 3), dtype="uint8", order="C")
            conversion = API.LUCAM_CONVERSION(
                DemosaicMethod=API.LUCAM_DM_HIGHER_QUALITY,
                CorrectionMatrix=API.LUCAM_CM_CUSTOM,
            )

            if not API.LucamConvertFrameToRgb24(
                self.lucam._handle,
                el_image,
                raw_image.ctypes.data_as(API.pBYTE),
                width,
                height,
                frameformat.pixelFormat,
                ctypes.pointer(conversion),
            ):
                raise LucamError()

            el_image = el_image[..., ::-1]
            el_image = cv2.rotate(el_image, cv2.ROTATE_180)
            el_image = np.flip(el_image, axis=1)

            self.image_overlay(visible_image, el_image)
            base_filename = "_".join(
                self.info_vars[field].get() for field in self.info_fields
            )
            file_path = os.path.join(self.dir_path.get(), f"{base_filename}_EL.jpg")
            plt.imsave(file_path, el_image)

            el_snapshot_ok = True
            self.update_status("EL snapshot sequence completed successfully", "green")
        except Exception as exc:
            messagebox.showerror("EL Snapshot Error", f"Failed to take EL snapshot: {exc}")
            self.update_status(f"EL snapshot sequence error: {exc}", "red")
        finally:
            # De-energize the die before moving the camera or stage again.
            output_cleanup_ok = self._turn_capture_outputs_off()

            if camera_moved:
                try:
                    current_position = get_pos(self.motor, [EL_CAMERA_AXIS])[EL_CAMERA_AXIS]
                    return_pulse = convert_axis_pulse(
                        EL_CAMERA_AXIS,
                        int(initial_position) - int(current_position),
                    )
                    return_move_ok = self._move_motor(return_pulse)
                    if not return_move_ok:
                        self.update_status("EL return movement was stopped", "red")
                except Exception as exc:
                    return_move_ok = False
                    self.update_status(f"Failed to return EL camera: {exc}", "red")

            try:
                self.exposure_var.set(original_exposure)
                self.set_exposure()
                self.lucam.set_properties(
                    brightness=1.0,
                    contrast=1.0,
                    saturation=1.0,
                    hue=0.0,
                    gamma=1.0,
                    exposure=original_exposure,
                    gain=1.0,
                )
            except Exception as exc:
                el_snapshot_ok = False
                self.update_status(f"Failed to restore camera settings: {exc}", "red")
            self.capture_in_progress = False

        capture_ok = el_snapshot_ok and return_move_ok and output_cleanup_ok
        if capture_ok:
            set_keithley_output(self.light_keithley, True)
            if self.output_state_changed is not None:
                self.output_state_changed("light", True)

        if capture_ok and show_comparison:
            self._show_el_comparison(visible_image, el_image)
        return capture_ok
    

# ==================== Standalone Entry Point ====================

def main():
    motor = None
    try:
        try:
            motor = set_res_gpib("3")
        except Exception as e:
            print(f"Failed to initialize motor: {e}")
        root = tk.Tk()
        root.geometry("400x650")  # Increased window size
        app = LucamStreamApp(root, motor=motor)
        root.protocol("WM_DELETE_WINDOW", app.on_closing)
        root.mainloop()
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if motor is not None:
            motor.close()

if __name__ == '__main__':
    main()

