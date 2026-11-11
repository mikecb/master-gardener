"""The build plan: what to buy or build in which year, under an annual budget.

Three rules order the wishlist:

- Structure first, area by area: trees and shrubs, then hardscape, then
  perennials, then small plants. Nothing lands in an earlier year than
  something of an earlier tier in the same area, so a bed is never filled
  with perennials while its tree is still to go in.
- Priority across the property: high, then normal, then low. Whatever an
  item waits on takes on that item's priority, so a high-priority perennial
  pulls its area's trees forward with it.
- Budget: each year takes items in that order while they fit; money left
  over goes to later items that do fit. An item dearer than a whole year's
  budget gets a year to itself.

Like tasks, the plan is never stored: it is worked out again from the
wishlist, so a new item, a purchase or a changed budget reshuffles it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .models import Site, Task
from .planting_rules import rules_for
from .schedule import PLANTING_KINDS

TREES_SHRUBS = "trees_shrubs"
HARDSCAPE = "hardscape"
PERENNIALS = "perennials"
SMALL_PLANTS = "small_plants"
TIERS = (TREES_SHRUBS, HARDSCAPE, PERENNIALS, SMALL_PLANTS)

HIGH = "high"
NORMAL = "normal"
LOW = "low"
PRIORITIES = (HIGH, NORMAL, LOW)

KIND_PLANTING = "planting"
KIND_PROJECT = "project"

# Care profiles that make a plant part of the garden's structure.
STRUCTURE_PROFILES = {
    "apple",
    "peach",
    "blueberry",
    "deciduous_tree",
    "japanese_maple",
    "conifer",
    "pine",
    "boxwood",
    "azalea",
    "hydrangea",
    "rose",
    "deciduous_shrub",
    "evergreen_shrub",
    "currant",
    "fig",
}
STRUCTURE_WORDS = ("tree", "shrub", "conifer", "hedge")
# A plant is bought in the weeks before its planting window opens.
BUY_LEAD = timedelta(weeks=6)
# Without a planting window, work runs from about when the soil can be worked
# until the first frost.
SEASON_START = timedelta(weeks=-4)


def planting_tier(record: dict | None, profile: str | None) -> str:
    """Which structural tier a plant belongs to."""
    if profile in STRUCTURE_PROFILES:
        return TREES_SHRUBS
    if record:
        subcategory = record["subcategory"].lower()
        if any(word in subcategory for word in STRUCTURE_WORDS):
            return TREES_SHRUBS
        if record["season"] != "perennial":
            return SMALL_PLANTS
    return PERENNIALS


@dataclass(frozen=True)
class PlanItem:
    """Something on the wishlist: a planned planting or a project."""

    id: str
    area_id: str
    name: str
    tier: str
    kind: str = KIND_PLANTING
    priority: str = NORMAL
    cost: float | None = None
    not_before: int | None = None
    added_on: date | None = None


@dataclass(frozen=True)
class Placement:
    item: PlanItem
    year: int
    reason: str = ""  # why not this year; empty when it is this year
    over_budget: bool = False  # costs more than a whole year's budget


@dataclass(frozen=True)
class PlanYear:
    year: int
    budget: float | None
    spent: float  # already bought this year (this year only)
    placements: tuple[Placement, ...]

    @property
    def planned(self) -> float:
        return sum(p.item.cost or 0 for p in self.placements)

    @property
    def remaining(self) -> float | None:
        if self.budget is None:
            return None
        return self.budget - self.spent - self.planned

    @property
    def unpriced(self) -> int:
        return sum(1 for p in self.placements if p.item.cost is None)


def build_plan(
    items: list[PlanItem],
    *,
    this_year: int,
    budget: float | None,
    spent: float = 0.0,
) -> list[PlanYear]:
    """Place every item in a year. This year is always included."""
    if budget is not None and budget <= 0:
        budget = None
    tier = {item.id: TIERS.index(item.tier) for item in items}
    rank = {item.id: PRIORITIES.index(item.priority) for item in items}
    # Something an item waits on is as urgent as the item.
    urgency = {
        item.id: min(
            rank[other.id]
            for other in items
            if other.area_id == item.area_id and tier[other.id] >= tier[item.id]
        )
        for item in items
    }
    ordered = sorted(
        items,
        key=lambda i: (
            urgency[i.id],
            tier[i.id],
            i.added_on or date.min,
            i.name.lower(),
            i.id,
        ),
    )

    left: dict[int, float] = {}

    def money(year: int) -> float:
        start = budget - (spent if year == this_year else 0)
        return left.get(year, start)

    placed: list[Placement] = []
    for item in ordered:
        floor, why = this_year, ""
        if item.not_before and item.not_before > floor:
            floor, why = item.not_before, f"Not before {item.not_before}"
        before = [
            p
            for p in placed
            if p.item.area_id == item.area_id and tier[p.item.id] < tier[item.id]
        ]
        if before:
            last = max(before, key=lambda p: p.year)
            if last.year > floor:
                floor, why = last.year, f"After {last.item.name}"

        year, over = floor, False
        if budget is not None:
            cost = item.cost or 0
            over = cost > budget
            need = budget if over else cost  # too dear: wait for an untouched year
            while money(year) + 1e-9 < need:
                year += 1
            left[year] = money(year) - cost
        if year == this_year:
            reason = ""
        elif year == floor:
            reason = why
        else:
            reason = f"No room in the budget before {year}"
        placed.append(Placement(item=item, year=year, reason=reason, over_budget=over))

    years = sorted({this_year, *(p.year for p in placed)})
    return [
        PlanYear(
            year=year,
            budget=budget,
            spent=spent if year == this_year else 0.0,
            placements=tuple(p for p in placed if p.year == year),
        )
        for year in years
    ]


def buy_window(record: dict | None, site: Site, year: int) -> tuple[date, date]:
    """When to buy a plant (or do a project) planned for this year.

    A plant is bought in the six weeks before its planting window opens;
    a project, or a plant with no window, belongs to the working season.
    """
    if record:
        rule = next(
            (
                r
                for r in rules_for(record)
                if r["kind"] in PLANTING_KINDS and "anchor" in r
            ),
            None,
        )
        if rule:
            opens = site.anchor(rule["anchor"], year) + timedelta(
                weeks=rule["offset_weeks"]
            )
            if opens - BUY_LEAD < date(year, 1, 1):
                # The window opens so early it belongs to next season.
                opens = site.anchor(rule["anchor"], year + 1) + timedelta(
                    weeks=rule["offset_weeks"]
                )
            return opens - BUY_LEAD, opens - timedelta(days=1)
    return site.anchor("last_frost", year) + SEASON_START, site.anchor(
        "first_frost", year
    )


def plan_task(
    item: PlanItem,
    *,
    record: dict | None,
    quantity: int,
    site: Site,
    year: int,
    completed_on: date | None = None,
) -> Task:
    """The to-do item for one of this year's purchases."""
    start, end = buy_window(record if item.kind == KIND_PLANTING else None, site, year)
    if item.kind == KIND_PLANTING:
        title = f"Buy {item.name}"
        what = f"{quantity} {item.name}" if quantity > 1 else item.name
    else:
        title = item.name
        what = item.name
    cost = f", about {item.cost:g}" if item.cost is not None else ""
    return Task(
        id=f"plan:{item.id}:{year}",
        area_id=item.area_id,
        title=title,
        kind="buy" if item.kind == KIND_PLANTING else "project",
        start=start,
        end=end,
        detail=f"In this year's build plan: {what}{cost}.",
        planting_id=item.id if item.kind == KIND_PLANTING else None,
        completed_on=completed_on,
    )
