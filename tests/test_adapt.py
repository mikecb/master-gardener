"""Adaptive maintenance tests. Standard library only."""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parents[1] / "custom_components" / "master_gardener")
)

from engine.adapt import (
    EARLY,
    LATE,
    ON_TIME,
    REPORT,
    Adjustment,
    Signal,
    adjust,
    describe,
    interval,
    reported,
)

TODAY = date(2027, 6, 1)


def signal(days_ago, kind, note=""):
    return Signal(TODAY - timedelta(days=days_ago), kind, note)


class AdaptTest(unittest.TestCase):
    def test_reports_name_the_job(self):
        self.assertTrue(reported("mow", "Grass too long"))
        self.assertTrue(reported("mow", "the lawn is a meadow"))
        self.assertTrue(reported("deadhead", "Roses full of spent blooms"))
        self.assertFalse(reported("mow", "Weeds in bed 3"))
        self.assertFalse(reported("deadhead", "Grass too long"))
        self.assertFalse(reported("prune", "anything"))

    def test_two_complaints_tighten(self):
        adjustment, left = adjust(
            None, [signal(20, REPORT, "Grass too long"), signal(2, REPORT)], TODAY
        )
        self.assertEqual(adjustment, Adjustment(0.85, "reported 2 times"))
        self.assertEqual(left, [])  # used up: one step per bad spell
        self.assertEqual(interval(7, adjustment), 6)
        self.assertEqual(
            describe(7, adjustment), "Every 6 days, usually 7: reported 2 times."
        )

    def test_early_counts_as_sooner(self):
        adjustment, _ = adjust(None, [signal(9, EARLY), signal(1, REPORT)], TODAY)
        self.assertEqual(adjustment.reason, "reported 1 time and done early 1 time")

    def test_one_signal_or_old_ones_do_nothing(self):
        current = Adjustment(0.85, "reported 2 times")
        self.assertEqual(adjust(current, [signal(1, REPORT)], TODAY)[0], current)
        old = [signal(90, REPORT), signal(1, REPORT)]
        adjustment, left = adjust(None, old, TODAY)
        self.assertIsNone(adjustment)
        self.assertEqual(left, [signal(1, REPORT)])  # the old one is dropped

    def test_late_three_times_running_loosens(self):
        late = [signal(30, LATE), signal(15, LATE), signal(1, LATE)]
        adjustment, left = adjust(None, late, TODAY)
        self.assertEqual(adjustment, Adjustment(1.15, "done late 3 times running"))
        self.assertEqual(interval(14, adjustment), 16)
        self.assertEqual(left, [])
        broken = [signal(30, LATE), signal(15, ON_TIME), signal(1, LATE)]
        self.assertIsNone(adjust(None, broken, TODAY)[0])

    def test_bounds(self):
        tight = Adjustment(0.52, "x")
        adjustment, _ = adjust(tight, [signal(2, REPORT), signal(1, REPORT)], TODAY)
        self.assertEqual(adjustment.factor, 0.5)
        loose = Adjustment(1.9, "x")
        late = [signal(3, LATE), signal(2, LATE), signal(1, LATE)]
        self.assertEqual(adjust(loose, late, TODAY)[0].factor, 2.0)
        self.assertEqual(interval(1, Adjustment(0.5, "x")), 1)  # never below a day

    def test_round_trip_and_no_note_when_unchanged(self):
        s = Signal(TODAY, REPORT, "Grass too long")
        self.assertEqual(Signal.from_list(s.to_list()), s)
        self.assertEqual(describe(7, None), "")
        self.assertEqual(describe(7, Adjustment(1.05, "x")), "")  # still 7 days


if __name__ == "__main__":
    unittest.main()
