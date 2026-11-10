"""Local plant database built from the OpenPlantDB CSV export.

https://github.com/cwfrazier1/openplantdb (CC0). The CSV is streamed row by
row into SQLite so a 40,000-plant catalogue never has to sit in memory; only
the records of plants actually growing in the garden are kept by the caller.
"""

from __future__ import annotations

import csv
import sqlite3
from contextlib import closing
from pathlib import Path

TEXT_COLUMNS = (
    "slug",
    "common_name",
    "scientific_name",
    "category",
    "subcategory",
    "maturity_from",
    "sow_method",
    "season",
    "frost_tolerance",
    "plant_anchor",
    "plant_note",
    "sun",
    "water",
)
NUMBER_COLUMNS = (
    "maturity_days_min",
    "maturity_days_max",
    "plant_weeks_min",
    "plant_weeks_max",
    "usda_zone_min",
    "usda_zone_max",
    "height_in_min",
    "height_in_max",
    "spread_in_min",
    "spread_in_max",
)
COLUMNS = TEXT_COLUMNS + NUMBER_COLUMNS

# A real export has tens of thousands of rows; far fewer means a broken file.
MIN_PLANTS = 1000


def _number(text: str | None) -> float | int | None:
    if text is None or text.strip() == "":
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def build_database(csv_path: Path, db_path: Path) -> int:
    """Create db_path from an OpenPlantDB plants.csv. Returns the plant count.

    Raises ValueError if the file does not look like the export we expect, so
    a changed or truncated download never replaces a working database.
    """
    db_path.unlink(missing_ok=True)
    with (
        open(csv_path, newline="", encoding="utf-8") as handle,
        closing(sqlite3.connect(db_path)) as con,
    ):
        reader = csv.DictReader(handle)
        missing = set(COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Plant data is missing columns: {sorted(missing)}")
        con.execute(
            "CREATE TABLE plants ("
            + ", ".join(
                [
                    "slug TEXT PRIMARY KEY",
                    *(f"{c} TEXT" for c in TEXT_COLUMNS[1:]),
                    *(f"{c} REAL" for c in NUMBER_COLUMNS),
                    "name_lc TEXT",
                    "search TEXT",
                ]
            )
            + ")"
        )
        marks = ", ".join("?" * (len(COLUMNS) + 2))
        con.executemany(
            f"INSERT OR REPLACE INTO plants VALUES ({marks})",
            (
                (
                    *(row[c] for c in TEXT_COLUMNS),
                    *(_number(row[c]) for c in NUMBER_COLUMNS),
                    row["common_name"].lower(),
                    " ".join(
                        (
                            row["common_name"],
                            row["scientific_name"],
                            row["slug"].replace("-", " "),
                        )
                    ).lower(),
                )
                for row in reader
                if row["slug"]
            ),
        )
        con.execute("CREATE INDEX plants_name ON plants (name_lc)")
        count = con.execute("SELECT count(*) FROM plants").fetchone()[0]
        con.commit()
    if count < MIN_PLANTS:
        db_path.unlink(missing_ok=True)
        raise ValueError(f"Plant data has only {count} plants")
    return count


def _query(db_path: Path, where: str, params: tuple, tail: str = "") -> list[dict]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT {', '.join(COLUMNS)} FROM plants WHERE {where} {tail}", params
        ).fetchall()
    return [_record(row) for row in rows]


def _record(row: sqlite3.Row) -> dict:
    record = dict(row)
    for column in NUMBER_COLUMNS:
        value = record[column]
        if isinstance(value, float) and value.is_integer():
            record[column] = int(value)
    return record


def get(db_path: Path, slug: str) -> dict | None:
    rows = _query(db_path, "slug = ?", (slug,))
    return rows[0] if rows else None


def resolve(db_path: Path, text: str) -> tuple[dict | None, list[dict]]:
    """Find the one plant a user means by an id or a name.

    Returns (plant, []) for an exact id or an unambiguous exact name, and
    otherwise (None, candidates) with the closest matches, possibly none.
    """
    wanted = " ".join(text.lower().split())
    by_name: list[dict] = []
    # As typed, then singular: "tomatoes" and "carrots" mean tomato and carrot.
    for name in dict.fromkeys(
        (wanted, wanted.removesuffix("es"), wanted.removesuffix("s"))
    ):
        if record := get(db_path, name.replace(" ", "-")):
            return record, []
        by_name = by_name or _query(db_path, "name_lc = ?", (name,))
        if len(by_name) == 1:
            return by_name[0], []
    return None, by_name or search(db_path, text, limit=8)


def search(
    db_path: Path,
    query: str = "",
    *,
    zone: int | None = None,
    limit: int = 20,
    sun: str | None = None,
    water: str | None = None,
    category: str | None = None,
    plant_type: str | None = None,
    min_height_in: float | None = None,
    max_height_in: float | None = None,
) -> list[dict]:
    """Plants matching every word of query and every filter given.

    query is matched against the name, botanical name and id; plant_type
    against the free-text subcategory ("evergreen shrub", "fern"). Height
    filters keep plants whose whole mature range fits. With nothing to match
    on, returns nothing rather than the first plants in the alphabet.
    """
    words = query.lower().replace("-", " ").split()
    clauses = ["search LIKE ? ESCAPE '\\'"] * len(words)
    params: list = [f"%{_escape(word)}%" for word in words]
    for column, value in (("sun", sun), ("water", water), ("category", category)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    for word in (plant_type or "").lower().replace("-", " ").split():
        clauses.append("replace(lower(subcategory), '-', ' ') LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape(word)}%")
    if min_height_in is not None:
        clauses.append("height_in_min >= ?")
        params.append(min_height_in)
    if max_height_in is not None:
        clauses.append("height_in_max <= ?")
        params.append(max_height_in)
    if not clauses:
        return []
    if zone is not None:
        clauses.append("usda_zone_min <= ? AND usda_zone_max >= ?")
        params += [zone, zone]
    where = " AND ".join(clauses)
    phrase = " ".join(words)
    # Exact name first, then names starting with the query, then shortest.
    tail = "ORDER BY name_lc = ? DESC, name_lc LIKE ? ESCAPE '\\' DESC, "
    tail += "length(common_name), common_name LIMIT ?"
    params += [phrase, f"{_escape(phrase)}%", limit]
    return _query(db_path, where, tuple(params), tail)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
