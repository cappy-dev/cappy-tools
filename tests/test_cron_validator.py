import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "cron-validator"))
from cron_validator import HOUR_SPEC, parse_field


class ParseWrappedRangeTests(unittest.TestCase):
    def test_step_continues_across_wrapped_range_boundary(self):
        self.assertEqual(parse_field("22-2/3", HOUR_SPEC), {22, 1})

    def test_unstepped_wrapped_range_includes_both_sides(self):
        self.assertEqual(parse_field("22-2", HOUR_SPEC), {22, 23, 0, 1, 2})

    def test_step_on_ascending_range_keeps_start_anchor(self):
        self.assertEqual(parse_field("2-22/3", HOUR_SPEC), {2, 5, 8, 11, 14, 17, 20})


if __name__ == "__main__":
    unittest.main()
