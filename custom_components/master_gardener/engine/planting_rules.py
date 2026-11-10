"""Turn one OpenPlantDB record into schedule rules and a care profile.

OpenPlantDB knows when to sow or plant and how long a crop takes. It knows
nothing about upkeep (pruning, feeding, mowing), so each plant is also matched
to one of our own care profiles in data/catalog.json.
"""

from __future__ import annotations

# OpenPlantDB anchors -> (our frost anchor, extra weeks). "spring" and "fall"
# are read as the frost dates; soil is workable roughly five weeks earlier.
ANCHORS = {
    "last_frost": ("last_frost", 0),
    "spring": ("last_frost", 0),
    "soil_workable": ("last_frost", -5),
    "first_frost": ("first_frost", 0),
    "fall": ("first_frost", 0),
}
# For seedlings started indoors the database gives the sowing time only. They
# go out, in weeks from the last frost, according to how much cold they take.
PLANT_OUT_WEEKS = {"tender": 1, "half-hardy": -2, "hardy": -4}
MIN_WEEKS_INDOORS = 4
# "Days to maturity" means a harvest only for things you eat.
EDIBLE = {"vegetable", "herb", "fruit", "berry"}


def rules_for(record: dict) -> list[dict]:
    """Schedule rules (see engine/schedule.py) for sowing, planting, harvest."""
    anchor, shift = ANCHORS.get(record["plant_anchor"], ("last_frost", 0))
    first = (record["plant_weeks_min"] or 0) + shift
    last = (record["plant_weeks_max"] or 0) + shift
    window = {
        "anchor": anchor,
        "offset_weeks": first,
        "window_weeks": max(1, round(last - first)),
        "detail": record.get("plant_note") or "",
    }

    if record["season"] == "perennial":
        # Planted once: the window recurs each year only until it is in the
        # ground. Days to maturity is years to bearing here, not a harvest date.
        return [
            {
                "key": "plant",
                "kind": "plant",
                "title": "Plant {name}",
                "until_planted": True,
                **window,
            }
        ]

    method = record["sow_method"]
    if method == "indoor-start":
        if anchor == "last_frost":
            out = max(
                PLANT_OUT_WEEKS.get(record["frost_tolerance"], 1),
                first + MIN_WEEKS_INDOORS,
            )
        else:
            out = first + MIN_WEEKS_INDOORS + 1
        rules = [
            {
                "key": "start",
                "kind": "sow_indoors",
                "title": "Start {name} indoors",
                **window,
            },
            {
                "key": "plant",
                "kind": "transplant",
                "title": "Plant {name} outdoors",
                "anchor": anchor,
                "offset_weeks": out,
                "window_weeks": 3,
                "detail": "Harden off for a week first.",
            },
        ]
        grows_from = "plant" if record["maturity_from"] == "transplant" else "start"
    elif method == "direct":
        rules = [
            {
                "key": "sow",
                "kind": "direct_sow",
                "title": "Sow {name} outdoors",
                **window,
            }
        ]
        grows_from = "sow"
    else:  # transplant, bulb, tuber, cutting, division
        kind = "transplant" if method == "transplant" else "plant"
        rules = [{"key": "plant", "kind": kind, "title": "Plant {name}", **window}]
        grows_from = "plant"

    soonest = record["maturity_days_min"]
    if record["category"] in EDIBLE and soonest:
        latest = record["maturity_days_max"] or soonest
        rules.append(
            {
                "key": "harvest",
                "kind": "harvest",
                "title": "Harvest {name}",
                "after": grows_from,
                "days": soonest,
                "window_weeks": max(2, round((latest - soonest) / 7) + 2),
            }
        )
    return rules


# Genus (or "genus species") -> care profile. Checked before the type words.
_BY_NAME = {
    "malus": "apple",
    "pyrus": "apple",
    "vaccinium": "blueberry",
    "fragaria": "strawberry",
    "acer palmatum": "japanese_maple",
    "acer japonicum": "japanese_maple",
    "acer shirasawanum": "japanese_maple",
    "pinus": "pine",
    "picea": "conifer",
    "abies": "conifer",
    "tsuga": "conifer",
    "juniperus": "conifer",
    "thuja": "conifer",
    "cedrus": "conifer",
    "chamaecyparis": "conifer",
    "cupressus": "conifer",
    "cryptomeria": "conifer",
    "buxus": "boxwood",
    "ilex": "boxwood",
    "taxus": "boxwood",
    "rhododendron": "azalea",
    "camellia": "azalea",
    "pieris": "azalea",
    "kalmia": "azalea",
    "hydrangea paniculata": "hydrangea",
    "hydrangea arborescens": "hydrangea",
    "rosa": "rose",
    "lavandula": "lavender",
    "salvia rosmarinus": "lavender",
    "rosmarinus": "lavender",
    "santolina": "lavender",
    "narcissus": "spring_bulb",
    "tulipa": "spring_bulb",
    "crocus": "spring_bulb",
    "hyacinthus": "spring_bulb",
    "galanthus": "spring_bulb",
    "muscari": "spring_bulb",
}
# Words in the subcategory -> care profile, first match wins.
_BY_TYPE = (
    ("turfgrass", "lawn"),
    ("ornamental grass", "ornamental_grass"),
    ("conifer", "conifer"),
    ("evergreen tree", "conifer"),
    ("tree", "deciduous_tree"),
    ("fern", "perennial"),
    ("perennial", "perennial"),
    ("groundcover", "perennial"),
    ("daylily", "perennial"),
    ("iris", "perennial"),
)


def care_profile(record: dict) -> str | None:
    """The care profile whose upkeep tasks suit this plant, if any."""
    # Hybrids are written with a multiplication sign or a plain x.
    words = record["scientific_name"].lower().replace(chr(215), " ").split()
    words = [w for w in words if w != "x"]
    for name in (" ".join(words[:2]), words[0] if words else ""):
        if name in _BY_NAME:
            return _BY_NAME[name]
    if words and words[0] == "prunus" and record["category"] in ("fruit", "berry"):
        return "peach"
    subcategory = record["subcategory"].lower().replace("-", " ")
    if "turfgrass" in subcategory:
        return "lawn"
    if record["season"] != "perennial":
        return None
    for word, profile in _BY_TYPE:
        if word in subcategory:
            return profile
    return None
