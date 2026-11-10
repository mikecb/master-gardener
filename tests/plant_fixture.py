"""A tiny plant database built from tests/fixtures/plants.csv, for engine tests."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parents[1] / "custom_components" / "master_gardener")
)

from engine import plantdb

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "plants.csv"


def build(directory: Path) -> Path:
    """Build the fixture database in directory and return its path."""
    db_path = Path(directory) / "plants.sqlite"
    original = plantdb.MIN_PLANTS
    plantdb.MIN_PLANTS = 1
    try:
        plantdb.build_database(FIXTURE_CSV, db_path)
    finally:
        plantdb.MIN_PLANTS = original
    return db_path


def records() -> dict[str, dict]:
    """Every fixture plant's record, keyed by id."""
    with tempfile.TemporaryDirectory() as directory:
        db_path = build(Path(directory))
        return {r["slug"]: r for r in plantdb.search(db_path, "a", limit=100)} | {
            r["slug"]: r for r in plantdb.search(db_path, "e", limit=100)
        }
