import unittest
from unittest.mock import MagicMock, patch, sentinel

import SSD220
from stage_gui import StageGui


class ManualDieMoveTests(unittest.TestCase):
    def _gui_for_destination(self, destination: str = "1") -> StageGui:
        gui = StageGui.__new__(StageGui)
        gui._main_running = False
        gui._home_is_set = True
        gui.home_position = {"X": "0", "Y": "0", "Z": "100", "U": "0", "V": "0"}
        gui.die_config = {"1": ("device-1", "")}
        gui.die_positions = {"1": {"X": "10", "Y": "20", "U": "0", "V": "0"}}
        gui.die_controller_positions = {
            "1": {"X": "-200", "Y": "-2", "U": "0", "V": "0", "Z": "100"}
        }
        gui.move_destination_combobox = MagicMock()
        gui.move_destination_combobox.get.return_value = destination
        gui._get_stage_inst = MagicMock(return_value=sentinel.stage)
        gui._log = MagicMock()
        return gui

    def test_die_move_uses_absolute_target_at_home_z(self):
        gui = self._gui_for_destination()
        with patch("stage_gui.move_to_position") as movement:
            gui._handle_move_position()
        movement.assert_called_once_with(
            sentinel.stage, gui.die_controller_positions["1"],
            read_position=False, home_z="100",
        )
        gui._log.assert_called_once_with("Moved to die 1")

    def test_failed_die_move_does_not_report_success(self):
        gui = self._gui_for_destination()
        with patch("stage_gui.move_to_position", side_effect=RuntimeError("movement failed")):
            gui._handle_move_position()
        gui._log.assert_called_once_with("Move to 1 error: movement failed")

    def test_invalid_or_unavailable_die_target_does_not_move(self):
        for destination, targets in (("2", {}), ("1", None), ("1", {})):
            with self.subTest(destination=destination, targets=targets):
                gui = self._gui_for_destination(destination)
                gui.die_controller_positions = targets
                with patch("stage_gui.move_to_position") as movement:
                    gui._handle_move_position()
                movement.assert_not_called()
                gui._get_stage_inst.assert_not_called()

    def test_die_move_completes_retraction_before_lateral_motion(self):
        gui = self._gui_for_destination()
        positions = {"X": "0", "Y": "0", "Z": "200", "U": "0", "V": "0"}
        events = []

        def get_pos(_inst, axes=None):
            return {axis: positions[axis] for axis in (positions if axes is None else axes)}

        def move_axis(_inst, axis, pulse, *_speeds):
            if int(pulse) == 0:
                return False
            positions[axis] = str(int(positions[axis]) + int(pulse))
            events.append(("start", axis))
            return True

        def motion_wait(_inst, axis, **_kwargs):
            events.append(("complete", axis))

        with (
            patch("stage_gui.move_to_position", SSD220.move_to_position),
            patch.object(SSD220, "get_pos", side_effect=get_pos),
            patch.object(SSD220, "_move_axis", side_effect=move_axis),
            patch.object(SSD220, "_mot_wait", side_effect=motion_wait),
        ):
            gui._handle_move_position()
        self.assertEqual(events, [
            ("start", "Z"), ("complete", "Z"),
            ("start", "X"), ("complete", "X"),
            ("start", "Y"), ("complete", "Y"),
        ])
        self.assertEqual(positions, gui.die_controller_positions["1"])

    def test_set_home_generates_both_tables_and_rebuilds_absolute_targets(self):
        gui = self._gui_for_destination()
        gui._refresh_move_destinations = MagicMock()
        layout = {"1": {"X": "10", "Y": "20", "U": "3", "V": "4"}}
        homes = [
            {"X": "1000", "Y": "2000", "U": "3000", "V": "4000", "Z": "50"},
            {"X": "1100", "Y": "2100", "U": "3100", "V": "4100", "Z": "60"},
        ]
        expected_targets = [
            {"X": "800", "Y": "1998", "U": "2997", "V": "3990", "Z": "50"},
            {"X": "900", "Y": "2098", "U": "3097", "V": "4090", "Z": "60"},
        ]
        with (
            patch("stage_gui.get_pos", side_effect=homes),
            patch("stage_gui.DieLayout") as die_layout,
        ):
            die_layout.return_value.die_positions.return_value = layout
            for home, expected in zip(homes, expected_targets):
                gui._set_home()
                self.assertEqual(gui.home_position, home)
                self.assertEqual(gui.die_positions, layout)
                self.assertEqual(gui.die_controller_positions, {"1": expected})
                self.assertIsNone(gui.contact_z)

    def test_failed_home_rebuild_keeps_previous_coordinate_tables(self):
        gui = self._gui_for_destination()
        previous_home = gui.home_position.copy()
        previous_layout = gui.die_positions.copy()
        previous_targets = gui.die_controller_positions.copy()
        with (
            patch("stage_gui.get_pos", return_value={"Z": "50"}),
            patch("stage_gui.DieLayout") as die_layout,
        ):
            die_layout.return_value.die_positions.return_value = {"1": {"X": "10"}}
            gui._set_home()
        self.assertEqual(gui.home_position, previous_home)
        self.assertEqual(gui.die_positions, previous_layout)
        self.assertEqual(gui.die_controller_positions, previous_targets)


if __name__ == "__main__":
    unittest.main()
