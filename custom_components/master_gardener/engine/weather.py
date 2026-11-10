"""Decide from a weather forecast when to hold off watering and what to protect.

Temperatures are in °C and rain in mm; the caller converts. Only the text shown
to the user is put in their own units.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from .models import ACTIVE, Planting, Site

FROST = "frost"
HEAT = "heat"

# Rain today or tomorrow counts; further out the forecast is too unsure and the
# soil too dry by then.
RAIN_DAYS = 2
RAIN_ENOUGH_MM = 5.0
# Forecast rain is only counted when it is at least this likely (percent)...
RAIN_LIKELY = 50
# ...and a forecast that gives no amounts must be this sure of rain.
RAIN_NEAR_CERTAIN = 70

# Frost and heat are looked for today and the two days after: far enough ahead
# to act, near enough to trust.
ALERT_DAYS = 3

# Lowest forecast temperature at which a planting wants protecting. Frost can
# settle on leaves when the air a few feet up is still just above freezing.
FROST_BELOW = {"tender": 2.0, "half-hardy": -2.0}
# Frost alerts run from eight weeks before the average last frost until four
# weeks after the average first one. In deep winter whatever is tender has
# been lifted, covered for the season or lost, and a nightly alert is noise.
FROST_SEASON_START_WEEKS = -8
FROST_SEASON_END_WEEKS = 4

# Cool-season crops bolt or scorch in heat that established plants shrug off.
HEAT_ABOVE_COOL_SEASON = 30.0
# Anything planted in the last few weeks has no roots to speak of yet.
HEAT_ABOVE_NEW = 35.0
NEW_FOR_DAYS = 42

MAX_NAMED = 6


@dataclass(frozen=True)
class DayForecast:
    day: date
    low: float | None = None
    high: float | None = None
    rain_mm: float | None = None
    rain_chance: float | None = None  # percent


@dataclass(frozen=True)
class Alert:
    kind: str  # FROST or HEAT
    day: date  # the first day it applies
    temperature: float  # the worst forecast over the alert days, °C
    plants: tuple[str, ...]
    reason: str


def daily(entries: Iterable[dict]) -> list[DayForecast]:
    """Fold forecast entries into one per day.

    Each entry has a `day` and may have `temperature`, `templow`,
    `is_daytime`, `precipitation` and `precipitation_probability`. That covers
    daily forecasts (one entry a day, high and low), twice-daily ones (a day
    entry with the high, a night entry with the low) and hourly ones.
    """
    lows: dict[date, list[float]] = {}
    highs: dict[date, list[float]] = {}
    rain: dict[date, list[float]] = {}
    chance: dict[date, list[float]] = {}
    days: set[date] = set()
    for entry in entries:
        day = entry["day"]
        days.add(day)
        temperature = entry.get("temperature")
        low = entry.get("templow")
        daytime = entry.get("is_daytime")
        if low is not None:
            lows.setdefault(day, []).append(low)
        if temperature is not None:
            if low is None and daytime is not True:
                lows.setdefault(day, []).append(temperature)
            if low is not None or daytime is not False:
                highs.setdefault(day, []).append(temperature)
        if entry.get("precipitation") is not None:
            rain.setdefault(day, []).append(entry["precipitation"])
        if entry.get("precipitation_probability") is not None:
            chance.setdefault(day, []).append(entry["precipitation_probability"])
    return [
        DayForecast(
            day=day,
            low=min(lows[day]) if day in lows else None,
            high=max(highs[day]) if day in highs else None,
            rain_mm=sum(rain[day]) if day in rain else None,
            rain_chance=max(chance[day]) if day in chance else None,
        )
        for day in sorted(days)
    ]


def _temp(celsius: float, imperial: bool) -> str:
    return f"{celsius * 9 / 5 + 32:.0f}°F" if imperial else f"{celsius:.0f}°C"


def _when(day: date, today: date) -> str:
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return f"on {day:%A}"


def _named(plantings: list[Planting]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(p.name for p in plantings))


def _list(names: tuple[str, ...]) -> str:
    shown = ", ".join(names[:MAX_NAMED])
    more = len(names) - MAX_NAMED
    return f"{shown} and {more} more" if more > 0 else shown


def _window(forecast: list[DayForecast], today: date, days: int) -> list[DayForecast]:
    last = today + timedelta(days=days - 1)
    days = [d for d in forecast if today <= d.day <= last]
    return sorted(days, key=lambda d: d.day)


def rain_note(
    forecast: list[DayForecast], today: date, *, imperial: bool = False
) -> str | None:
    """Why watering can wait, or None when no worthwhile rain is forecast."""
    days = _window(forecast, today, RAIN_DAYS)
    total = sum(
        d.rain_mm
        for d in days
        if d.rain_mm and (d.rain_chance is None or d.rain_chance >= RAIN_LIKELY)
    )
    if total >= RAIN_ENOUGH_MM:
        amount = f"{total / 25.4:.1f} in" if imperial else f"{total:.0f} mm"
        return f"{amount} of rain is forecast by tomorrow"
    chance = max(
        (d.rain_chance for d in days if d.rain_mm is None and d.rain_chance),
        default=0,
    )
    if chance >= RAIN_NEAR_CERTAIN:
        return f"rain is {chance:.0f}% likely by tomorrow"
    return None


def _is_annual(planting: Planting) -> bool:
    record = planting.plant_data
    return bool(record) and record.get("season") != "perennial"


def in_ground(planting: Planting, today: date) -> bool:
    """Whether the planting is out in the garden for the weather to reach.

    An annual counts from its planting date for a year. A perennial, or a plant
    the catalogue does not know, counts unless its planting date is still ahead.
    """
    if planting.status != ACTIVE:
        return False
    if planting.planted_on is None:
        return not _is_annual(planting)
    if planting.planted_on > today:
        return False
    return not (_is_annual(planting) and (today - planting.planted_on).days > 365)


def frost_alert(
    forecast: list[DayForecast],
    plantings: list[Planting],
    *,
    site: Site,
    today: date,
    imperial: bool = False,
) -> Alert | None:
    """The plantings in one area that the coming nights are too cold for."""
    start = site.anchor("last_frost", today.year) + timedelta(
        weeks=FROST_SEASON_START_WEEKS
    )
    end = site.anchor("first_frost", today.year) + timedelta(
        weeks=FROST_SEASON_END_WEEKS
    )
    if not start <= today <= end:
        return None
    cold = [d for d in _window(forecast, today, ALERT_DAYS) if d.low is not None]
    at_risk = []
    risky_days = []
    for planting in plantings:
        limit = FROST_BELOW.get((planting.plant_data or {}).get("frost_tolerance"))
        if limit is None or not in_ground(planting, today):
            continue
        days = [d for d in cold if d.low <= limit]
        if days:
            at_risk.append(planting)
            risky_days.extend(days)
    if not at_risk:
        return None
    first = min(d.day for d in risky_days)
    lowest = min(d.low for d in risky_days)
    names = _named(at_risk)
    return Alert(
        kind=FROST,
        day=first,
        temperature=lowest,
        plants=names,
        reason=(
            f"Lows down to {_temp(lowest, imperial)} forecast, starting "
            f"{_when(first, today)}. Cover or bring in: {_list(names)}"
        ),
    )


def heat_alert(
    forecast: list[DayForecast],
    plantings: list[Planting],
    *,
    today: date,
    imperial: bool = False,
) -> Alert | None:
    """The plantings in one area that the coming days are too hot for."""
    hot = [d for d in _window(forecast, today, ALERT_DAYS) if d.high is not None]
    at_risk = []
    risky_days = []
    for planting in plantings:
        if not in_ground(planting, today):
            continue
        limits = []
        if (planting.plant_data or {}).get("season") == "cool":
            limits.append(HEAT_ABOVE_COOL_SEASON)
        planted = planting.planted_on
        if planted and (today - planted).days <= NEW_FOR_DAYS:
            limits.append(HEAT_ABOVE_NEW)
        if not limits:
            continue
        days = [d for d in hot if d.high >= min(limits)]
        if days:
            at_risk.append(planting)
            risky_days.extend(days)
    if not at_risk:
        return None
    first = min(d.day for d in risky_days)
    highest = max(d.high for d in risky_days)
    names = _named(at_risk)
    return Alert(
        kind=HEAT,
        day=first,
        temperature=highest,
        plants=names,
        reason=(
            f"Highs up to {_temp(highest, imperial)} forecast, starting "
            f"{_when(first, today)}. Water early and shade: {_list(names)}"
        ),
    )
