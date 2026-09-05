import importlib
import sys
import types
import unittest
from unittest.mock import patch


def _install_import_stubs() -> None:
    """Provide the optional GUI and hardware dependencies used by stage_gui."""
    tk = types.ModuleType("tkinter")

    class Widget:
        def __init__(self, *_args, **_kwargs):
            pass

    tk.Tk = Widget
    tk.Frame = Widget
    tk.Canvas = Widget
    tk.Misc = Widget
    tk.Event = Widget
    tk.Toplevel = Widget
    tk.Button = Widget
    tk.Text = Widget
    tk.StringVar = Widget
    tk.DISABLED = "disabled"
    tk.NORMAL = "normal"
    tk.LEFT = "left"
    tk.RIGHT = "right"
    tk.TOP = "top"
    tk.BOTTOM = "bottom"
    tk.X = "x"
    tk.Y = "y"
    tk.BOTH = "both"
    tk.END = "end"

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Combobox = Widget
    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.askopenfilename = lambda **_kwargs: ""
    messagebox = types.ModuleType("tkinter.messagebox")
    sys.modules.update(
        {
            "tkinter": tk,
            "tkinter.ttk": ttk,
            "tkinter.filedialog": filedialog,
            "tkinter.messagebox": messagebox,
        }
    )
    tk.ttk = ttk
    tk.filedialog = filedialog

    pandas = types.ModuleType("pandas")
    sys.modules["pandas"] = pandas

    pyvisa = types.ModuleType("pyvisa")
    pyvisa_resources = types.ModuleType("pyvisa.resources")
    pyvisa_resources.MessageBasedResource = object
    sys.modules.update({"pyvisa": pyvisa, "pyvisa.resources": pyvisa_resources})

    keithley = types.ModuleType("Keithley2400")
    for name in ("close_keithley", "set_keithley_current", "set_keithley_output", "setup_keithley"):
        setattr(keithley, name, lambda *_args, **_kwargs: None)
    sys.modules["Keithley2400"] = keithley

    ssd220 = types.ModuleType("SSD220")
    ssd220.Axis = str
    ssd220.AXES = ("X", "Y", "Z", "U", "V")
    ssd220.DEFAULT_FAST_SPEED = "10000"
    ssd220.DEFAULT_LOW_SPEED = "5000"
    for name in (
        "convert_axis_pulse",
        "get_pos",
        "move",
        "move_with_control",
        "move_to_position",
        "set_all_axes_speed_table",
        "set_res_gpib",
    ):
        setattr(ssd220, name, lambda *_args, **_kwargs: None)
    sys.modules["SSD220"] = ssd220

    el_station = types.ModuleType("el_station")
    el_station.FOCUS_SCORE_THRESHOLD_RATIO = 0.1
    el_station.LucamStreamApp = object
    sys.modules["el_station"] = el_station

    yelo = types.ModuleType("YeloModuleImageCapture")
    yelo.DieLayout = object
    yelo._relative_pulse = lambda *_args, **_kwargs: {}
    yelo.die_travel_order = lambda _count, config: list(config)
    yelo.main = lambda *_args, **_kwargs: None
    sys.modules["YeloModuleImageCapture"] = yelo


_STUBBED_MODULES = (
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "pandas",
    "pyvisa",
    "pyvisa.resources",
    "Keithley2400",
    "SSD220",
    "el_station",
    "YeloModuleImageCapture",
)
if "stage_gui" in sys.modules:
    stage_gui = sys.modules["stage_gui"]
else:
    _ORIGINAL_MODULES = {name: sys.modules.get(name) for name in _STUBBED_MODULES}
    _install_import_stubs()
    stage_gui = importlib.import_module("stage_gui")
    for _module_name, _module in _ORIGINAL_MODULES.items():
        if _module is None:
            sys.modules.pop(_module_name, None)
        else:
            sys.modules[_module_name] = _module


class FakeCombobox:
    def __init__(self, value=""):
        self.values = []
        self.state = None
        self.value = value

    def configure(self, **kwargs):
        if "values" in kwargs:
            self.values = list(kwargs["values"])
        if "state" in kwargs:
            self.state = kwargs["state"]

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeButton:
    def __init__(self):
        self.state = None

    def configure(self, **kwargs):
        self.state = kwargs["state"]


class FakeStringVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class FakeStartCombobox(FakeCombobox):
    def current(self, index):
        self.value = self.values[index]


