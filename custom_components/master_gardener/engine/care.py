"""Decide from soil readings whether an area needs water or fertilizer.

Readings are soil moisture in percent and soil conductivity (a proxy for
dissolved nutrients) in µS/cm. With several sensors in an area the lowest
reading decides, so the driest or hungriest spot gets attention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .models import Site

# Once advice is on, the reading must clear the threshold by this much before
# it turns off, so a value hovering at the threshold does not flap.
WATER_MARGIN = 5.0  # percentage points
FEED_MARGIN = 1.1  # factor
# Forecast rain postpones watering only while the soil is above this share of
# its threshold; drier than that, the plants cannot wait on a forecast.
TOO_DRY_TO_WAIT = 0.6

# Feed from shortly before the last frost until six weeks before the first:
# late feeding pushes soft growth that the frost then kills.
FEED_SEASON_START_WEEKS = -2
FEED_SEASON_END_WEEKS = -6

_FALLBACK = "other"
# Soil moisture (%) below which an area of this style wants water.
DEFAULT_DRY_BELOW = {
    "vegetable": 30,
    "orchard": 20,
    "cottage": 25,
    "english": 25,
    "japanese": 25,
    "mediterranean": 12,
    "conifer_woodland": 20,
    "native_woodland": 20,
    "meadow": 10,
    "lawn": 20,
    "other": 20,
}
# Conductivity (µS/cm) below which it wants feeding. 0 means never: wildflower
# meadows need poor soil, and fertilizer hands them over to grasses.
DEFAULT_FEED_BELOW = {
    "vegetable": 350,
    "orchard": 200,
    "cottage": 250,
    "english": 250,
    "japanese": 200,
    "mediterranean": 100,
    "conifer_woodland": 100,
    "native_woodland": 100,
    "meadow": 0,
    "lawn": 200,
    "other": 200,
}


def default_dry_below(style: str) -> float:
    return DEFAULT_DRY_BELOW.get(style, DEFAULT_DRY_BELOW[_FALLBACK])


def default_feed_below(style: str) -> float:
    return DEFAULT_FEED_BELOW.get(style, DEFAULT_FEED_BELOW[_FALLBACK])


@dataclass(frozen=True)
class Advice:
    needed: bool
    reason: str
    reading: float | None = None
    threshold: float | None = None


def water_advice(
    moisture: list[float],
    dry_below: float,
    was_needed: bool = False,
    rain: str | None = None,
) -> Advice | None:
    """None when there is no reading to judge by.

    `rain` says what rain is forecast (see weather.rain_note). Watering then
    waits for it, unless the soil is too dry to gamble on a forecast.
    """
    if not moisture:
        return None
    lowest = min(moisture)
    limit = dry_below + (WATER_MARGIN if was_needed else 0)
    if lowest < limit:
        reason = f"Soil moisture {lowest:.0f}% is below {dry_below:.0f}%"
        if lowest >= dry_below:
            reason = f"Soil moisture {lowest:.0f}% has not recovered yet"
        if rain and lowest >= dry_below * TOO_DRY_TO_WAIT:
            return Advice(False, f"{reason}, but {rain}", lowest, dry_below)
        return Advice(True, reason, lowest, dry_below)
    return Advice(False, f"Soil moisture {lowest:.0f}% is fine", lowest, dry_below)


def feed_advice(
    conductivity: list[float],
    moisture: list[float],
    *,
    feed_below: float,
    dry_below: float,
    site: Site,
    today: date,
    was_needed: bool = False,
) -> Advice | None:
    """None when there is no reading, or the area is never fed."""
    if not conductivity or feed_below <= 0:
        return None
    lowest = min(conductivity)

    def no(reason: str) -> Advice:
        return Advice(False, reason, lowest, feed_below)

    start = site.anchor("last_frost", today.year) + timedelta(
        weeks=FEED_SEASON_START_WEEKS
    )
    end = site.anchor("first_frost", today.year) + timedelta(
        weeks=FEED_SEASON_END_WEEKS
    )
    if not start <= today <= end:
        return no("Outside the feeding season")
    if moisture and min(moisture) < dry_below:
        # Conductivity reads low in dry soil whatever the nutrient level.
        return no("Soil is too dry to judge fertility; water first")
    if lowest < feed_below * (FEED_MARGIN if was_needed else 1):
        return Advice(
            True,
            f"Soil fertility {lowest:.0f} µS/cm is below {feed_below:.0f} µS/cm",
            lowest,
            feed_below,
        )
    return no(f"Soil fertility {lowest:.0f} µS/cm is fine")
