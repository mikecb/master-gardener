"""Weather rule tests. Standard library only."""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parents[1] / "custom_components" / "master_gardener")
)

from engine.care import water_advice
from engine.models import Planting, Site
from engine.weather import (
    DayForecast,
    daily,
    frost_alert,
    heat_alert,
    in_ground,
    rain_note,
)

SITE = Site(last_frost=(4, 15), first_frost=(10, 25))
TODAY = date(2027, 4, 20)
TOMORROW = TODAY + timedelta(days=1)


def day(offset=0, **values):
    return DayForecast(day=TODAY + timedelta(days=offset), **values)


def planting(name, tolerance="tender", season="warm", planted_on=TODAY, **extra):
    record = {"season": season, "frost_tolerance": tolerance}
    return Planting(
        id=name,
        area_id="a",
        name=name,
        plant_data=record,
        planted_on=planted_on,
        **extra,
    )


def frost(forecast, plantings, today=TODAY, **kwargs):
    return frost_alert(forecast, plantings, site=SITE, today=today, **kwargs)


class DailyTest(unittest.TestCase):
    def test_daily_entries_pass_through(self):
        entry = {
            "day": TODAY,
            "temperature": 18,
            "templow": 3,
            "precipitation": 4,
            "precipitation_probability": 60,
        }
        (only,) = daily([entry])
        self.assertEqual(only, DayForecast(TODAY, 3, 18, 4, 60))

    def test_day_and_night_halves_are_joined(self):
        entries = [
            {"day": TODAY, "temperature": 1, "is_daytime": False},
            {"day": TOMORROW, "temperature": 15, "is_daytime": True},
            {"day": TOMORROW, "temperature": -2, "is_daytime": False},
        ]
        days = daily(entries)
        self.assertEqual((days[0].low, days[0].high), (1, None))
        self.assertEqual((days[1].low, days[1].high), (-2, 15))
        self.assertIsNone(days[1].rain_mm)

    def test_hours_are_folded_into_days(self):
        entries = [
            {"day": TODAY, "temperature": 9, "precipitation": 1.5},
            {"day": TODAY, "temperature": 4, "precipitation": 2.5},
            {"day": TOMORROW, "temperature": 12, "precipitation_probability": 20},
            {"day": TOMORROW, "temperature": 21, "precipitation_probability": 80},
        ]
        days = daily(entries)
        self.assertEqual(days[0], DayForecast(TODAY, 4, 9, 4.0, None))
        self.assertEqual(days[1], DayForecast(TOMORROW, 12, 21, None, 80))


class RainTest(unittest.TestCase):
    def test_enough_rain_by_tomorrow(self):
        forecast = [day(0, rain_mm=2), day(1, rain_mm=6), day(2, rain_mm=30)]
        self.assertEqual(
            rain_note(forecast, TODAY), "8 mm of rain is forecast by tomorrow"
        )
        self.assertEqual(
            rain_note(forecast, TODAY, imperial=True),
            "0.3 in of rain is forecast by tomorrow",
        )

    def test_too_little_too_late_or_too_unlikely(self):
        self.assertIsNone(rain_note([], TODAY))
        self.assertIsNone(rain_note([day(0, rain_mm=3)], TODAY))
        self.assertIsNone(rain_note([day(2, rain_mm=30)], TODAY))
        self.assertIsNone(rain_note([day(-1, rain_mm=30)], TODAY))
        self.assertIsNone(rain_note([day(0, rain_mm=30, rain_chance=30)], TODAY))
        self.assertIsNotNone(rain_note([day(0, rain_mm=30, rain_chance=50)], TODAY))

    def test_a_forecast_without_amounts_must_be_near_certain(self):
        self.assertIsNone(rain_note([day(1, rain_chance=60)], TODAY))
        self.assertEqual(
            rain_note([day(1, rain_chance=80)], TODAY), "rain is 80% likely by tomorrow"
        )

    def test_watering_waits_for_rain_unless_the_soil_is_very_dry(self):
        note = "8 mm of rain is forecast by tomorrow"
        waiting = water_advice([22], 30, rain=note)
        self.assertFalse(waiting.needed)
        self.assertEqual(waiting.reason, f"Soil moisture 22% is below 30%, but {note}")
        self.assertTrue(water_advice([17], 30, rain=note).needed)
        self.assertEqual(
            water_advice([40], 30, rain=note).reason, "Soil moisture 40% is fine"
        )


class InGroundTest(unittest.TestCase):
    def test_annuals_count_from_planting_for_a_year(self):
        self.assertTrue(in_ground(planting("Tomato"), TODAY))
        self.assertFalse(in_ground(planting("Tomato", planted_on=None), TODAY))
        self.assertFalse(in_ground(planting("Tomato", planted_on=TOMORROW), TODAY))
        last_year = TODAY - timedelta(days=400)
        self.assertFalse(in_ground(planting("Tomato", planted_on=last_year), TODAY))

    def test_perennials_and_unknown_plants_are_assumed_planted(self):
        fig = planting("Fig", season="perennial", planted_on=None)
        self.assertTrue(in_ground(fig, TODAY))
        long_ago = TODAY - timedelta(days=900)
        old = planting("Fig", season="perennial", planted_on=long_ago)
        self.assertTrue(in_ground(old, TODAY))
        unknown = Planting(id="x", area_id="a", name="Mystery shrub")
        self.assertTrue(in_ground(unknown, TODAY))

    def test_only_living_plantings(self):
        self.assertFalse(in_ground(planting("Tomato", status="dead"), TODAY))
        self.assertFalse(in_ground(planting("Tomato", status="proposed"), TODAY))


