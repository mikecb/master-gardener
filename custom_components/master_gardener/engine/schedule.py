"""Turn areas, plantings and frost dates into dated tasks.

Three rule shapes, all anchored to the site's frost dates so the same catalog
works in any temperate climate:

  anchor:   {"anchor": "last_frost", "offset_weeks": -8, "window_weeks": 2}
  after:    {"after": "<rule key>", "days": 70, "window_weeks": 6}
  interval: {"every_days": 7, "anchor": ..., "offset_weeks": ...,
             "until_anchor": ..., "until_offset_weeks": ...}
"""

from __future__ import annotations

from datetime import date, timedelta

from .catalog import Catalog
from .models import ACTIVE, Area, CustomTask, Planting, Site, Task
from .planting_rules import rules_for

# Rules of these kinds are considered done once a planting has a planted_on date.
PLANTING_KINDS = {"sow_indoors", "transplant", "direct_sow", "plant"}
# "after" rules that follow one of these count from the real planting date.
IN_GROUND_KINDS = {"transplant", "direct_sow", "plant"}


def build_tasks(
    *,
    site: Site,
    areas: list[Area],
    plantings: list[Planting],
    custom_tasks: list[CustomTask],
    completions: dict[str, date],
    last_done: dict[str, date],
    catalog: Catalog,
    start: date,
    end: date,
    today: date,
) -> list[Task]:
    """Return every task whose window overlaps [start, end], sorted by date."""
    years = range(start.year - 1, end.year + 1)
    by_id = {area.id: area for area in areas}
    tasks: list[Task] = []

    for area in areas:
        tasks += _expand(
            source=f"area:{area.id}",
            area_id=area.id,
            name=area.name,
            rules=catalog.area_rules(area.style),
            planting=None,
            since=area.since,
            site=site,
            years=years,
            completions=completions,
            last_done=last_done,
            today=today,
        )

    for planting in plantings:
        if planting.status != ACTIVE or planting.area_id not in by_id:
            continue
        rules = [
            *(rules_for(planting.plant_data) if planting.plant_data else ()),
            *catalog.profile_rules(planting.profile),
        ]
        if not rules:
            continue
        since = max(
            (d for d in (planting.planted_on, planting.added_on) if d), default=None
        )
        tasks += _expand(
            source=f"planting:{planting.id}",
            area_id=planting.area_id,
            name=planting.name,
            rules=rules,
            planting=planting,
            since=since,
            site=site,
            years=years,
            completions=completions,
            last_done=last_done,
            today=today,
        )

    for custom in custom_tasks:
        if custom.area_id not in by_id:
            continue
        task_id = f"custom:{custom.id}"
        tasks.append(
            Task(
                id=task_id,
                area_id=custom.area_id,
                title=custom.title,
                kind="custom",
                start=custom.due,
                end=custom.due,
                detail=custom.notes,
                custom=True,
                completed_on=completions.get(task_id),
            )
        )

    tasks = [t for t in tasks if t.end >= start and t.start <= end]
    tasks.sort(key=lambda t: (t.start, t.end, t.title))
    return tasks


def _expand(
    *,
    source: str,
    area_id: str,
    name: str,
    rules: list[dict],
    planting: Planting | None,
    since: date | None,
    site: Site,
    years: range,
    completions: dict[str, date],
    last_done: dict[str, date],
    today: date,
) -> list[Task]:
    tasks: list[Task] = []
    planted_on = planting.planted_on if planting else None
    kinds = {rule["key"]: rule["kind"] for rule in rules}

    for year in years:
        starts: dict[str, date] = {}
        for rule in rules:
            key = rule["key"]
            common = {
                "area_id": area_id,
                "title": rule["title"].format(name=name),
                "kind": rule["kind"],
                "detail": rule.get("detail", ""),
                "planting_id": planting.id if planting else None,
            }

            if rule.get("until_planted") and planted_on:
                continue  # a one-time planting that is already in the ground

            if "every_days" in rule:
                tasks += _repeats(
                    rule, f"{source}:{key}", common, since, site, year, last_done, today
                )
                continue

            if "after" in rule:
                base = starts.get(rule["after"])
                if base is None:
                    continue
                if (
                    planted_on
                    and planted_on.year == year
                    and kinds[rule["after"]] in IN_GROUND_KINDS
                ):
                    base = planted_on
                begin = base + timedelta(days=rule["days"])
            else:
                begin = site.anchor(rule["anchor"], year) + timedelta(
                    weeks=rule["offset_weeks"]
                )
            starts[key] = begin
            finish = begin + timedelta(weeks=rule.get("window_weeks", 1), days=-1)

            task_id = f"{source}:{key}:{year}"
            completed = completions.get(task_id)
            if (
                not completed
                and planted_on
                and planted_on.year == year
                and rule["kind"] in PLANTING_KINDS
            ):
                completed = planted_on
            if since and finish < since:
                continue
            tasks.append(
                Task(
                    id=task_id,
                    start=begin,
                    end=finish,
                    completed_on=completed,
                    **common,
                )
            )
    return tasks


def _repeats(
    rule: dict,
    series: str,
    common: dict,
    since: date | None,
    site: Site,
    year: int,
    last_done: dict[str, date],
    today: date,
) -> list[Task]:
    """One real task (the next one due) plus projected repeats to season end."""
    every = timedelta(days=rule["every_days"])
    season_start = site.anchor(rule["anchor"], year) + timedelta(
        weeks=rule["offset_weeks"]
    )
    season_end = site.anchor(rule["until_anchor"], year) + timedelta(
        weeks=rule["until_offset_weeks"]
    )
    if season_end < today or (since and season_end < since):
        return []  # nothing to do out of season

    done = last_done.get(series)
    first = max(season_start, since) if since else season_start
    if done and done + every > first:
        first = done + every
    elif not done and today > first:
        first = today  # first seen mid-season: due now, not weeks overdue

    tasks: list[Task] = []
    current = first
    while current <= season_end:
        tasks.append(
            Task(
                id=f"{series}:{current.isoformat()}",
                start=current,
                end=current + every - timedelta(days=1),
                series=series,
                projected=current != first,
                **common,
            )
        )
        current += every
    return tasks
