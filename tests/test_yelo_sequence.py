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


class DieLayoutReferenceTests(unittest.TestCase):
    def setUp(self):
        self.layout = yelo.DieLayout(
            dies_per_row=16, dies_per_group=4, die_spacing=9000,
            group_gap=12500, row_spacing=32500,
            second_row_die_upside_down_offset=5000,
            second_row_u_row_offset=5000, second_row_center_offset=250,
        )

    def test_default_reference_preserves_original_layout(self):
        positions = self.layout.die_positions()
        self.assertEqual(positions, self.layout.die_positions("1"))
        self.assertEqual(list(positions), [str(die) for die in range(1, 33)])
        self.assertEqual(positions["1"], {"X": "0", "Y": "0", "U": "0", "V": "0"})
        self.assertEqual(positions["9"], {"X": "0", "Y": "48500", "U": "0", "V": "0"})
        self.assertEqual(positions["32"], {"X": "37500", "Y": "172750", "U": "5250", "V": "5000"})

    def test_reference_three_keeps_earlier_dies_and_original_ids(self):
        positions = self.layout.die_positions("3")
        self.assertEqual(len(positions), 32)
        self.assertEqual(positions["3"], {"X": "0", "Y": "0", "U": "0", "V": "0"})
        self.assertEqual(positions["1"], {"X": "0", "Y": "-9000", "U": "0", "V": "0"})
        self.assertEqual(positions["5"], {"X": "0", "Y": "9000", "U": "0", "V": "0"})

    def test_second_row_reference_reverses_offsets_and_preserves_group_gap(self):
        positions = self.layout.die_positions("10")
        self.assertEqual(positions["10"], {"X": "0", "Y": "0", "U": "0", "V": "0"})
        self.assertEqual(positions["1"], {"X": "-37500", "Y": "-48750", "U": "-5250", "V": "-5000"})
        self.assertEqual(positions["8"], {"X": "0", "Y": "-21500", "U": "0", "V": "0"})
        self.assertEqual(positions["9"], {"X": "-37500", "Y": "-250", "U": "-5250", "V": "-5000"})

    def test_invalid_reference_is_rejected(self):
        for reference_die in ("", "0", "33", "bad"):
            with self.subTest(reference_die=reference_die):
                with self.assertRaisesRegex(ValueError, "Invalid reference die"):
                    self.layout.die_positions(reference_die)


class YeloConfiguredDieTests(unittest.TestCase):
    def _run_sequence(self, *, die_config=None, start_die=None, home_z="0", die_positions=None):
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
            patch.object(yelo, "move_to_position") as home_move,
            patch.object(yelo.time, "sleep"),
        ):
            yelo.main(
                stage=object(),
                home_position={"X": "0", "Y": "0", "Z": home_z, "U": "0", "V": "0"},
                die_positions=_die_positions() if die_positions is None else die_positions,
                contact_z="10",
                capture_el=lambda die: captures.append(die) or True,
                die_config=die_config,
                start_die=start_die,
                focus_reference_score=100.0,
                get_focus_score=lambda die: focus_checks.append(die) or 100.0,
                refocus=lambda _die: True,
            )

        self.assertEqual(home_move.call_args.kwargs["home_z"], home_z)

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

    def test_reference_die_does_not_filter_or_change_starting_die(self):
        positions = yelo.DieLayout(
            dies_per_row=2, dies_per_group=2, die_spacing=10,
            group_gap=0, row_spacing=10,
        ).die_positions("3")
        moves, captures, _focus_checks = self._run_sequence(
            die_positions=positions, start_die="1",
        )
        self.assertEqual(captures, ["1", "3", "4", "2"])
        self.assertEqual(moves[0]["Y"], "-1")
        moves, captures, _focus_checks = self._run_sequence(
            die_positions=positions, start_die="3",
        )
        self.assertEqual(captures, ["3", "4", "2"])
        self.assertEqual(moves[0], {"X": "0", "Y": "0", "U": "0", "V": "0"})

    def test_omitting_die_config_preserves_standalone_behavior(self):
        _moves, captures, focus_checks = self._run_sequence()

        self.assertEqual(captures, ["1", "3", "4", "2"])
        self.assertEqual(focus_checks, ["1", "3", "4", "2"])

    def test_return_home_uses_recorded_nonzero_home_z(self):
        self._run_sequence(die_config={"1": object()}, home_z="50")

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
