"""Name matching tests. Standard library only."""

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parents[1] / "custom_components" / "master_gardener")
)

from engine.matching import is_a, plant_words, shorter_names


def plant(name):
    return {"common_name": name, "scientific_name": "X y"}


class MatchingTest(unittest.TestCase):
    def test_packet_words_are_dropped(self):
        self.assertEqual(plant_words("Organic Mustard Mix"), ["mustard"])
        self.assertEqual(plant_words("Mix"), ["mix"])  # nothing else to go by

    def test_shorter_names(self):
        self.assertEqual(
            shorter_names("Blush Cherry Tomato"), ["cherry tomato", "tomato"]
        )
        self.assertEqual(
            shorter_names("Wild Garden Mustard Mix"), ["garden mustard", "mustard"]
        )
        self.assertEqual(shorter_names("Chives"), [])

    def test_is_a(self):
        self.assertTrue(is_a(plant("Pumpkin"), "Lodi Pumpkin"))
        self.assertTrue(is_a(plant("Brown Mustard"), "Organic Mustard Mix"))
        self.assertTrue(is_a(plant("Kale"), "Wild Garden Kales"))  # plural
        self.assertFalse(is_a(plant("Shirley Poppy Mix"), "Organic Mustard Mix"))
        self.assertFalse(is_a(plant("Tomatillo"), "Blush Tomato"))


if __name__ == "__main__":
    unittest.main()
