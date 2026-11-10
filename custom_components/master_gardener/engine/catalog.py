"""Our own gardening knowledge: yearly tasks per area style and care profile.

Plants themselves come from OpenPlantDB (engine/plantdb.py). A care profile is
the upkeep a kind of plant needs (pruning, feeding, mowing), which that
database does not cover.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "catalog.json"


class Catalog:
    def __init__(self, data: dict) -> None:
        self.profiles: dict[str, dict] = data["profiles"]
        self._area_rules: dict[str, list[dict]] = data["area_rules"]

    @classmethod
    def load(cls, path: Path = DATA_FILE) -> Catalog:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def profile_rules(self, key: str | None) -> list[dict]:
        profile = self.profiles.get(key) if key else None
        return profile["rules"] if profile else []

    def area_rules(self, style: str) -> list[dict]:
        return [*self._area_rules.get("all", []), *self._area_rules.get(style, [])]
