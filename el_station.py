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
import cv2
import imutils
import time
from PIL import Image
from pyvisa.resources import MessageBasedResource
from Keithley2400 import set_keithley_output
from SSD220 import get_pos, move, move_with_control, set_res_gpib

EL_CAMERA_AXIS = "W"


class LucamStreamApp:
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
        self.capture_in_progress = False
        self.stream_duration = 0
        self.max_stream_time = 36001  # 10 hours of run-time and then it closes camera
        
        # Stream display settings
        self.current_scale = 100  # Default scale 100%
        self.frame_width = 0
        self.frame_height = 0
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
        
    def setup_ui(self):
        """================ Exposure Control Set ================"""
        exposure_frame = tk.Frame(self.master)
        exposure_frame.pack(pady=5)
        
        tk.Label(exposure_frame, text="Exposure (ms):").pack(side=tk.LEFT)
        exposure_entry = tk.Entry(exposure_frame, textvariable=self.exposure_var, width=10)
        exposure_entry.pack(side=tk.LEFT, padx=5)
        tk.Button(exposure_frame, text="Set Exposure", command=self.set_exposure).pack(side=tk.LEFT)
        
        """================ Stream Resize Control Set ================"""
        resize_frame = tk.Frame(self.master)
        resize_frame.pack(pady=5)
        
        tk.Label(resize_frame, text="Stream Size:").pack(side=tk.LEFT)
        
        # Size buttons
        tk.Button(resize_frame, text="50%", command=lambda: self.set_stream_scale(50)).pack(side=tk.LEFT, padx=5)
        tk.Button(resize_frame, text="75%", command=lambda: self.set_stream_scale(75)).pack(side=tk.LEFT, padx=5)
        tk.Button(resize_frame, text="100%", command=lambda: self.set_stream_scale(100)).pack(side=tk.LEFT, padx=5)
        tk.Button(resize_frame, text="Fit to Window", command=self.fit_to_window).pack(side=tk.LEFT, padx=5)
    
        """================ Save Directory Set ================"""
        dir_frame = tk.Frame(self.master)
        dir_frame.pack(pady=5)
        
        tk.Label(dir_frame, text="Save Directory:").pack(side=tk.LEFT)
        tk.Entry(dir_frame, textvariable=self.dir_path, width=40).pack(side=tk.LEFT, padx=5)
        tk.Button(dir_frame, text="Browse", command=self.select_directory).pack(side=tk.LEFT)

        """================ Image Info Set ================"""
        info_frame = tk.Frame(self.master)
        info_frame.pack(pady=5)
        
        for label in self.info_fields:
            row = tk.Frame(info_frame)
            row.pack(pady=2)
            tk.Label(row, text=f"{label}:").pack(side=tk.LEFT)
            tk.Entry(row, textvariable=self.info_vars[label], width=20).pack(side=tk.LEFT)
            
        """================ Basic Motor Control Set ================"""
        motor_frame = tk.Frame(self.master)
        motor_frame.pack(pady=10)
        
        tk.Label(motor_frame, text="Steps:").pack(side=tk.LEFT)
        tk.Entry(motor_frame, textvariable=self.steps_var, width=10).pack(side=tk.LEFT, padx=5)
        
        tk.Button(motor_frame, text="Move Up", command=self.move_up).pack(side=tk.LEFT, padx=5)
        tk.Button(motor_frame, text="Move Down", command=self.move_down).pack(side=tk.LEFT, padx=5)
        tk.Button(motor_frame, text="Find Focus", command=self.find_focus).pack(side=tk.LEFT, padx=5)
        
        # Focus score display
        tk.Label(motor_frame, textvariable=self.focus_score_var).pack(side=tk.LEFT, padx=5)
    
        """================ Streaming Control Set ================"""
        streaming_frame = tk.Frame(self.master)
        streaming_frame.pack(pady=10)
        
        self.start_button = tk.Button(streaming_frame, text="Open Camera View", command=self.start_streaming)
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = tk.Button(streaming_frame, text="Close Camera View", command=self.stop_streaming, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        """================ Snapshot Control Set ================"""
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
        
        """================ Status Display Set ================"""
        self.status_label = tk.Label(self.master, text="Camera not initialized", fg="red")
        self.status_label.pack(pady=10)
        
        """================ Fine Motor Control Set ================"""
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
        
        tk.Button(motor_frame, text="Find Focus", command=self.find_focus_en).pack(pady=5)
        
        # Focus score display
        tk.Label(motor_frame, textvariable=self.focus_score_var).pack(pady=5)

    def on_window_resize(self, event):
        """Handle window resize event - adjust video stream if "Fit to Window" was selected"""
        # Only update if the window size has actually changed
        if (self.window_width != event.width or self.window_height != event.height) and self.lucam and self.streaming:
            self.window_width = event.width
            self.window_height = event.height
            # If we're in "fit to window" mode, adjust the stream
            if self.current_scale == 0:  # 0 indicates "fit to window"
                self.fit_to_window()
    
    def set_stream_scale(self, scale_percent):
        """Set the stream display size to a percentage of the original size"""
        if not self.lucam or not self.streaming:
            return
                
        self.current_scale = scale_percent
        
        try:
            # Get the current frame format to know the actual frame size
            frameformat, _ = self.lucam.GetFormat()
            
            # Calculate the width and height based on the scale
            width = int(frameformat.width * scale_percent / 100)
            height = int(frameformat.height * scale_percent / 100)
            
            # Stop streaming first
            # self.lucam.StreamVideoControl('stop_streaming')
            
            # Adjust the display window
            self.lucam.AdjustDisplayWindow(b'Lucam Video Stream', 0, 0, width, height)
            
            # Restart streaming
            # self.lucam.StreamVideoControl('start_display')
            
            self.update_status(f"Stream resized to {scale_percent}%", "green")
            
        except LucamError as e:
            self.update_status(f"Failed to resize stream: {e}", "red")
    
    def fit_to_window(self):
        """Adjust the stream to fit the current window size"""
        if not self.lucam or not self.streaming:
            return
            
        self.current_scale = 0  # 0 indicates "fit to window" mode
        
        try:
            # Get window dimensions (subtract some padding for UI elements)
            window_width = self.master.winfo_width() - 50
            window_height = self.master.winfo_height() - 350  # Adjust as needed based on UI layout
            
            # Ensure minimum dimensions
            window_width = max(window_width, 320)
            window_height = max(window_height, 240)
            
            # Get the current frame format
            frameformat, _ = self.lucam.GetFormat()
            original_width = frameformat.width
            original_height = frameformat.height
            
            # Calculate scaling to maintain aspect ratio
            width_ratio = window_width / original_width
            height_ratio = window_height / original_height
            
            # Use the smaller ratio to ensure it fits within the window
            scale_ratio = min(width_ratio, height_ratio)
            
            display_width = int(original_width * scale_ratio)
            display_height = int(original_height * scale_ratio)
            
            # Adjust the display window
            self.lucam.AdjustDisplayWindow(b'Lucam Video Stream', 0, 0, display_width, display_height)
            
            # Calculate and display the actual scale percentage
            actual_scale = int(scale_ratio * 100)
            self.update_status(f"Stream fitted to window ({actual_scale}% of original)", "green")
            
        except LucamError as e:
            self.update_status(f"Failed to fit stream to window: {e}", "red")
            
        


    def move_up(self):
        if not self.motor:
            messagebox.showerror("Error", "Motor not initialized")
            return
        try:
            steps = self.steps_var.get()
            if not self._move_motor(str(-int(steps))):
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
            if not self._move_motor(str(int(steps))):
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
            if not self._move_motor(str(-int(steps))):
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
            if not self._move_motor(str(int(steps))):
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

    def find_focus(self):
        if not self.motor or not self.lucam:
            messagebox.showerror("Error", "Motor or camera not initialized")
            return
        positions = []
        scores = []
            
        try:
            # Fixed step size of 2500
            step_size = 2500
            current_pos = 0 
            self.update_status(f"Searching for best focus using 2500 steps per increment - Please wait", "black")
            
            # Store positions and their focus scores
            
            # Take initial snapshot and calculate focus at current position
            initial_position = get_pos(self.motor, [EL_CAMERA_AXIS])[EL_CAMERA_AXIS]
            initial_position_value = 0
            snapshot = self.lucam.TakeSnapshot()
            current_score = self.calculate_focus(snapshot)
            
            # Record initial position and score
            positions.append(initial_position_value)
            scores.append(current_score)
            self.focus_score_var.set(f"Focus Score: {current_score:.2f}")
            self.update_status(f"Initial position: {initial_position}, Score: {current_score:.2f}", "black")
            self.master.update()
            
            # First step up (2500 steps)
            move(self.motor, {EL_CAMERA_AXIS: str(-int(step_size))})
            time.sleep(0.5)  # Wait for stability
            up1_position = 2500
            up1_position_value = int(up1_position)
            
            snapshot = self.lucam.TakeSnapshot()
            up1_score = self.calculate_focus(snapshot)
            
            # Record first up position and score
            positions.append(up1_position_value)
            scores.append(up1_score)
            self.focus_score_var.set(f"Focus Score: {up1_score:.2f}")
            self.update_status(f"Up 1 position: {up1_position}, Score: {up1_score:.2f}", "black")
            self.master.update()
            
            # Second step up (another 2500 steps)
            move(self.motor, {EL_CAMERA_AXIS: str(-int(step_size))})
            time.sleep(0.5)  # Wait for stability
            up2_position = 5000
            up2_position_value = int(up2_position)
            
            snapshot = self.lucam.TakeSnapshot()
            up2_score = self.calculate_focus(snapshot)
            
            # Record second up position and score
            positions.append(up2_position_value)
            scores.append(up2_score)
            self.focus_score_var.set(f"Focus Score: {up2_score:.2f}")
            self.update_status(f"Up 2 position: {up2_position}, Score: {up2_score:.2f}", "black")
            self.master.update()
            
            # Move back to initial position
            move(self.motor, {EL_CAMERA_AXIS: str(int(2 * step_size))})
            time.sleep(0.5)  # Wait for stability
            
            # First step down (2500 steps)
            move(self.motor, {EL_CAMERA_AXIS: str(int(step_size))})
            time.sleep(0.5)  # Wait for stability
            down1_position = -2500
            down1_position_value = int(down1_position)
            
            snapshot = self.lucam.TakeSnapshot()
            down1_score = self.calculate_focus(snapshot)
            
            # Record first down position and score
            positions.append(down1_position_value)
            scores.append(down1_score)
            self.focus_score_var.set(f"Focus Score: {down1_score:.2f}")
            self.update_status(f"Down 1 position: {down1_position}, Score: {down1_score:.2f}", "black")
            self.master.update()
            
            # Second step down (another 2500 steps)
            move(self.motor, {EL_CAMERA_AXIS: str(int(step_size))})
            time.sleep(0.5)  # Wait for stability
            down2_position = -5000
            down2_position_value = int(down2_position)
            
            snapshot = self.lucam.TakeSnapshot()
            down2_score = self.calculate_focus(snapshot)
            
            # Record second down position and score
            positions.append(down2_position_value)
            scores.append(down2_score)
            self.focus_score_var.set(f"Focus Score: {down2_score:.2f}")
            self.update_status(f"Down 2 position: {down2_position}, Score: {down2_score:.2f}", "black")
            self.master.update()
            
            # Find the best focus score and position
            best_score_index = scores.index(max(scores))
            best_position = positions[best_score_index]
            print(best_position)
            best_score = scores[best_score_index]
            
                                
            if best_position > 0:
                best_dir = 'CCW'
            else: best_dir = 'CW'
            
            time.sleep(0.4)
            move(self.motor, {EL_CAMERA_AXIS: str(-5000)})
            time.sleep(1.4)
            # If best focus is at one of the extremes, move there but warn user
            if best_score_index == 0 or best_score_index == 4:
                # Move to the position with best focus
                self.update_status(f"Best focus at extreme position: {best_position}, moving there...", "orange")
                move(self.motor, {EL_CAMERA_AXIS: str(abs(int(best_position)) if best_dir == "CW" else -abs(int(best_position)))})
                
                self.focus_score_var.set(f"Focus Score: {best_score:.2f}")
                self.update_status(f"Focus optimization complete. Best at extreme: {best_score:.2f}", "orange")
            else:
                # We have 5 points, let's estimate the best position using a parabola
                # Sort positions and scores together
                sorted_data = sorted(zip(positions, scores))
                sorted_positions, sorted_scores = zip(*sorted_data)
                
                # Try to fit a parabola (quadratic function) through the five points
                try:
                    import numpy as np
                    from scipy import optimize
                    
                    # Define the quadratic function: f(x) = a*x^2 + b*x + c
                    def quadratic(x, a, b, c):
                        return a * (x**2) + b * x + c
                    
                    # Fit the function to our data points (all 5 points)
                    params, _ = optimize.curve_fit(quadratic, sorted_positions, sorted_scores)
                    a, b, c = params
                    
                    # Log the fitted parameters for debugging
                    self.update_status(f"Fit parameters: a={a:.6f}, b={b:.2f}, c={c:.2f}", "black")
                    self.master.update()

                    
                    
                    # The maximum of a parabola is at x = -b/(2*a)
                    # But only if a < 0 (opens downward)
                    if a < 0:
                        estimated_best_position = int(-b / (2 * a))
                        print(estimated_best_position)
                        
                        # Check if the estimated position is within our search range
                        min_pos = min(positions)
                        max_pos = max(positions)
                        if min_pos <= estimated_best_position <= max_pos:
                            if estimated_best_position > 0:
                                best_dir = 'CCW'
                            else: best_dir = 'CW'
                            # Move to the estimated best position
                            self.update_status(f"Moving to estimated best position: {estimated_best_position}...", "black")
                            move(self.motor, {EL_CAMERA_AXIS: str(abs(int(best_position)) if best_dir == "CW" else -abs(int(best_position)))})
                            
                            # Take a snapshot at the estimated best position and check focus
                            time.sleep(0.5)
                            snapshot = self.lucam.TakeSnapshot()
                            estimated_score = self.calculate_focus(snapshot)
                            
                            self.focus_score_var.set(f"Focus Score: {estimated_score:.2f}")
                            self.update_status(f"Focus optimization complete. Estimated best score: {estimated_score:.2f}", "green")
                        else:
                            # If estimated position is outside our range, move to the best known position
                            self.update_status(f"Estimated position out of range, moving to best known position...", "black")
                            move(self.motor, {EL_CAMERA_AXIS: str(abs(int(best_position)) if best_dir == "CW" else -abs(int(best_position)))})
                            self.focus_score_var.set(f"Focus Score: {best_score:.2f}")
                            self.update_status(f"Focus optimization complete. Best score: {best_score:.2f}", "green")
                    else:
                        # If parabola opens upward, can't find a maximum - use best known position
                        self.update_status(f"Cannot determine best focus, moving to best known position...", "black")
                        move(self.motor, {EL_CAMERA_AXIS: str(abs(int(best_position)) if best_dir == "CW" else -abs(int(best_position)))})
                        self.focus_score_var.set(f"Focus Score: {best_score:.2f}")
                        self.update_status(f"Focus optimization complete. Best score: {best_score:.2f}", "green")
                except:
                    # If curve fitting fails, fall back to best known position
                    self.update_status(f"Error in focus estimation, moving to best known position...", "black")
                    move(self.motor, {EL_CAMERA_AXIS: str(abs(int(best_position)) if best_dir == "CW" else -abs(int(best_position)))})
                    self.focus_score_var.set(f"Focus Score: {best_score:.2f}")
                    self.update_status(f"Focus optimization complete. Best score: {best_score:.2f}", "green")
            
        except Exception as e:
            self.update_status(f"Focus finding error: {e}", "red")
            # Try to return to initial position on error
            try:
                print('fail')
            except:
                pass
            
            # For debugging purposes, print all measured points
            self.update_status(f"Debug - All points: {list(zip(positions, scores))}", "red")
            self.master.update()
        
    def find_focus_en(self):
        if not self.motor or not self.lucam:
            messagebox.showerror("Error", "Motor or camera not initialized")
            return
        positions = []
        scores = []
        rerun = False
        try:
            snapshot = self.lucam.TakeSnapshot()
            current_score = self.calculate_focus(snapshot)
            # Fixed step size of 2500
            if current_score < 90: 
                step_size = 1000
                rerun = True
            elif (current_score >= 90) == True  &  (current_score <= 200)==True: 
                step_size = 500
                rerun = True
            else: 
                step_size = 200
                rerun = False
            
            # Store positions and their focus scores
            positions = [0, step_size,step_size*2,step_size*3,step_size*4,step_size*5,step_size*6, -step_size*1, -step_size*2, -step_size*3, -step_size*4, -step_size*5, -step_size*6]
            pulse_send = [0,step_size,step_size,step_size,step_size,step_size,step_size, -step_size*7, -step_size,-step_size,-step_size,-step_size,-step_size]
            scores = []
            
            for i,position in enumerate(positions):
                if pulse_send[i] >= 0: direction = 'CCW'
                elif pulse_send[i] < 0: direction = 'CW'
                move(self.motor, {EL_CAMERA_AXIS: str(abs(int(pulse_send[i])) if direction == "CW" else -abs(int(pulse_send[i])))})
                time.sleep(0.3)
                snapshot = self.lucam.TakeSnapshot()
                current_score = self.calculate_focus(snapshot)
                scores.append(current_score)
                if i > 0: print(scores[-1] > scores[-2])
                self.update_status(f"{current_score}", "green")
            
            print(scores)
            
            # Find the best focus score and position
            best_score_index = scores.index(max(scores))
            best_position = positions[best_score_index]
            # print(best_position)
            best_score = scores[best_score_index]
            
            travel_pulse = best_position - (-(step_size*6))
            move(self.motor, {EL_CAMERA_AXIS: str(-abs(int(travel_pulse)))})
            
            
            
        except Exception as e:
            self.update_status(f"Focus finding error: {e}", "red")
            # Try to return to initial position on error
            try:
                print('fail')
            except:
                pass
            
            # For debugging purposes, print all measured points
            self.update_status(f"Debug - All points: {list(zip(positions, scores))}", "red")
            self.master.update()
            return
        
        if rerun == True: 
            self.find_focus_en()
            self.focus_score_var.set("Still focusing - please wait", 'red')
        else: 
            self.update_status("!!! DONE !!!", "green")
            
            
    def select_directory(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.dir_path.set(dir_path)

    def set_image_info(self, design: str, lot: str, wafer: str, device_id: str):
        self.info_vars["DESIGN"].set(design)
        self.info_vars["LOT"].set(lot)
        self.info_vars["WAFER"].set(wafer)
        self.info_vars["ID"].set(device_id)
    
    def init_camera(self):
        try:
            #check how many cameras are connected, if it cannot find any then it would indicate that
            num_cameras = LucamNumCameras()
            if num_cameras < 1:
                self.update_status("No cameras found", "red")
                return
            
            # Open the first camera
            self.lucam = Lucam(1)
            
            # Enable snapshot buttons
            self.snapshot_button.config(state=tk.NORMAL)
            self.snap_el_button.config(state=tk.NORMAL)
            self.update_status("Camera initialized successfully", "green")
            
        except Exception as e:
            self.update_status(f"Camera initialization error: {e}", "red")
    
    def start_streaming(self):
        if not self.lucam:
            messagebox.showerror("Error", "Camera not initialized")
            return
        
        try:
            if not self.display_window_created:
                self.lucam.CreateDisplayWindow(b'Lucam Video Stream')
                self.display_window_created = True

            # Start video streaming in the display window.
            self.lucam.StreamVideoControl('start_display')
            
            # Update UI state
            self.streaming = True
            self.stream_duration = 0
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            
            self.update_status("Streaming started", "green")
            
            # Start monitoring stream duration
            self.monitor_stream()
            
        except LucamError as e:
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

        self.streaming = False
        self.stream_duration = 0
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        if errors:
            self.update_status(f"Close camera view error: {'; '.join(errors)}", "red")
        else:
            self.update_status("Camera view closed", "red")
    
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

                #close the camera connection
                self.lucam.CameraClose()
        except Exception as e:
            print(f"cleanup error:{e}")
        
        # close tkinter window
        self.master.destroy()
        
    
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
    
    def calculate_focus(self, image):
        # Resize to 100x100
        # resized = imutils.resize(image[100:1400,500:1400], width=150)
        resized = imutils.resize(image, width=150)
        # Convert to grayscale if needed
        if len(resized.shape) == 3:
            resized = cv2.cvtColor(resized[30:-30, 50:-50], cv2.COLOR_RGB2GRAY)
        # Calculate Laplacian variance
        return cv2.Laplacian(resized, cv2.CV_64F).var()
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

    def take_el_snapshot(self, show_comparison: bool = False) -> bool:
        """Capture visible and EL images with safe source and motor cleanup."""
        el_pulse = 28000
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
            if not self._move_motor(str(-el_pulse)):
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
                    return_pulse = str(int(initial_position) - int(current_position))
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