class FrostTest(unittest.TestCase):
    def test_tender_plants_are_named_for_the_first_cold_night(self):
        forecast = [day(0, low=6), day(1, low=1), day(2, low=-1)]
        beds = [
            planting("Tomato"),
            planting("Tomato"),
            planting("Carrot", "half-hardy", "cool"),
            planting("Garlic", "hardy", "cool"),
        ]
        alert = frost(forecast, beds)
        self.assertEqual((alert.kind, alert.day), ("frost", TOMORROW))
        self.assertEqual(alert.temperature, -1)
        self.assertEqual(alert.plants, ("Tomato",))
        self.assertEqual(
            alert.reason,
            "Lows down to -1°C forecast, starting tomorrow. Cover or bring in: Tomato",
        )

    def test_half_hardy_plants_need_a_harder_frost(self):
        beds = [planting("Carrot", "half-hardy", "cool")]
        self.assertIsNone(frost([day(0, low=0)], beds))
        alert = frost([day(0, low=-3)], beds, imperial=True)
        self.assertIn("27°F forecast, starting today", alert.reason)

    def test_nothing_to_protect_or_no_cold(self):
        self.assertIsNone(frost([day(0, low=-5)], [planting("Garlic", "hardy")]))
        self.assertIsNone(frost([day(0, low=-5)], []))
        self.assertIsNone(frost([day(0, low=5)], [planting("Tomato")]))
        self.assertIsNone(frost([day(0, high=1)], [planting("Tomato")]))
        self.assertIsNone(frost([day(3, low=-5)], [planting("Tomato")]))  # too far
        not_out = planting("Tomato", planted_on=None)
        self.assertIsNone(frost([day(0, low=-5)], [not_out]))

    def test_weekday_named_beyond_tomorrow(self):
        alert = frost([day(2, low=0)], [planting("Tomato")])
        self.assertIn("starting on Thursday", alert.reason)

    def test_quiet_in_deep_winter(self):
        fig = planting("Fig", season="perennial", planted_on=None)
        for today in (date(2027, 1, 15), date(2027, 12, 5)):
            cold = [DayForecast(day=today, low=-8)]
            self.assertIsNone(frost(cold, [fig], today=today))
        for today in (date(2027, 2, 25), date(2027, 11, 15)):
            cold = [DayForecast(day=today, low=-8)]
            self.assertIsNotNone(frost(cold, [fig], today=today))

    def test_long_lists_are_cut_short(self):
        beds = [planting(f"Plant {n}") for n in range(8)]
        alert = frost([day(0, low=0)], beds)
        self.assertEqual(len(alert.plants), 8)
        self.assertTrue(alert.reason.endswith("Plant 5 and 2 more"))


class HeatTest(unittest.TestCase):
    def test_cool_season_crops_suffer_first(self):
        spring = TODAY - timedelta(days=60)
        pea = planting("Pea", "hardy", "cool", planted_on=spring)
        tomato = planting("Tomato", planted_on=spring)
        forecast = [day(0, high=27), day(1, high=31), day(2, high=33)]
        alert = heat_alert(forecast, [pea, tomato], today=TODAY)
        self.assertEqual((alert.kind, alert.day), ("heat", TOMORROW))
        self.assertEqual(alert.plants, ("Pea",))
        self.assertEqual(
            alert.reason,
            "Highs up to 33°C forecast, starting tomorrow. Water early and shade: Pea",
        )
        self.assertIsNone(heat_alert([day(0, high=29)], [pea], today=TODAY))

    def test_new_plantings_in_real_heat(self):
        new = planting("Tomato", planted_on=TODAY - timedelta(days=10))
        settled = planting("Zinnia", planted_on=TODAY - timedelta(days=90))
        self.assertIsNone(heat_alert([day(0, high=33)], [new], today=TODAY))
        alert = heat_alert([day(0, high=36)], [new, settled], today=TODAY)
        self.assertEqual(alert.plants, ("Tomato",))
        alert = heat_alert([day(0, high=36)], [new], today=TODAY, imperial=True)
        self.assertIn("97°F", alert.reason)

    def test_nothing_at_risk(self):
        settled = planting("Zinnia", planted_on=TODAY - timedelta(days=90))
        self.assertIsNone(heat_alert([day(0, high=40)], [settled], today=TODAY))
        self.assertIsNone(heat_alert([day(0, low=40)], [planting("Pea")], today=TODAY))


if __name__ == "__main__":
    unittest.main()
