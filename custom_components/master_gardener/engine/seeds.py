"""Seed on hand, from an inventory kept elsewhere (Simple Inventory).

For each sowing coming up, the matching seed is looked for: its "Sow ..."
task says what there is, and where there is none, or only seed past its
expiry date, a "Get seed for ..." task comes before the sowing window.

Matching: a planting of a plain species (tomato) takes seed of that species
or named for it (Sungold tomato); a planting of a named variety (Cherokee
Purple tomato) wants that variety.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

from .models import ACTIVE, Planting, Task

SOW_KINDS = {"sow_indoors", "direct_sow"}
ORDER_KIND = "order_seed"
# Seed is wanted in hand this long before the sowing window opens.
ORDER_LEAD = timedelta(weeks=6)


@dataclass(frozen=True)
class SeedItem:
    name: str
    quantity: float | None = None  # None: not counted, assumed some
    unit: str = ""
    expiry: date | None = None
    aliases: tuple[str, ...] = ()
    record: dict | None = None  # the catalogue plant its name resolves to

    @property
    def names(self) -> list[str]:
        return [n.lower() for n in (self.name, *self.aliases) if n]

    def describe(self) -> str:
        if self.quantity is None:
            return self.name
        unit = f" {self.unit}" if self.unit else ""
        return f"{self.name} ({self.quantity:g}{unit})"


def parse_items(response: dict | None) -> list[SeedItem]:
    """Simple Inventory's get_items response; unnamed items are skipped."""
    items = []
    for raw in (response or {}).get("items") or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            continue
        try:
            quantity = (
                float(raw["quantity"]) if raw.get("quantity") is not None else None
            )
        except (TypeError, ValueError):
            quantity = None
        try:
            expiry = date.fromisoformat(str(raw.get("expiry_date"))[:10])
        except ValueError:
            expiry = None
        aliases = raw.get("aliases") or []
        items.append(
            SeedItem(
                name=str(raw["name"]).strip(),
                quantity=quantity,
                unit=str(raw.get("unit") or ""),
                expiry=expiry,
                aliases=tuple(str(a) for a in aliases if a),
            )
        )
    return items


def _binomial(record: dict) -> str:
    """'Solanum lycopersicum 'Sungold'' -> 'solanum lycopersicum'."""
    words = record["scientific_name"].lower().replace(chr(215), " ").split()
    return " ".join([w for w in words if w != "x"][:2])


def _is_variety(record: dict | None) -> bool:
    return bool(record) and "'" in record["scientific_name"]


def matches(item: SeedItem, planting: Planting) -> bool:
    """Whether this seed will do for this planting."""
    record = planting.plant_data
    if item.record and record:
        if item.record["slug"] == record["slug"]:
            return True
        if not _is_variety(record) and _binomial(item.record) == _binomial(record):
            return True
    wanted = {planting.name.lower()}
    if record:
        wanted.add(record["common_name"].lower().split(" (")[0])
    for name in wanted:
        pattern = rf"\b{re.escape(name)}(e?s)?\b"
        if any(re.search(pattern, have) for have in item.names):
            return True
    return False


def stock(
    items: list[SeedItem], planting: Planting, today: date
) -> tuple[list[SeedItem], list[SeedItem]]:
    """(usable, expired) seed for a planting. Used-up seed is neither."""
    usable, expired = [], []
    for item in items:
        if not matches(item, planting):
            continue
        if item.quantity is not None and item.quantity <= 0:
            continue
        if item.expiry and item.expiry < today:
            expired.append(item)
        else:
            usable.append(item)
    return usable, expired


def apply_seed(
    tasks: list[Task],
    plantings: dict[str, Planting],
    items: list[SeedItem],
    completions: dict[str, date],
    today: date,
) -> list[Task]:
    """Say what seed there is on each open sowing; add "Get seed" where none."""
    out: list[Task] = []
    orders: dict[str, Task] = {}
    for task in tasks:
        planting = plantings.get(task.planting_id or "")
        if (
            task.kind not in SOW_KINDS
            or planting is None
            or planting.status != ACTIVE
            or task.completed_on
            or task.end < today
        ):
            out.append(task)
            continue
        usable, expired = stock(items, planting, today)
        if usable:
            have = ", ".join(i.describe() for i in usable)
            detail = f"{task.detail} Seed on hand: {have}.".strip()
            out.append(replace(task, detail=detail))
            continue
        out.append(task)
        order_id = f"seed:{planting.id}:{task.start.year}"
        if expired:
            old = ", ".join(
                f"{i.name} (expired {i.expiry.isoformat()})" for i in expired
            )
            why = f"The seed on hand is past its date: {old}."
        else:
            why = f"No seed for {planting.name} in the seed inventory."
        orders[order_id] = Task(
            id=order_id,
            area_id=task.area_id,
            title=f"Get seed for {planting.name}",
            kind=ORDER_KIND,
            start=task.start - ORDER_LEAD,
            end=task.start - timedelta(days=1),
            detail=f"{why} Sowing starts {task.start.isoformat()}.",
            planting_id=planting.id,
            completed_on=completions.get(order_id),
        )
    out += orders.values()
    out.sort(key=lambda t: (t.start, t.end, t.title))
    return out
