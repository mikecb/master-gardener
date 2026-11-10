"""Estimate frost dates from a USDA hardiness zone.

A hardiness zone measures winter cold, not frost timing, so this is a starting
point the user can correct. Dates lean cautious: the late end of the usual
last-frost range and the early end of the first-frost range.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# zone number -> (last spring frost, first autumn frost) as (month, day),
# for the colder "a" half. Zones 10 and up are close to frost-free; they get
# nominal dates so the calendar still has anchors.
_ZONE_FROSTS: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {
    1: ((6, 5), (8, 25)),
    2: ((5, 25), (9, 1)),
    3: ((5, 16), (9, 8)),
    4: ((5, 12), (9, 21)),
    5: ((4, 30), (10, 13)),
    6: ((4, 21), (10, 17)),
    7: ((4, 10), (10, 25)),
    8: ((3, 28), (11, 7)),
    9: ((2, 28), (11, 25)),
    10: ((1, 31), (12, 15)),
    11: ((1, 15), (12, 26)),
}
_HALF_ZONE_DAYS = 5  # the warmer "b" half: frost ends earlier, returns later


def frost_dates_for_zone(zone: str) -> tuple[str, str]:
    """Return (last_frost, first_frost) as "MM-DD" for a zone like "7a"."""
    match = re.fullmatch(r"\s*(\d{1,2})\s*([abAB]?)\s*", zone)
    if not match or not 1 <= int(match[1]) <= 13:
        raise ValueError(f"Not a hardiness zone: {zone!r}")
    last, first = _ZONE_FROSTS[min(int(match[1]), 11)]
    shift = timedelta(days=_HALF_ZONE_DAYS if match[2].lower() == "b" else 0)
    year = 2001  # any non-leap year
    return (
        (date(year, *last) - shift).strftime("%m-%d"),
        (date(year, *first) + shift).strftime("%m-%d"),
    )
