"""Soil care rule tests. Standard library only."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parents[1] / "custom_components" / "master_gardener")
)

from engine.care import (
    default_dry_below,
    default_feed_below,
    feed_advice,
    water_advice,
)
from engine.models import Site

SITE = Site(last_frost=(4, 15), first_frost=(10, 25))
JUNE = date(2027, 6, 10)


def feed(conductivity, moisture=(40,), today=JUNE, **kwargs):
    args = {"feed_below": 350, "dry_below": 30, "site": SITE, "today": today}
    args.update(kwargs)
    return feed_advice(list(conductivity), list(moisture), **args)


class WaterTest(unittest.TestCase):
    def test_no_reading_no_advice(self):
        self.assertIsNone(water_advice([], 30))

    def test_driest_sensor_decides(self):
        advice = water_advice([45, 18, 33], 30)
        self.assertTrue(advice.needed)
        self.assertEqual((advice.reading, advice.threshold), (18, 30))
        self.assertEqual(advice.reason, "Soil moisture 18% is below 30%")
        self.assertFalse(water_advice([45, 31], 30).needed)

    def test_must_recover_past_the_threshold_to_clear(self):
        self.assertFalse(water_advice([32], 30).needed)
        still = water_advice([32], 30, was_needed=True)
        self.assertTrue(still.needed)
        self.assertIn("not recovered", still.reason)
        self.assertFalse(water_advice([35], 30, was_needed=True).needed)


class FeedTest(unittest.TestCase):
    def test_low_fertility_in_season(self):
        advice = feed([400, 210])
        self.assertTrue(advice.needed)
        self.assertEqual(advice.reading, 210)
        self.assertFalse(feed([400, 360]).needed)

    def test_no_reading_or_never_fed(self):
        self.assertIsNone(feed([]))
        self.assertIsNone(feed([50], feed_below=0))

    def test_only_in_feeding_season(self):
        for today in (date(2027, 3, 1), date(2027, 9, 20), date(2027, 12, 1)):
            advice = feed([100], today=today)
            self.assertFalse(advice.needed)
            self.assertEqual(advice.reason, "Outside the feeding season")
        self.assertTrue(feed([100], today=date(2027, 4, 2)).needed)
        self.assertTrue(feed([100], today=date(2027, 9, 13)).needed)

    def test_dry_soil_is_watered_before_it_is_judged(self):
        advice = feed([100], moisture=[12])
        self.assertFalse(advice.needed)
        self.assertIn("water first", advice.reason)
        self.assertTrue(feed([100], moisture=[]).needed)  # no moisture sensor

    def test_must_recover_past_the_threshold_to_clear(self):
        self.assertFalse(feed([360]).needed)
        self.assertTrue(feed([360], was_needed=True).needed)
        self.assertFalse(feed([400], was_needed=True).needed)


class DefaultsTest(unittest.TestCase):
    def test_style_defaults(self):
        self.assertGreater(
            default_dry_below("vegetable"), default_dry_below("mediterranean")
        )
        self.assertEqual(default_feed_below("meadow"), 0)
        self.assertEqual(default_dry_below("unheard-of"), default_dry_below("other"))
        self.assertEqual(default_feed_below("unheard-of"), default_feed_below("other"))


if __name__ == "__main__":
    unittest.main()
