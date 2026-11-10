"""Build plan tests. Standard library only."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import plant_fixture
from engine.models import Site
from engine.plan import (
    HARDSCAPE,
    HIGH,
    KIND_PROJECT,
    LOW,
    PERENNIALS,
    SMALL_PLANTS,
    TREES_SHRUBS,
    PlanItem,
    build_plan,
    buy_window,
    plan_task,
    planting_tier,
)
from engine.planting_rules import care_profile

SITE = Site(last_frost=(4, 15), first_frost=(10, 25))
RECORDS = plant_fixture.records()
YEAR = 2027


def item(name, tier=PERENNIALS, cost=100.0, area="a", **extra):
    return PlanItem(id=name, area_id=area, name=name, tier=tier, cost=cost, **extra)


def years(plan):
    """{item name: year}"""
    return {p.item.name: y.year for y in plan for p in y.placements}


def plan(items, budget=500.0, **kwargs):
    return build_plan(items, this_year=YEAR, budget=budget, **kwargs)


class TierTest(unittest.TestCase):
    def tier(self, slug):
        record = RECORDS[slug]
        return planting_tier(record, care_profile(record))

    def test_trees_and_shrubs(self):
        for slug in ("apple", "white-oak", "knock-out-rose", "montmorency-cherry"):
            self.assertEqual(self.tier(slug), TREES_SHRUBS, slug)

    def test_perennials_and_small_plants(self):
        self.assertEqual(self.tier("hosta"), PERENNIALS)
        self.assertEqual(self.tier("enemion-biternatum"), PERENNIALS)
        self.assertEqual(self.tier("zinnia"), SMALL_PLANTS)
        self.assertEqual(self.tier("tomato"), SMALL_PLANTS)

    def test_by_profile_or_default(self):
        self.assertEqual(planting_tier(None, "conifer"), TREES_SHRUBS)
        self.assertEqual(planting_tier(None, None), PERENNIALS)


class PlanTest(unittest.TestCase):
    def test_what_fits_this_year_stays_this_year(self):
        (year,) = plan([item("a"), item("b")])
        self.assertEqual(year.year, YEAR)
        self.assertEqual(year.planned, 200)
        self.assertEqual(year.remaining, 300)
        self.assertEqual(year.placements[0].reason, "")

    def test_budget_spills_into_later_years(self):
        result = plan([item(n, cost=200) for n in "abcde"])
        self.assertEqual(
            years(result),
            {"a": YEAR, "b": YEAR, "c": YEAR + 1, "d": YEAR + 1, "e": 2029},
        )
        self.assertEqual(
            result[1].placements[0].reason, f"No room in the budget before {YEAR + 1}"
        )

    def test_spending_this_year_counts(self):
        result = plan([item("a", cost=300)], spent=250)
        self.assertEqual(years(result), {"a": YEAR + 1})
        self.assertEqual(result[0].spent, 250)
        self.assertEqual(result[0].remaining, 250)
        self.assertEqual(result[1].spent, 0)

    def test_leftover_money_goes_to_what_fits(self):
        result = plan(
            [item("big", cost=400), item("big2", cost=400), item("s", cost=50)]
        )
        self.assertEqual(years(result), {"big": YEAR, "big2": YEAR + 1, "s": YEAR})

    def test_structure_goes_in_first_within_an_area(self):
        result = plan(
            [
                item("hosta", cost=50, added_on=date(2026, 1, 1)),
                item("path", HARDSCAPE, cost=300),
                item("oak", TREES_SHRUBS, cost=400),
            ]
        )
        self.assertEqual(
            years(result), {"oak": YEAR, "path": YEAR + 1, "hosta": YEAR + 1}
        )
        hosta = result[1].placements[-1]
        self.assertEqual(hosta.item.name, "hosta")
        self.assertEqual(hosta.reason, "After path")

    def test_areas_do_not_hold_each_other_up(self):
        result = plan(
            [
                item("oak", TREES_SHRUBS, cost=400, area="woods"),
                item("hosta", cost=50, area="border"),
            ]
        )
        self.assertEqual(years(result), {"oak": YEAR, "hosta": YEAR})

    def test_priority_orders_the_property(self):
        result = plan(
            [
                item("first", cost=400, area="x"),
                item("urgent", cost=400, area="y", priority=HIGH),
                item("whenever", cost=50, area="z", priority=LOW),
            ]
        )
        self.assertEqual(
            years(result), {"urgent": YEAR, "first": YEAR + 1, "whenever": YEAR}
        )

    def test_an_urgent_item_pulls_its_prerequisites_forward(self):
        result = plan(
            [
                item("elsewhere", TREES_SHRUBS, cost=400, area="x"),
                item("maple", TREES_SHRUBS, cost=300, area="y"),
                item("fern", cost=100, area="y", priority=HIGH),
            ]
        )
        self.assertEqual(
            years(result), {"maple": YEAR, "fern": YEAR, "elsewhere": YEAR + 1}
        )

    def test_not_before(self):
        result = plan([item("patio", HARDSCAPE, not_before=2030), item("bed")])
        self.assertEqual(years(result), {"patio": 2030, "bed": 2030})
        reasons = {p.item.name: p.reason for y in result for p in y.placements}
        self.assertEqual(reasons, {"patio": "Not before 2030", "bed": "After patio"})
        self.assertEqual([y.year for y in result], [YEAR, 2030])

    def test_too_dear_for_a_year_gets_one_to_itself(self):
        result = plan(
            [item("shrub", TREES_SHRUBS, cost=100), item("pond", HARDSCAPE, 1200, "b")]
        )
        self.assertEqual(years(result), {"shrub": YEAR, "pond": YEAR + 1})
        pond = result[1].placements[0]
        self.assertTrue(pond.over_budget)
        self.assertEqual(result[1].remaining, -700)

    def test_without_a_budget_everything_is_this_year(self):
        for budget in (None, 0):
            result = plan(
                [item("pond", HARDSCAPE, 5000), item("fern"), item("x", cost=None)],
                budget=budget,
            )
            self.assertEqual(len(result), 1)
            self.assertIsNone(result[0].remaining)
            self.assertEqual(result[0].unpriced, 1)

    def test_unpriced_items_take_no_budget(self):
        result = plan([item("a", cost=500), item("b", cost=None)])
        self.assertEqual(years(result), {"a": YEAR, "b": YEAR})

    def test_empty(self):
        (year,) = plan([])
        self.assertEqual((year.year, year.planned, year.remaining), (YEAR, 0, 500))


class BuyWindowTest(unittest.TestCase):
    def test_before_the_planting_window_opens(self):
        # Hosta: plant from the last frost (April 15).
        self.assertEqual(
            buy_window(RECORDS["hosta"], SITE, YEAR),
            (date(2027, 3, 4), date(2027, 4, 14)),
        )
        # Japanese maple: plant in fall, 8 weeks before first frost (Aug 30).
        _, end = buy_window(RECORDS["bloodgood-japanese-maple"], SITE, YEAR)
        self.assertEqual(end, date(2027, 8, 29))

    def test_an_early_window_belongs_to_next_season(self):
        early = Site(last_frost=(2, 10), first_frost=(12, 1))
        start, end = buy_window(RECORDS["tomato"], early, YEAR)  # sow 8 weeks before
        self.assertEqual(end.year, YEAR)
        self.assertGreater(start, date(2027, 6, 1))

    def test_projects_and_unknown_plants_take_the_season(self):
        self.assertEqual(
            buy_window(None, SITE, YEAR), (date(2027, 3, 18), date(2027, 10, 25))
        )

    def test_task(self):
        task = plan_task(
            item("Hosta", cost=45),
            record=RECORDS["hosta"],
            quantity=3,
            site=SITE,
            year=YEAR,
        )
        self.assertEqual(task.id, "plan:Hosta:2027")
        self.assertEqual(task.title, "Buy Hosta")
        self.assertEqual(task.kind, "buy")
        self.assertEqual(task.planting_id, "Hosta")
        self.assertEqual(task.detail, "In this year's build plan: 3 Hosta, about 45.")
        path = plan_task(
            item("Lay the flagstone path", HARDSCAPE, None, kind=KIND_PROJECT),
            record=None,
            quantity=1,
            site=SITE,
            year=YEAR,
        )
        self.assertEqual(path.title, "Lay the flagstone path")
        self.assertEqual(path.kind, "project")
        self.assertIsNone(path.planting_id)
        self.assertEqual(
            path.detail, "In this year's build plan: Lay the flagstone path."
        )


if __name__ == "__main__":
    unittest.main()
