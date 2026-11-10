"""Seed-on-hand tests. Standard library only."""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import plant_fixture
from engine.catalog import Catalog
from engine.models import Area, Planting, Site
from engine.planting_rules import care_profile
from engine.schedule import build_tasks
from engine.seeds import (
    ORDER_KIND,
    SeedItem,
    apply_seed,
    matches,
    next_window,
    parse_items,
    pick_area,
    proposals,
    stock,
)

RECORDS = plant_fixture.records()
SITE = Site(last_frost=(4, 15), first_frost=(10, 25))
TODAY = date(2027, 1, 10)


def planting(slug, name=None, **extra):
    record = RECORDS[slug]
    return Planting(
        id=slug,
        area_id="veg",
        name=name or record["common_name"],
        plant=slug,
        plant_data=record,
        profile=care_profile(record),
        added_on=date(2026, 12, 1),
        **extra,
    )


def seed(name, slug=None, **extra):
    return SeedItem(name=name, record=RECORDS[slug] if slug else None, **extra)


def tasks_for(*plantings):
    return build_tasks(
        site=SITE,
        areas=[Area(id="veg", name="Veg", style="vegetable")],
        plantings=list(plantings),
        custom_tasks=[],
        completions={},
        last_done={},
        catalog=Catalog.load(),
        start=date(2027, 1, 1),
        end=date(2027, 12, 31),
        today=TODAY,
    )


class ParseTest(unittest.TestCase):
    def test_simple_inventory_items(self):
        items = parse_items(
            {
                "items": [
                    {
                        "name": " Sungold tomato ",
                        "quantity": 2,
                        "unit": "packets",
                        "expiry_date": "2028-12-31",
                        "aliases": ["sun gold"],
                    },
                    {"name": "Carrot", "quantity": "lots", "expiry_date": ""},
                    {"name": ""},
                    "junk",
                ]
            }
        )
        self.assertEqual(
            items,
            [
                SeedItem(
                    "Sungold tomato", 2.0, "packets", date(2028, 12, 31), ("sun gold",)
                ),
                SeedItem("Carrot"),
            ],
        )
        self.assertEqual(items[0].describe(), "Sungold tomato (2 packets)")
        self.assertEqual(items[1].describe(), "Carrot")
        self.assertEqual(parse_items(None), [])


class MatchTest(unittest.TestCase):
    def test_a_species_takes_any_seed_of_it(self):
        tomato = planting("tomato")
        self.assertTrue(matches(seed("Cherokee Purple", "cherokee-purple"), tomato))
        self.assertTrue(matches(seed("Sungold tomatoes"), tomato))  # by name
        self.assertFalse(matches(seed("Tomatillo"), tomato))
        self.assertFalse(matches(seed("Danvers carrot"), tomato))

    def test_a_variety_wants_that_variety(self):
        cherokee = planting("cherokee-purple")
        self.assertFalse(matches(seed("Tomato", "tomato"), cherokee))
        self.assertFalse(matches(seed("Sungold tomato"), cherokee))
        self.assertTrue(matches(seed("Cherokee Purple", "cherokee-purple"), cherokee))
        self.assertTrue(matches(seed("cherokee purple tomato seed"), cherokee))

    def test_aliases_and_own_name(self):
        carrot = planting("carrot", name="Bed 3 carrots")
        self.assertTrue(matches(seed("Danvers", aliases=("carrot",)), carrot))
        self.assertTrue(matches(seed("bed 3 carrots"), carrot))

    def test_stock(self):
        tomato = planting("tomato")
        items = [
            seed("Sungold tomato", quantity=1),
            seed("Old tomato", expiry=date(2026, 1, 1)),
            seed("Used tomato", quantity=0),
            seed("Carrot"),
        ]
        usable, expired = stock(items, tomato, TODAY)
        self.assertEqual([i.name for i in usable], ["Sungold tomato"])
        self.assertEqual([i.name for i in expired], ["Old tomato"])


