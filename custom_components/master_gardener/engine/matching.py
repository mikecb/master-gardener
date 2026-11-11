"""Which catalogue plant a name most likely means.

Shared by the Add plantings form and the seed inventory.
"""

from __future__ import annotations

import re
from collections import Counter

from .plantdb import name_forms


def names_match(typed: str, name: str) -> bool:
    """Whether what was typed is the plant's name, allowing for a plural."""
    return name.lower() in name_forms(typed)


def _species(record: dict) -> tuple[str, str]:
    words = record["scientific_name"].replace(chr(215), " ").split()
    words = [w for w in words if w.lower() != "x"]
    return (words[0] if words else "", " ".join(words[:2]))


def rank_candidates(typed: str, candidates: list[dict]) -> list[dict]:
    """Likeliest first.

    A plant named for what was typed ("Buddy Boxwood", not "Boxwood Basil"),
    then one of the genus and species most matches share (a Buxus, not the
    African boxwood), then the shortest name.
    """
    forms = name_forms(typed)
    genera = Counter(_species(c)[0] for c in candidates)
    species = Counter(_species(c)[1] for c in candidates)

    def key(record: dict) -> tuple:
        name = record["common_name"].lower().split(" (")[0]
        genus, binomial = _species(record)
        return (
            not any(name.endswith(form) for form in forms),
            -genera[genus],
            -species[binomial],
            len(name),
        )

    return sorted(candidates, key=key)


# Words that describe the packet or the plant's form, not which plant it is:
# "Organic Mustard Mix", "Rainier Cherry Tree".
PACKET_WORDS = {
    "mix",
    "mixed",
    "blend",
    "organic",
    "heirloom",
    "seed",
    "seeds",
    "tree",
    "trees",
    "bush",
    "bushes",
    "plant",
    "plants",
}


def plant_words(text: str) -> list[str]:
    """A packet name without its packet words: 'Organic Mustard Mix' ->
    ['mustard']. Left as it is if nothing would be left."""
    words = text.lower().split()
    kept = [w for w in words if w not in PACKET_WORDS]
    return kept or words


def shorter_names(text: str) -> list[str]:
    """Ever shorter tails of a name, head noun last: 'Blush Cherry Tomato' ->
    ['cherry tomato', 'tomato']. Variety names lead, the plant comes last."""
    words = plant_words(text)
    return [" ".join(words[i:]) for i in range(1, len(words))]


def is_a(record: dict, text: str) -> bool:
    """Whether a plant is the kind the name ends with: a 'Lodi Pumpkin' must
    be some pumpkin, an 'Organic Mustard Mix' some mustard."""
    words = plant_words(text)
    name = record["common_name"].lower().split(" (")[0]  # not the aside
    return any(
        re.search(rf"\b{re.escape(form)}(e?s)?\b", name)
        for form in name_forms(words[-1])
    )
