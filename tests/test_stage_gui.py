import unittest
from unittest.mock import MagicMock, patch, sentinel

from stage_gui import StageGui


class ManualDieMoveTests(unittest.TestCase):
    def _gui_for_destination(self, destination: str = "1") -> StageGui:
        gui = StageGui.__new__(StageGui)
        gui._main_running = False
        gui._home_is_set = True
        gui.home_position = {"X": "0", "Y": "0", "Z": "100", "U": "0", "V": "0"}
        gui.die_config = {"1": ("device-1", "")}
        gui.die_positions = {"1": {"X": "10", "Y": "20", "U": "0", "V": "0"}}
        gui.move_destination_combobox = MagicMock()
        gui.move_destination_combobox.get.return_value = destination
        gui._get_stage_inst = MagicMock(return_value=sentinel.stage)
        gui._log = MagicMock()
        return gui

    def test_die_move_uses_relative_pulse_without_moving_z_home(self):
        gui = self._gui_for_destination()
        pulse = {"X": "100", "Y": "20", "U": "0", "V": "0"}

        with (
            patch("stage_gui._relative_pulse", return_value=pulse) as relative_pulse,
            patch("stage_gui.move_with_control", return_value=True) as move_with_control,
            patch("stage_gui.move_to_position") as move_to_position,
        ):
            gui._handle_move_position()

        relative_pulse.assert_called_once_with(
            sentinel.stage, gui.home_position, gui.die_positions["1"]
        )
        move_with_control.assert_called_once_with(sentinel.stage, pulse)
        move_to_position.assert_not_called()
        gui._log.assert_called_once_with("Moved to die 1")

    def test_stopped_die_move_keeps_existing_log_message(self):
        gui = self._gui_for_destination()

        with (
            patch("stage_gui._relative_pulse", return_value={"X": "100"}),
            patch("stage_gui.move_with_control", return_value=False),
        ):
            gui._handle_move_position()

        gui._log.assert_called_once_with("Move to die 1 was stopped")

    def test_failed_die_move_keeps_existing_log_message(self):
        gui = self._gui_for_destination()

        with patch("stage_gui._relative_pulse", side_effect=RuntimeError("pulse failure")):
            gui._handle_move_position()

        gui._log.assert_called_once_with("Move to die 1 error: pulse failure")


if __name__ == "__main__":
    unittest.main()
