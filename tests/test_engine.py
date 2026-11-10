"""Engine tests. Standard library only:  python -m unittest discover tests"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import plant_fixture
from engine.catalog import Catalog
from engine.climate import frost_dates_for_zone
from engine.models import (
    DEAD,
    DONE,
    DUE,
    OVERDUE,
    UPCOMING,
    Area,
    CustomTask,
    Planting,
    Site,
)
from engine.planting_rules import care_profile
from engine.schedule import build_tasks

CATALOG = Catalog.load()
SITE = Site(last_frost=(4, 15), first_frost=(10, 25))
VEG = Area(id="veg", name="Vegetable garden", style="vegetable")
LAWN = Area(id="lawn", name="Front lawn", style="lawn")
RECORDS = plant_fixture.records()


def planting(plant_id, slug, **kwargs):
    """A planting of a fixture plant, set up as the integration would."""
    record = RECORDS[slug]
    return Planting(
        id=plant_id,
        area_id=kwargs.pop("area_id", "veg"),
        name=record["common_name"],
        plant=slug,
        plant_data=record,
        profile=care_profile(record),
        **kwargs,
    )


def build(plantings=(), *, areas=(VEG,), today=date(2027, 1, 1), **kwargs):
    args = {
        "site": SITE,
        "areas": list(areas),
        "plantings": list(plantings),
        "custom_tasks": [],
        "completions": {},
        "last_done": {},
        "catalog": CATALOG,
        "start": date(2027, 1, 1),
        "end": date(2027, 12, 31),
        "today": today,
    }
    args.update(kwargs)
    return build_tasks(**args)


def by_kind(tasks, kind):
    return [t for t in tasks if t.kind == kind]


class CatalogTest(unittest.TestCase):
    def test_every_rule_is_well_formed(self):
        rule_sets = [p["rules"] for p in CATALOG.profiles.values()]
        rule_sets += [CATALOG.area_rules(s) for s in ("vegetable", "lawn", "meadow")]
        for rules in rule_sets:
            keys = [r["key"] for r in rules]
            self.assertEqual(len(keys), len(set(keys)))
            seen = set()
            for rule in rules:
                self.assertIn("{name}", rule["title"])
                if "every_days" in rule:
                    self.assertIn("until_anchor", rule)
                elif "after" in rule:
                    self.assertIn(rule["after"], seen, "must follow an earlier rule")
                else:
                    self.assertIn(rule["anchor"], ("last_frost", "first_frost"))
                seen.add(rule["key"])

    def test_profiles_never_reuse_the_planting_rule_keys(self):
        for profile in CATALOG.profiles.values():
            keys = {rule["key"] for rule in profile["rules"]}
            self.assertFalse(keys & {"start", "sow", "plant"})


class ClimateTest(unittest.TestCase):
    def test_frost_dates_from_zone(self):
        self.assertEqual(frost_dates_for_zone("7a"), ("04-10", "10-25"))
        self.assertEqual(frost_dates_for_zone("7b"), ("04-05", "10-30"))
        self.assertEqual(frost_dates_for_zone("7"), ("04-10", "10-25"))
        self.assertEqual(frost_dates_for_zone("13b"), frost_dates_for_zone("11b"))

    def test_every_zone_has_spring_before_autumn(self):
        for number in range(1, 14):
            for half in "ab":
                last, first = frost_dates_for_zone(f"{number}{half}")
                self.assertLess(last, first)

    def test_rejects_nonsense(self):
        for bad in ("", "0a", "14a", "7c", "warm"):
            with self.assertRaises(ValueError):
                frost_dates_for_zone(bad)


class VegetableCalendarTest(unittest.TestCase):
    def test_tomato_february_april_and_summer(self):
        tasks = build([planting("p1", "tomato")])
        start = by_kind(tasks, "sow_indoors")[0]
        plant = by_kind(tasks, "transplant")[0]
        harvest = by_kind(tasks, "harvest")[0]
        self.assertEqual(start.start, date(2027, 2, 18))  # 8 weeks before Apr 15
        self.assertEqual(plant.start, date(2027, 4, 22))
        self.assertEqual(harvest.start, plant.start + timedelta(days=60))
        self.assertEqual(start.title, "Start Tomato indoors")

    def test_planted_on_completes_planting_and_moves_harvest(self):
        planted = date(2027, 5, 1)
        tasks = build([planting("p1", "tomato", planted_on=planted)])
        self.assertEqual(by_kind(tasks, "sow_indoors"), [])  # already in the past
        self.assertEqual(by_kind(tasks, "transplant")[0].completed_on, planted)
        self.assertEqual(
            by_kind(tasks, "harvest")[0].start, planted + timedelta(days=60)
        )

    def test_garlic_harvest_lands_in_following_year(self):
        tasks = build([planting("g", "garlic")])
        harvest = by_kind(tasks, "harvest")[0]
        self.assertEqual(harvest.id, "planting:g:harvest:2026")
        self.assertEqual(harvest.start, date(2026, 9, 13) + timedelta(days=240))
        self.assertEqual(harvest.start.year, 2027)

    def test_dead_and_unknown_plantings_make_no_tasks(self):
        baseline = build()
        dead = planting("p1", "tomato", status=DEAD)
        unknown = Planting(id="p2", area_id="veg", name="Mystery shrub")
        orphan = planting("p3", "tomato", area_id="gone")
        self.assertEqual(build([dead, unknown, orphan]), baseline)

    def test_nothing_before_added_on(self):
        tasks = build([planting("p1", "tomato", added_on=date(2027, 6, 1))])
        self.assertEqual([t.kind for t in tasks if t.planting_id], ["harvest"])


class PerennialTest(unittest.TestCase):
    def test_planting_window_until_it_is_in_the_ground(self):
        tasks = build([planting("a", "apple")])
        plant = by_kind(tasks, "plant")[0]
        self.assertEqual(plant.title, "Plant Apple")
        self.assertEqual(plant.start, date(2027, 3, 18))  # 4 weeks before frost
        self.assertIn("bare-root", plant.detail)

        planted = build([planting("a", "apple", planted_on=date(2026, 3, 20))])
        self.assertEqual(by_kind(planted, "plant"), [])

        # Planted years ago on a date nobody knows: still no planting task,
        # but its upkeep goes on.
        old = build([planting("a", "apple", established=True)])
        self.assertEqual(by_kind(old, "plant"), [])
        self.assertTrue(by_kind(old, "prune"))

    def test_upkeep_comes_from_the_care_profile(self):
        tasks = build([planting("a", "apple", planted_on=date(2025, 3, 20))])
        self.assertEqual(
            sorted({t.kind for t in tasks if t.planting_id}),
            ["fertilize", "harvest", "prune", "thin"],
        )
        self.assertEqual(by_kind(tasks, "prune")[0].title, "Dormant-prune Apple")

    def test_profile_alone_is_enough(self):
        lawn = Planting(id="l", area_id="veg", name="Back lawn", profile="lawn")
        self.assertTrue(by_kind(build([lawn]), "mow"))


class StateTest(unittest.TestCase):
    def test_states_and_completion(self):
        tomato = planting("p1", "tomato")
        start = by_kind(build([tomato]), "sow_indoors")[0]
        self.assertEqual(start.state(date(2027, 2, 1)), UPCOMING)
        self.assertEqual(start.state(date(2027, 2, 20)), DUE)
        self.assertEqual(start.state(date(2027, 3, 10)), OVERDUE)
        done = build([tomato], completions={start.id: date(2027, 2, 21)})
        self.assertEqual(by_kind(done, "sow_indoors")[0].state(date(2027, 3, 10)), DONE)

    def test_custom_task(self):
        custom = CustomTask(
            id="c1", area_id="veg", title="Fix fence", due=date(2027, 5, 5)
        )
        task = next(t for t in build(custom_tasks=[custom]) if t.custom)
        self.assertEqual(
            (task.id, task.start, task.end),
            ("custom:c1", date(2027, 5, 5), date(2027, 5, 5)),
        )
        self.assertEqual(task.state(date(2027, 5, 6)), OVERDUE)


class RepeatingTaskTest(unittest.TestCase):
    def mows(self, **kwargs):
        return by_kind(build(areas=(LAWN,), **kwargs), "mow")

    def test_one_real_task_then_projections(self):
        mows = self.mows()
        self.assertEqual(mows[0].start, date(2027, 3, 25))  # 3 weeks before frost
        self.assertFalse(mows[0].projected)
        self.assertTrue(all(m.projected for m in mows[1:]))
        self.assertEqual(mows[1].start - mows[0].start, timedelta(days=7))
        self.assertLessEqual(mows[-1].start, date(2027, 11, 8))

    def test_first_seen_mid_season_is_due_today(self):
        today = date(2027, 6, 10)
        first = self.mows(today=today)[0]
        self.assertEqual(first.start, today)
        self.assertEqual(first.state(today), DUE)

    def test_completion_rolls_forward_and_lateness_is_overdue(self):
        done = date(2027, 6, 10)
        first = self.mows(today=date(2027, 6, 11), last_done={"area:lawn:mow": done})[0]
        self.assertEqual(first.start, date(2027, 6, 17))
        self.assertEqual(first.state(date(2027, 6, 30)), OVERDUE)

    def test_no_mowing_out_of_season(self):
        self.assertEqual(self.mows(today=date(2027, 12, 1)), [])


if __name__ == "__main__":
    unittest.main()
