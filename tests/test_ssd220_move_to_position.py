import sys
import types
import unittest
from unittest.mock import patch


try:
    import pyvisa  # noqa: F401
except ModuleNotFoundError:
    pyvisa = types.ModuleType("pyvisa")
    resources = types.ModuleType("pyvisa.resources")
    resources.MessageBasedResource = object
    pyvisa.resources = resources
    sys.modules.update({"pyvisa": pyvisa, "pyvisa.resources": resources})

import SSD220


class MoveToPositionOrderingTests(unittest.TestCase):
    def _run_move(self, current, target, *, home_z="0", wait=True):
        positions = {axis: "0" for axis in SSD220.AXES}
        positions.update(current)
        events = []

        def get_pos(_inst, axes=None):
            return {axis: positions[axis] for axis in (SSD220.AXES if axes is None else axes)}

        def move_axis(_inst, axis, pulse, *_speeds):
            if int(pulse) == 0:
                return False
            positions[axis] = str(int(positions[axis]) + int(pulse))
            events.append(("start", axis, positions[axis]))
            return True

        def motion_wait(_inst, axis, **_kwargs):
            events.append(("complete", axis, positions[axis]))

        with (
            patch.object(SSD220, "get_pos", side_effect=get_pos),
            patch.object(SSD220, "_move_axis", side_effect=move_axis),
            patch.object(SSD220, "_mot_wait", side_effect=motion_wait),
        ):
            result = SSD220.move_to_position(object(), target, home_z=home_z, wait=wait)
        return result, events

    def _move_order(self, current_z: str, target_z: str) -> list[str]:
        _result, events = self._run_move(
            {"X": "0", "Z": current_z, "Y": "0"},
            {"X": "10", "Z": target_z, "Y": "20"},
        )
        return [axis for event, axis, _position in events if event == "start"]

    def test_retracts_z_before_other_axes(self):
        self.assertEqual(self._move_order(current_z="100", target_z="50"), ["Z", "X", "Y"])

    def test_lowers_z_after_other_axes(self):
        self.assertEqual(self._move_order(current_z="50", target_z="100"), ["X", "Y", "Z"])

    def test_unchanged_z_at_home_remains_last(self):
        self.assertEqual(self._move_order(current_z="0", target_z="0"), ["X", "Y"])

    def test_unchanged_z_not_at_home_retracts_first(self):
        self.assertEqual(self._move_order(current_z="100", target_z="100"), ["Z", "X", "Y", "Z"])

    def test_custom_home_retracts_then_restores_original_z(self):
        target = {"X": "10", "Z": "100"}
        result, events = self._run_move({"X": "0", "Z": "100"}, target, home_z="50")
        self.assertEqual([e for e in events if e[0] == "start"], [
            ("start", "Z", "50"), ("start", "X", "10"), ("start", "Z", "100")
        ])
        self.assertEqual(result["Z"], "100")
        self.assertEqual(target, {"X": "10", "Z": "100"})

    def test_at_custom_home_only_lateral_axes_move(self):
        result, events = self._run_move(
            {"X": "0", "Z": "50"}, {"X": "10", "Z": "50"}, home_z="50"
        )
        self.assertEqual([axis for event, axis, _ in events if event == "start"], ["X"])
        self.assertEqual(result["Z"], "50")

    def test_repeated_contact_is_a_noop(self):
        result, events = self._run_move({"Z": "100"}, {"Z": "100"}, home_z="50")
        self.assertEqual(events, [])
        self.assertEqual(result["Z"], "100")

    def test_unchanged_multi_axis_target_is_a_noop(self):
        position = {"X": "10", "Z": "100"}
        _result, events = self._run_move(position, position, home_z="50")
        self.assertEqual(events, [])

    def test_wait_false_still_waits_for_retraction_and_lateral_phases(self):
        _result, events = self._run_move(
            {"X": "0", "Z": "100"}, {"X": "10", "Z": "100"}, home_z="50", wait=False
        )
        self.assertEqual(events, [
            ("start", "Z", "50"), ("complete", "Z", "50"),
            ("start", "X", "10"), ("complete", "X", "10"),
            ("start", "Z", "100"),
        ])

    def test_wait_false_retraction_finishes_before_lateral_start(self):
        _result, events = self._run_move(
            {"X": "0", "Z": "100"}, {"X": "10", "Z": "50"}, wait=False
        )
        self.assertEqual(events, [
            ("start", "Z", "50"), ("complete", "Z", "50"), ("start", "X", "10")
        ])

    def test_wait_false_lateral_move_finishes_before_descent(self):
        _result, events = self._run_move(
            {"X": "0", "Z": "50"}, {"X": "10", "Z": "100"}, wait=False
        )
        self.assertEqual(events, [
            ("start", "X", "10"), ("complete", "X", "10"), ("start", "Z", "100")
        ])

    def test_stop_prevents_later_phases(self):
        for outcomes, expected_axes in (([False], [["Z"]]), ([True, False], [["Z"], ["X"]])):
            with self.subTest(outcomes=outcomes):
                with (
                    patch.object(SSD220, "get_pos", return_value={"X": "0", "Z": "100"}),
                    patch.object(SSD220, "move_with_control", side_effect=outcomes) as movement,
                ):
                    result = SSD220.move_to_position(
                        object(), {"X": "10", "Z": "100"}, home_z="50",
                        stop_requested=lambda: False, read_position=False,
                    )
                self.assertEqual(result, {})
                self.assertEqual([list(call.args[1]) for call in movement.call_args_list], expected_axes)

    def test_lateral_only_request_reaches_target(self):
        result, events = self._run_move({"X": "0"}, {"X": "10"}, wait=False)
        self.assertEqual(result["X"], "10")
        self.assertEqual(events, [("start", "X", "10")])


if __name__ == "__main__":
    unittest.main()