class ApplyTest(unittest.TestCase):
    def apply(self, plantings, items, completions=None):
        return apply_seed(
            tasks_for(*plantings),
            {p.id: p for p in plantings},
            items,
            completions or {},
            TODAY,
        )

    def test_seed_on_hand_is_named_on_the_sowing(self):
        tasks = self.apply([planting("tomato")], [seed("Sungold tomato", quantity=2)])
        (sow,) = [t for t in tasks if t.kind == "sow_indoors"]
        self.assertTrue(sow.detail.endswith("Seed on hand: Sungold tomato (2)."))
        self.assertFalse([t for t in tasks if t.kind == ORDER_KIND])

    def test_get_seed_before_the_window_when_there_is_none(self):
        tasks = self.apply([planting("tomato"), planting("carrot")], [seed("Carrot")])
        (order,) = [t for t in tasks if t.kind == ORDER_KIND]
        (sow,) = [t for t in tasks if t.kind == "sow_indoors"]
        self.assertEqual(order.id, "seed:tomato:2027")
        self.assertEqual(order.title, "Get seed for Tomato")
        self.assertEqual(order.start, sow.start - timedelta(weeks=6))
        self.assertEqual(order.end, sow.start - timedelta(days=1))
        self.assertEqual(order.planting_id, "tomato")
        self.assertIn("No seed for Tomato", order.detail)
        self.assertEqual(tasks, sorted(tasks, key=lambda t: (t.start, t.end, t.title)))

    def test_expired_seed_is_named(self):
        tasks = self.apply(
            [planting("tomato")], [seed("Old tomato", expiry=date(2025, 12, 31))]
        )
        (order,) = [t for t in tasks if t.kind == ORDER_KIND]
        self.assertIn("past its date: Old tomato (expired 2025-12-31)", order.detail)

    def test_completed_order_stays_done(self):
        tasks = self.apply(
            [planting("tomato")], [], completions={"seed:tomato:2027": TODAY}
        )
        (order,) = [t for t in tasks if t.kind == ORDER_KIND]
        self.assertEqual(order.completed_on, TODAY)

    def test_nothing_for_what_is_planted_or_past_or_not_sown(self):
        planted = planting("tomato", planted_on=date(2027, 5, 1))
        tasks = self.apply([planted, planting("apple")], [])
        self.assertFalse([t for t in tasks if t.kind == ORDER_KIND])
        # Late in the year the spring sowing is over: nothing to order.
        late = apply_seed(
            tasks_for(planting("tomato")),
            {"tomato": planting("tomato")},
            [],
            {},
            date(2027, 9, 1),
        )
        self.assertFalse([t for t in late if t.kind == ORDER_KIND])


class ProposalTest(unittest.TestCase):
    AREAS = (("veg", "vegetable"), ("front", "cottage"), ("fruit", "orchard"))

    def propose(self, items, plantings=(), today=TODAY, areas=None):
        return proposals(items, list(plantings), list(areas or self.AREAS), SITE, today)

    def test_next_window(self):
        # Tomato: start indoors 8 weeks before the April 15 last frost.
        self.assertEqual(
            next_window(RECORDS["tomato"], SITE, TODAY),
            (date(2027, 2, 18), date(2027, 3, 3)),
        )
        # Once this year's window has closed, next year's.
        self.assertEqual(
            next_window(RECORDS["tomato"], SITE, date(2027, 6, 1))[0],
            date(2028, 2, 19),  # a leap year
        )

    def test_pick_area_by_kind_of_plant(self):
        self.assertEqual(pick_area(RECORDS["carrot"], self.AREAS), "veg")
        self.assertEqual(pick_area(RECORDS["apple"], self.AREAS), "fruit")
        self.assertEqual(pick_area(RECORDS["apple"], self.AREAS[:2]), "veg")
        self.assertEqual(pick_area(RECORDS["zinnia"], self.AREAS), "front")
        self.assertIsNone(pick_area(RECORDS["zinnia"], [("veg", "vegetable")]))

    def test_packets_whose_window_is_near(self):
        found = self.propose(
            [
                seed("Blush Cherry Tomato", "tomato", guessed=True),
                seed("Danvers carrot", "carrot"),
                seed("Garlic", "garlic"),  # planted in autumn: not yet
                seed("Zinnia", "zinnia"),  # April 15: more than 12 weeks out
                seed("Mystery"),  # no plant: nothing to schedule
                seed("Used carrot", "carrot", quantity=0),
                seed("Old carrot", "carrot", expiry=date(2026, 1, 1)),
            ]
        )
        self.assertEqual(
            [(p.seed.name, p.area_id, p.opens) for p in found],
            [
                ("Blush Cherry Tomato", "veg", date(2027, 2, 18)),
                ("Danvers carrot", "veg", date(2027, 3, 25)),
            ],
        )
        later = self.propose([seed("Zinnia", "zinnia")], today=date(2027, 2, 1))
        self.assertEqual([p.area_id for p in later], ["front"])

    def test_covered_packets_are_not_proposed(self):
        packet = seed("Danvers carrot", "carrot")
        growing = planting("carrot")
        self.assertEqual(self.propose([packet], [growing]), [])
        declined = Planting(
            id="x",
            area_id="veg",
            name="Danvers carrot",
            status="removed",
            from_seed="Danvers carrot",
        )
        self.assertEqual(self.propose([packet], [declined]), [])
        # A dead carrot does not cover the packet; only living or planned ones.
        dead = planting("carrot", status="dead")
        self.assertEqual(len(self.propose([packet], [dead])), 1)
        self.assertEqual(self.propose([packet], areas=[("front", "cottage")]), [])


if __name__ == "__main__":
    unittest.main()
