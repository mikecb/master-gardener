"""Plain data types for the scheduling engine. No Home Assistant imports here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

ACTIVE = "active"
# Suggested (by an advisor) but not agreed: recorded, no tasks until accepted.
PROPOSED = "proposed"
# Agreed and waiting in the build plan to be bought: no tasks until then.
PLANNED = "planned"
DEAD = "dead"
REMOVED = "removed"
PLANTING_STATUSES = (ACTIVE, PROPOSED, PLANNED, DEAD, REMOVED)
# Projects (hardscape and other work that is not a plant).
DONE_PROJECT = "done"
PROJECT_STATUSES = (PLANNED, DONE_PROJECT, REMOVED)

UPCOMING = "upcoming"
DUE = "due"
OVERDUE = "overdue"
DONE = "done"


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass(frozen=True)
class Site:
    """Climate anchors for the property, as (month, day)."""

    last_frost: tuple[int, int]
    first_frost: tuple[int, int]

    def anchor(self, name: str, year: int) -> date:
        month, day = self.last_frost if name == "last_frost" else self.first_frost
        if (month, day) == (2, 29):
            day = 28
        return date(year, month, day)


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    style: str
    since: date | None = None  # tasks that ended before this are not generated


@dataclass
class Planting:
    id: str
    area_id: str
    name: str
    plant: str | None = None  # OpenPlantDB slug, None if not in the database
    plant_data: dict | None = None  # that plant's record, kept with the planting
    profile: str | None = None  # care profile supplying upkeep tasks
    quantity: int = 1
    planted_on: date | None = None
    # In the ground since before it was recorded, on a date nobody knows.
    established: bool = False
    added_on: date | None = None
    status: str = ACTIVE
    notes: str = ""
    estimated_cost: float | None = None  # for the whole quantity
    # Build plan: how urgent, which structural tier (None: from the plant),
    # the earliest year it may be bought, and when it was bought and for what.
    priority: str = "normal"
    tier: str | None = None
    not_before: int | None = None
    bought_on: date | None = None
    cost: float | None = None
    # The seed inventory item this was proposed from, so it is not proposed
    # again, and accepting it is not a purchase.
    from_seed: str | None = None

    def to_dict(self, *, with_record: bool = True) -> dict:
        """As stored; with_record=False leaves out the bulky plant record."""
        record = {"plant_data": self.plant_data} if with_record else {}
        return {
            "id": self.id,
            "area_id": self.area_id,
            "name": self.name,
            "plant": self.plant,
            **record,
            "profile": self.profile,
            "quantity": self.quantity,
            "planted_on": _iso(self.planted_on),
            "established": self.established,
            "added_on": _iso(self.added_on),
            "status": self.status,
            "notes": self.notes,
            "estimated_cost": self.estimated_cost,
            "priority": self.priority,
            "tier": self.tier,
            "not_before": self.not_before,
            "bought_on": _iso(self.bought_on),
            "cost": self.cost,
            "from_seed": self.from_seed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Planting:
        return cls(
            id=data["id"],
            area_id=data["area_id"],
            name=data["name"],
            plant=data.get("plant"),
            plant_data=data.get("plant_data"),
            profile=data.get("profile"),
            quantity=data.get("quantity", 1),
            planted_on=_date(data.get("planted_on")),
            established=data.get("established", False),
            added_on=_date(data.get("added_on")),
            status=data.get("status", ACTIVE),
            notes=data.get("notes", ""),
            estimated_cost=data.get("estimated_cost"),
            priority=data.get("priority", "normal"),
            tier=data.get("tier"),
            not_before=data.get("not_before"),
            bought_on=_date(data.get("bought_on")),
            cost=data.get("cost"),
            from_seed=data.get("from_seed"),
        )


@dataclass
class Project:
    """Build-plan work that is not a plant: a path, a fence, raised beds."""

    id: str
    area_id: str
    name: str
    tier: str = "hardscape"
    estimated_cost: float | None = None
    priority: str = "normal"
    not_before: int | None = None
    status: str = PLANNED
    notes: str = ""
    added_on: date | None = None
    done_on: date | None = None
    cost: float | None = None  # what it actually cost

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "area_id": self.area_id,
            "name": self.name,
            "tier": self.tier,
            "estimated_cost": self.estimated_cost,
            "priority": self.priority,
            "not_before": self.not_before,
            "status": self.status,
            "notes": self.notes,
            "added_on": _iso(self.added_on),
            "done_on": _iso(self.done_on),
            "cost": self.cost,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Project:
        return cls(
            id=data["id"],
            area_id=data["area_id"],
            name=data["name"],
            tier=data.get("tier", "hardscape"),
            estimated_cost=data.get("estimated_cost"),
            priority=data.get("priority", "normal"),
            not_before=data.get("not_before"),
            status=data.get("status", PLANNED),
            notes=data.get("notes", ""),
            added_on=_date(data.get("added_on")),
            done_on=_date(data.get("done_on")),
            cost=data.get("cost"),
        )


@dataclass
class CustomTask:
    """A task the user added by hand, e.g. feedback like "grass too long"."""

    id: str
    area_id: str
    title: str
    due: date
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "area_id": self.area_id,
            "title": self.title,
            "due": self.due.isoformat(),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CustomTask:
        return cls(
            id=data["id"],
            area_id=data["area_id"],
            title=data["title"],
            due=date.fromisoformat(data["due"]),
            notes=data.get("notes", ""),
        )


@dataclass(frozen=True)
class Task:
    id: str
    area_id: str
    title: str
    kind: str
    start: date
    end: date  # last day of the window, inclusive
    detail: str = ""
    planting_id: str | None = None
    series: str | None = None  # set for repeating tasks (mowing, deadheading)
    projected: bool = False  # a future repeat, shown on the calendar only
    custom: bool = False
    completed_on: date | None = None

    def state(self, today: date) -> str:
        if self.completed_on:
            return DONE
        if today < self.start:
            return UPCOMING
        if today <= self.end:
            return DUE
        return OVERDUE