class MoveDestinationTests(unittest.TestCase):
    def setUp(self):
        self.gui = object.__new__(stage_gui.StageGui)
        self.gui.move_destination_combobox = FakeCombobox()
        self.gui.move_position_button = FakeButton()
        self.gui.die_config = {"1": ("one", ""), "2": ("two", "")}
        self.gui._home_is_set = False
        self.gui.contact_z = None
        self.logs = []
        self.gui._log = self.logs.append

    def test_destinations_follow_home_and_contact_state(self):
        self.gui._refresh_move_destinations()
        self.assertEqual(self.gui.move_destination_combobox.values, ["1", "2"])
        self.assertEqual(self.gui.move_destination_combobox.state, "disabled")
        self.assertEqual(self.gui.move_position_button.state, "disabled")

        self.gui._home_is_set = True
        self.gui._refresh_move_destinations()
        self.assertEqual(self.gui.move_destination_combobox.values, ["Home", "1", "2"])
        self.assertEqual(self.gui.move_destination_combobox.value, "Home")

        self.gui.contact_z = "123"
        self.gui._refresh_move_destinations()
        self.assertEqual(self.gui.move_destination_combobox.values, ["Home", "Contact", "1", "2"])

        self.gui.contact_z = None
        self.gui.move_destination_combobox.set("Contact")
        self.gui._refresh_move_destinations()
        self.assertEqual(self.gui.move_destination_combobox.values, ["Home", "1", "2"])
        self.assertEqual(self.gui.move_destination_combobox.value, "Home")

    def test_refresh_preserves_valid_selection_without_duplicate_contact(self):
        self.gui._home_is_set = True
        self.gui.contact_z = "123"
        self.gui.move_destination_combobox.set("2")

        self.gui._refresh_move_destinations()
        self.gui._refresh_move_destinations()

        self.assertEqual(self.gui.move_destination_combobox.value, "2")
        self.assertEqual(self.gui.move_destination_combobox.values.count("Contact"), 1)

    def test_loading_csv_preserves_contact_destination(self):
        self.gui._home_is_set = True
        self.gui.contact_z = "123"
        self.gui.start_die_combobox = FakeStartCombobox()
        self.gui.die_config_csv_path = FakeStringVar()
        self.gui._read_die_config_csv = lambda _path: {
            "1": ("one", ""),
            "2": ("two", ""),
        }

        with patch.object(stage_gui.filedialog, "askopenfilename", return_value="dies.csv"):
            self.gui._select_die_config_csv()

        self.assertEqual(self.gui.die_config_csv_path.value, "dies.csv")
        self.assertEqual(self.gui.move_destination_combobox.values, ["Home", "Contact", "1", "2"])

    def test_setting_home_removes_contact_destination(self):
        class DieLayout:
            def __init__(self, **_kwargs):
                pass

            def die_positions(self):
                return {"1": {"X": "0", "Y": "0", "U": "0", "V": "0"}}

        self.gui._home_is_set = True
        self.gui.contact_z = "123"
        self.gui._focus_reference_score = 1.0
        self.gui._get_stage_inst = lambda: "stage"

        with (
            patch.object(stage_gui, "get_pos", return_value={"Z": "0"}),
            patch.object(stage_gui, "DieLayout", DieLayout),
        ):
            self.gui._set_home()

        self.assertIsNone(self.gui.contact_z)
        self.assertEqual(self.gui.move_destination_combobox.values, ["Home", "1", "2"])

    def test_setting_contact_adds_contact_destination(self):
        self.gui._home_is_set = True
        self.gui._get_stage_inst = lambda: "stage"

        with patch.object(stage_gui, "get_pos", return_value={"Z": "123"}):
            self.gui._set_contact()

        self.assertEqual(self.gui.contact_z, "123")
        self.assertEqual(self.gui.move_destination_combobox.values, ["Home", "Contact", "1", "2"])

    def test_contact_destination_moves_only_z(self):
        self.gui._home_is_set = True
        self.gui.contact_z = "123"
        self.gui._main_running = False
        self.gui.move_destination_combobox.set("Contact")
        self.gui._get_stage_inst = lambda: "stage"

        with patch.object(stage_gui, "move_to_position") as move_to_position:
            self.gui._handle_move_position()

        move_to_position.assert_called_once_with(
            "stage", {"Z": "123"}, read_position=False
        )
        self.assertEqual(self.logs, ["Moved to Contact Z: 123"])

    def test_contact_without_recorded_height_does_not_move(self):
        self.gui._home_is_set = True
        self.gui.contact_z = None
        self.gui._main_running = False
        self.gui.move_destination_combobox.set("Contact")

        with patch.object(stage_gui, "move_to_position") as move_to_position:
            self.gui._handle_move_position()

        move_to_position.assert_not_called()
        self.assertEqual(self.logs, ["Set Contact before moving to Contact"])


if __name__ == "__main__":
    unittest.main()
