"""Plant database, planting rules and care profile tests. Standard library only."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import plant_fixture
from engine import plantdb
from engine.catalog import Catalog
from engine.planting_rules import care_profile, rules_for

RECORDS = plant_fixture.records()


class DatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.db = plant_fixture.build(Path(cls._dir.name))

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def test_get_returns_typed_record(self):
        tomato = plantdb.get(self.db, "tomato")
        self.assertEqual(tomato["common_name"], "Tomato")
        self.assertEqual(tomato["plant_weeks_min"], -8)
        self.assertIsInstance(tomato["plant_weeks_min"], int)
        self.assertEqual(tomato["usda_zone_max"], 11)
        self.assertIsNone(plantdb.get(self.db, "unobtainium"))

    def test_resolve_by_id_or_unambiguous_name(self):
        for text in ("tomato", "Tomato", "cherokee-purple", "Cherokee Purple Tomato"):
            record, candidates = plantdb.resolve(self.db, text)
            self.assertIsNotNone(record, text)
            self.assertEqual(candidates, [])
        self.assertEqual(plantdb.resolve(self.db, "white oak")[0]["slug"], "white-oak")

    def test_resolve_offers_candidates_when_unsure(self):
        record, candidates = plantdb.resolve(self.db, "False Rue Anemone")
        self.assertIsNone(record)
        self.assertEqual(
            {c["slug"] for c in candidates},
            {"enemion-biternatum", "false-rue-anemone-2"},
        )
        record, candidates = plantdb.resolve(self.db, "japanese maple")
        self.assertIsNone(record)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(plantdb.resolve(self.db, "unobtainium"), (None, []))

    def test_name_forms(self):
        self.assertEqual(
            plantdb.name_forms(" Peonies "), ["peonies", "peony", "peoni", "peonie"]
        )
        self.assertEqual(
            plantdb.name_forms("tomatoes")[:3], ["tomatoes", "tomato", "tomatoe"]
        )
        self.assertEqual(plantdb.name_forms("hosta"), ["hosta"])

    def test_search_matches_every_word_and_ranks_exact_first(self):
        names = [p["common_name"] for p in plantdb.search(self.db, "tomato")]
        self.assertEqual(names, ["Tomato", "Cherokee Purple Tomato"])
        found = plantdb.search(self.db, "maple coral")
        self.assertEqual([p["slug"] for p in found], ["coral-bark-japanese-maple"])
        self.assertEqual(
            plantdb.search(self.db, "quercus")[0]["slug"], "white-oak"
        )  # botanical name
        self.assertEqual(plantdb.search(self.db, ""), [])
        self.assertEqual(plantdb.search(self.db, "100%"), [])  # wildcards are literal
        self.assertEqual(len(plantdb.search(self.db, "a", limit=3)), 3)

    def test_search_can_be_limited_to_a_zone(self):
        self.assertTrue(plantdb.search(self.db, "desert rose"))
        self.assertEqual(plantdb.search(self.db, "desert rose", zone=7), [])
        self.assertTrue(plantdb.search(self.db, "desert rose", zone=10))

    def test_rejects_files_that_are_not_the_export(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "new.sqlite"
            wrong = Path(directory) / "wrong.csv"
            wrong.write_text("slug,common_name\ntomato,Tomato\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing columns"):
                plantdb.build_database(wrong, target)
            # The fixture is far smaller than a real export.
            with self.assertRaisesRegex(ValueError, "only 17 plants"):
                plantdb.build_database(plant_fixture.FIXTURE_CSV, target)
            self.assertFalse(target.exists())


class PlantingRulesTest(unittest.TestCase):
    def rules(self, slug):
        return {rule["key"]: rule for rule in rules_for(RECORDS[slug])}

    def test_indoor_start(self):
        rules = self.rules("tomato")
        self.assertEqual(list(rules), ["start", "plant", "harvest"])
        self.assertEqual(rules["start"]["offset_weeks"], -8)
        self.assertEqual(rules["start"]["window_weeks"], 2)
        self.assertIn("6-8 weeks", rules["start"]["detail"])
        self.assertEqual(rules["plant"]["offset_weeks"], 1)  # tender: after frost
        self.assertEqual(rules["harvest"]["after"], "plant")
        self.assertEqual(rules["harvest"]["days"], 60)

    def test_direct_sown_and_soil_workable(self):
        carrot = self.rules("carrot")
        self.assertEqual(list(carrot), ["sow", "harvest"])
        self.assertEqual(carrot["sow"]["window_weeks"], 5)
        self.assertEqual(carrot["harvest"]["after"], "sow")
        pea = self.rules("pea")
        self.assertEqual(
            (pea["sow"]["anchor"], pea["sow"]["offset_weeks"]), ("last_frost", -5)
        )

    def test_autumn_planted_bulb(self):
        garlic = self.rules("garlic")
        self.assertEqual(garlic["plant"]["anchor"], "first_frost")
        self.assertEqual(garlic["plant"]["kind"], "plant")
        self.assertEqual(garlic["harvest"]["days"], 240)

    def test_flowers_have_no_harvest(self):
        self.assertEqual(list(self.rules("zinnia")), ["sow"])

    def test_perennials_are_planted_once_and_never_harvested_by_maturity(self):
        apple = self.rules("apple")
        self.assertEqual(list(apple), ["plant"])
        self.assertTrue(apple["plant"]["until_planted"])
        self.assertEqual(apple["plant"]["anchor"], "last_frost")  # "spring"
        maple = self.rules("bloodgood-japanese-maple")
        self.assertEqual(maple["plant"]["anchor"], "first_frost")  # "fall"

    def test_every_fixture_plant_yields_valid_rules(self):
        for record in RECORDS.values():
            for rule in rules_for(record):
                self.assertIn("{name}", rule["title"])
                self.assertGreaterEqual(rule["window_weeks"], 1)


class CareProfileTest(unittest.TestCase):
    def test_matches(self):
        expected = {
            "apple": "apple",
            "montmorency-cherry": "peach",
            "bloodgood-japanese-maple": "japanese_maple",
            "coral-bark-japanese-maple": "japanese_maple",
            "white-oak": "deciduous_tree",
            "midnight-kentucky-bluegrass": "lawn",
            "hosta": "perennial",
            "enemion-biternatum": "perennial",
            "knock-out-rose": "rose",
            "desert-rose": None,  # a rose in name only
            "tomato": None,
            "garlic": None,
        }
        for slug, profile in expected.items():
            self.assertEqual(care_profile(RECORDS[slug]), profile, slug)

    def test_every_profile_it_can_return_exists(self):
        from engine import planting_rules

        profiles = Catalog.load().profiles
        named = {*planting_rules._BY_NAME.values(), "peach"}
        named |= {profile for _, profile in planting_rules._BY_TYPE}
        self.assertLessEqual(named, set(profiles))


if __name__ == "__main__":
    unittest.main()
