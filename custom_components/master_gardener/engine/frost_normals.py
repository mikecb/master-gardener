"""Frost dates from the nearest weather station's climate normals.

NOAA's 1991-2020 U.S. Climate Normals give, for each station that records
temperature, the date by which the last spring freeze and the first autumn
freeze have happened in half of all years (32 °F, 50% probability). That is
what gardeners mean by the average last and first frost, and it is measured
where the garden is, rather than inferred from winter cold as the hardiness
zone estimate is.

https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlencode

DATASET = "normals-annualseasonal-1991-2020"
LAST_FROST = "ANN-TMIN-PRBLST-T32FP50"
FIRST_FROST = "ANN-TMIN-PRBFST-T32FP50"
SEARCH_URL = "https://www.ncei.noaa.gov/access/services/search/v1/data"
DATA_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
SOURCE = "NOAA 1991-2020 climate normals"
# Search this far around the garden, in degrees, then twice as far.
SEARCH_RADII = (0.5, 1.0)
# How many of the nearest stations to read; some have no freeze dates.
CANDIDATES = 5
EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class FrostNormals:
    station: str
    name: str
    distance_km: float
    elevation_m: float | None
    last_frost: tuple[int, int]  # (month, day)
    first_frost: tuple[int, int]

    def as_data(self) -> dict:
        """As kept in the config entry: where the dates came from."""
        return {
            "station": self.station,
            "name": self.name,
            "distance_km": round(self.distance_km, 1),
            "elevation_m": self.elevation_m,
            "last_frost": "{:02d}-{:02d}".format(*self.last_frost),
            "first_frost": "{:02d}-{:02d}".format(*self.first_frost),
        }


def search_url(latitude: float, longitude: float, radius: float) -> str:
    """Stations with freeze dates in a box around the garden."""
    north, south = latitude + radius, latitude - radius
    west, east = longitude - radius, longitude + radius
    query = {
        "dataset": DATASET,
        "bbox": f"{north:.4f},{west:.4f},{south:.4f},{east:.4f}",
        "dataTypes": LAST_FROST,
        "limit": 100,
    }
    return f"{SEARCH_URL}?{urlencode(query)}"


def data_url(station_ids: list[str]) -> str:
    query = {
        "dataset": DATASET,
        "stations": ",".join(station_ids),
        "dataTypes": f"{LAST_FROST},{FIRST_FROST}",
        "format": "json",
        "includeStationName": "true",
        "includeStationLocation": "1",
    }
    return f"{DATA_URL}?{urlencode(query)}"


def stations(search: dict) -> list[Station]:
    """The stations in a search response; malformed entries are skipped."""
    found = []
    for result in search.get("results") or []:
        try:
            station = result["stations"][0]
            longitude, latitude = result["boundingPoints"][0]["coordinates"]
            found.append(
                Station(
                    id=station["id"],
                    name=station.get("name") or station["id"],
                    latitude=float(latitude),
                    longitude=float(longitude),
                )
            )
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return found


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest(
    found: list[Station], latitude: float, longitude: float, limit: int = CANDIDATES
) -> list[tuple[Station, float]]:
    ranked = sorted(
        ((s, distance_km(latitude, longitude, s.latitude, s.longitude)) for s in found),
        key=lambda pair: pair[1],
    )
    return ranked[:limit]


def month_day(text: object) -> tuple[int, int] | None:
    """'   04/02' -> (4, 2). Missing values (-9999: no freeze) give None."""
    try:
        month, day = (int(part) for part in str(text).strip().split("/"))
    except ValueError:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


def pick(rows: list[dict], ranked: list[tuple[Station, float]]) -> FrostNormals | None:
    """The nearest station whose normals have both freeze dates, in order."""
    by_id = {row.get("STATION"): row for row in rows}
    for station, km in ranked:
        row = by_id.get(station.id)
        if row is None:
            continue
        last, first = month_day(row.get(LAST_FROST)), month_day(row.get(FIRST_FROST))
        if last is None or first is None or last >= first:
            continue  # frost-free, or not a northern-hemisphere season
        try:
            elevation = round(float(row["ELEVATION"]))
        except (KeyError, TypeError, ValueError):
            elevation = None
        return FrostNormals(
            station=station.id,
            name=_tidy_name(row.get("NAME") or station.name),
            distance_km=km,
            elevation_m=elevation,
            last_frost=last,
            first_frost=first,
        )
    return None


def _tidy_name(name: str) -> str:
    """NOAA's name without the country: 'CHARLOTTESVILLE AP, VA'."""
    return name.strip().removesuffix(" US")
