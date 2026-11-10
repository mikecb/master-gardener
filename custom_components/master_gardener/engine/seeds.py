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

from .models import ACTIVE, PLANNED, Planting, Site, Task
from .planting_rules import rules_for
from .schedule import PLANTING_KINDS

SOW_KINDS = {"sow_indoors", "direct_sow"}
ORDER_KIND = "order_seed"
# Seed is wanted in hand this long before the sowing window opens.
ORDER_LEAD = timedelta(weeks=6)
# A packet is proposed for planting this long before its window opens.
PROPOSE_LEAD = timedelta(weeks=12)
# Which garden styles suit which kind of plant, best first.
AREA_STYLES = {
    "vegetable": ("vegetable",),
    "herb": ("vegetable", "mediterranean", "cottage"),
    "fruit": ("orchard", "vegetable"),
    "berry": ("orchard", "vegetable"),
    "flower": ("cottage", "english", "meadow", "mediterranean"),
    "cover-crop": ("vegetable", "meadow"),
}


@dataclass(frozen=True)
class SeedItem:
    name: str
    quantity: float | None = None  # None: not counted, assumed some
    unit: str = ""
    expiry: date | None = None
    aliases: tuple[str, ...] = ()
    record: dict | None = None  # the catalogue plant its name resolves to
    guessed: bool = False  # record is the closest plant, not the name itself

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


@dataclass(frozen=True)
class Proposal:
    """A packet worth planting soon, and where."""

    seed: SeedItem
    area_id: str
    opens: date  # when its sowing or planting window opens


def next_window(record: dict, site: Site, today: date) -> tuple[date, date] | None:
    """The coming (or current) sowing or planting window for a plant."""
    rule = next(
        (r for r in rules_for(record) if r["kind"] in PLANTING_KINDS and "anchor" in r),
        None,
    )
    if rule is None:
        return None
    for year in (today.year, today.year + 1):
        opens = site.anchor(rule["anchor"], year) + timedelta(
            weeks=rule["offset_weeks"]
        )
        closes = opens + timedelta(weeks=rule.get("window_weeks", 1), days=-1)
        if closes >= today:
            return opens, closes
    return None  # pragma: no cover - next year's window is always ahead


def pick_area(record: dict, areas: list[tuple[str, str]]) -> str | None:
    """The first area whose style suits the plant: areas are (id, style)."""
    for style in AREA_STYLES.get(record.get("category", ""), ()):
        for area_id, area_style in areas:
            if area_style == style:
                return area_id
    return None


def proposals(
    items: list[SeedItem],
    plantings: list[Planting],
    areas: list[tuple[str, str]],
    site: Site,
    today: date,
) -> list[Proposal]:
    """Packets whose window opens within PROPOSE_LEAD and that nothing covers.

    A packet is covered once anything was proposed from it (whatever became
    of that), or by a growing or planned planting it would sow.
    """
    proposed = {p.from_seed for p in plantings if p.from_seed}
    current = [p for p in plantings if p.status in (ACTIVE, PLANNED)]
    found = []
    for item in items:
        if item.name in proposed or item.record is None:
            continue
        if item.quantity is not None and item.quantity <= 0:
            continue
        if item.expiry and item.expiry < today:
            continue
        if any(matches(item, p) for p in current):
            continue
        window = next_window(item.record, site, today)
        area_id = pick_area(item.record, areas)
        if window is None or area_id is None or window[0] - today > PROPOSE_LEAD:
            continue
        found.append(Proposal(seed=item, area_id=area_id, opens=window[0]))
    return found
