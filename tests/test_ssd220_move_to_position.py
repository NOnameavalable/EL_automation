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
    def _move_order(self, current_z: str, target_z: str) -> list[str]:
        target = {"X": "10", "Z": target_z, "Y": "20"}
        current = {"X": "0", "Z": current_z, "Y": "0"}

        with (
            patch.object(SSD220, "get_pos", return_value=current),
            patch.object(SSD220, "move", return_value={}) as move,
        ):
            SSD220.move_to_position(object(), target, read_position=False)

        pulse_to_target = move.call_args.args[1]
        return list(pulse_to_target)

    def test_retracts_z_before_other_axes(self):
        self.assertEqual(self._move_order(current_z="100", target_z="50"), ["Z", "X", "Y"])

    def test_lowers_z_after_other_axes(self):
        self.assertEqual(self._move_order(current_z="50", target_z="100"), ["X", "Y", "Z"])

    def test_unchanged_z_remains_last(self):
        self.assertEqual(self._move_order(current_z="100", target_z="100"), ["X", "Y", "Z"])


if __name__ == "__main__":
    unittest.main()
