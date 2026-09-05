import unittest
from unittest.mock import patch

import YeloModuleImageCapture as yelo


def _die_positions() -> dict[str, dict[str, str]]:
    return {
        "1": {"X": "0", "Y": "0", "U": "0", "V": "0"},
        "2": {"X": "10", "Y": "0", "U": "0", "V": "0"},
        "3": {"X": "0", "Y": "10", "U": "0", "V": "0"},
        "4": {"X": "10", "Y": "10", "U": "0", "V": "0"},
    }


class YeloConfiguredDieTests(unittest.TestCase):
    def _run_sequence(self, *, die_config=None, start_die=None):
        moves = []
        captures = []
        focus_checks = []

        def move_with_control(_stage, pulse, **_kwargs):
            moves.append(pulse)
            return True

        def get_pos(_stage, axes=None):
            axes = ["X", "Y", "Z", "U", "V"] if axes is None else axes
            return {axis: "0" for axis in axes}

        with (
            patch.object(yelo, "div"),
            patch.object(yelo, "get_pos", side_effect=get_pos),
            patch.object(yelo, "convert_axis_pulse", side_effect=lambda _axis, pulse: str(pulse)),
            patch.object(yelo, "move_with_control", side_effect=move_with_control),
            patch.object(yelo, "move_to_position"),
        ):
            yelo.main(
                stage=object(),
                home_position={"X": "0", "Y": "0", "Z": "0", "U": "0", "V": "0"},
                die_positions=_die_positions(),
                contact_z="10",
                capture_el=lambda die: captures.append(die) or True,
                die_config=die_config,
                start_die=start_die,
                focus_reference_score=100.0,
                get_focus_score=lambda die: focus_checks.append(die) or 100.0,
                refocus=lambda _die: True,
            )

        return moves, captures, focus_checks

    def test_die_config_filters_the_travel_order(self):
        moves, captures, focus_checks = self._run_sequence(
            die_config={"1": object(), "3": object()}
        )

        self.assertEqual(captures, ["1", "3"])
        self.assertEqual(focus_checks, ["1", "3"])
        self.assertEqual(len(moves), 6)

    def test_start_die_truncates_configured_travel_order(self):
        moves, captures, focus_checks = self._run_sequence(
            die_config={str(die): object() for die in range(1, 5)},
            start_die="3",
        )

        self.assertEqual(captures, ["3", "4", "2"])
        self.assertEqual(focus_checks, ["3", "4", "2"])
        self.assertEqual(moves[0]["Y"], "1")

    def test_invalid_start_die_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Starting die 2"):
            yelo.die_travel_order(
                4,
                die_config={"1": object(), "3": object()},
                start_die="2",
            )

    def test_omitting_die_config_preserves_standalone_behavior(self):
        _moves, captures, focus_checks = self._run_sequence()

        self.assertEqual(captures, ["1", "3", "4", "2"])
        self.assertEqual(focus_checks, ["1", "3", "4", "2"])

    def test_relative_pulse_targets_die_from_home(self):
        with patch.object(
            yelo,
            "get_pos",
            return_value={"X": "0", "Y": "0", "U": "0", "V": "0"},
        ):
            pulse = yelo._relative_pulse(
                object(),
                {"X": "0", "Y": "0", "U": "0", "V": "0"},
                {"X": "10", "Y": "20", "U": "3", "V": "4"},
            )

        self.assertEqual(pulse, {"X": "200", "Y": "2", "U": "3", "V": "10"})

    def test_relative_pulse_uses_current_controller_position(self):
        with patch.object(
            yelo,
            "get_pos",
            return_value={"X": "-100", "Y": "-1", "U": "-2", "V": "-5"},
        ):
            pulse = yelo._relative_pulse(
                object(),
                {"X": "0", "Y": "0", "U": "0", "V": "0"},
                {"X": "10", "Y": "20", "U": "3", "V": "4"},
            )

        self.assertEqual(pulse, {"X": "100", "Y": "1", "U": "1", "V": "5"})


if __name__ == "__main__":
    unittest.main()
