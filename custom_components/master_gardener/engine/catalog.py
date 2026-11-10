"""Bundled plant and area-style knowledge."""

from __future__ import annotations

import json
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "catalog.json"


class Catalog:
    def __init__(self, data: dict) -> None:
        self.plants: dict[str, dict] = data["plants"]
        self._area_rules: dict[str, list[dict]] = data["area_rules"]
        self._lookup: dict[str, str] = {}
        for key, plant in self.plants.items():
            for label in (key, plant["name"], *plant.get("aliases", [])):
                self._lookup[_norm(label)] = key

    @classmethod
    def load(cls, path: Path = DATA_FILE) -> Catalog:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def get(self, key: str | None) -> dict | None:
        return self.plants.get(key) if key else None

    def match(self, text: str) -> str | None:
        """Find a catalog key from a key, name or alias (singular or plural)."""
        wanted = _norm(text)
        for candidate in (wanted, wanted.removesuffix("s"), wanted.removesuffix("es")):
            if candidate in self._lookup:
                return self._lookup[candidate]
        return None

    def area_rules(self, style: str) -> list[dict]:
        return [*self._area_rules.get("all", []), *self._area_rules.get(style, [])]


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("_", " ").replace("-", " ").split())
