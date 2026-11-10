"""Frost dates from station normals. Standard library only."""

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(
    0, str(Path(__file__).parents[1] / "custom_components" / "master_gardener")
)

from engine import frost_normals as fn

GARDEN = (38.0724, -78.5015)  # Charlottesville, VA 22901


def result(station_id, name, lat, lon):
    """One station in a search response, as NOAA returns it."""
    return {
        "boundingPoints": [{"coordinates": [lon, lat], "type": "point"}],
        "stations": [{"id": station_id, "name": name, "dataTypes": []}],
    }


SEARCH = {
    "results": [
        result("USW00093736", "CHARLOTTESVILLE AP, VA US", 38.1386, -78.4530),
        result("USC00443213", "FREE UNION, VA US", 38.0908, -78.5894),
        result("USC00441593", "CHARLOTTESVILLE 2W, VA US", 38.0328, -78.5225),
        {"stations": []},  # malformed: skipped
    ]
}


def row(station_id, last, first, elevation=" 179.8", name="SOMEWHERE, VA US"):
    return {
        "STATION": station_id,
        "NAME": name,
        "ELEVATION": elevation,
        fn.LAST_FROST: last,
        fn.FIRST_FROST: first,
    }


class FrostNormalsTest(unittest.TestCase):
    def test_urls(self):
        query = parse_qs(urlparse(fn.search_url(*GARDEN, 0.5)).query)
        self.assertEqual(query["dataset"], [fn.DATASET])
        self.assertEqual(query["bbox"], ["38.5724,-79.0015,37.5724,-78.0015"])
        query = parse_qs(urlparse(fn.data_url(["A", "B"])).query)
        self.assertEqual(query["stations"], ["A,B"])
        self.assertEqual(query["dataTypes"], [f"{fn.LAST_FROST},{fn.FIRST_FROST}"])

    def test_nearest_stations_first(self):
        found = fn.stations(SEARCH)
        self.assertEqual(len(found), 3)
        ranked = fn.nearest(found, *GARDEN)
        self.assertEqual(
            [s.id for s, _ in ranked], ["USC00441593", "USC00443213", "USW00093736"]
        )
        self.assertAlmostEqual(ranked[0][1], 4.8, delta=0.1)
        self.assertEqual(len(fn.nearest(found, *GARDEN, limit=2)), 2)

    def test_distance(self):
        # One degree of latitude is about 111 km.
        self.assertAlmostEqual(fn.distance_km(38, -78, 39, -78), 111.2, delta=0.2)

    def test_month_day(self):
        self.assertEqual(fn.month_day("   04/02"), (4, 2))
        for missing in (" -9999.0", "", None, "13/01", "04/40"):
            self.assertIsNone(fn.month_day(missing))

    def test_pick_the_nearest_station_with_both_dates(self):
        ranked = fn.nearest(fn.stations(SEARCH), *GARDEN)
        rows = [
            row("USC00441593", " -9999.0", "   11/10"),  # nearest, incomplete
            row("USC00443213", "   04/19", "   10/24", name="FREE UNION, VA US"),
            row("USW00093736", "   04/03", "   11/01"),
        ]
        normals = fn.pick(rows, ranked)
        self.assertEqual(normals.station, "USC00443213")
        self.assertEqual(normals.name, "FREE UNION, VA")
        self.assertEqual((normals.last_frost, normals.first_frost), ((4, 19), (10, 24)))
        self.assertEqual(normals.elevation_m, 180)
        self.assertEqual(
            normals.as_data(),
            {
                "station": "USC00443213",
                "name": "FREE UNION, VA",
                "distance_km": round(normals.distance_km, 1),
                "elevation_m": 180,
                "last_frost": "04-19",
                "first_frost": "10-24",
            },
        )

    def test_frost_free_or_unusable_gives_nothing(self):
        ranked = fn.nearest(fn.stations(SEARCH), *GARDEN)
        rows = [
            row("USC00441593", " -9999.0", " -9999.0"),
            row("USC00443213", "   11/01", "   03/01"),  # out of order
            row("USW00093736", "   04/03", "   11/01", elevation="n/a"),
        ]
        normals = fn.pick(rows, ranked)
        self.assertEqual(normals.station, "USW00093736")
        self.assertIsNone(normals.elevation_m)
        self.assertIsNone(fn.pick(rows[:2], ranked))
        self.assertIsNone(fn.pick([], ranked))
        self.assertEqual(fn.stations({}), [])


if __name__ == "__main__":
    unittest.main()
