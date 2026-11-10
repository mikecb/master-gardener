"""Adaptive maintenance: repeating jobs follow what the garden needs.

A repeating job (mowing, deadheading) starts at its care profile's interval
and is adjusted from what happens:

- Tighter (sooner) after two signals within 60 days that it came round too
  slowly: a reported problem that names it ("grass too long"), or doing it
  before it was due.
- Looser (later) after three completions in a row done after the window had
  closed with no complaint: it was not needed that often.

Each step is 15%, and the interval stays between half and double the usual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

EARLY = "early"
ON_TIME = "on_time"
LATE = "late"
REPORT = "report"
COMPLETIONS = (EARLY, ON_TIME, LATE)

TIGHTEN = 0.85
LOOSEN = 1.15
MIN_FACTOR = 0.5
MAX_FACTOR = 2.0
SIGNALS_TO_TIGHTEN = 2
LATE_TO_LOOSEN = 3
RECENT = timedelta(days=60)

# Words in a reported problem that point at a kind of repeating job.
REPORT_WORDS = {
    "mow": ("grass", "lawn", "mow", "mowing", "mown"),
    "deadhead": ("deadhead", "spent", "faded", "dead flowers", "dead blooms"),
}


@dataclass(frozen=True)
class Signal:
    day: date
    kind: str  # EARLY, ON_TIME, LATE or REPORT
    note: str = ""  # a report's words

    def to_list(self) -> list:
        return [self.day.isoformat(), self.kind, self.note]

    @classmethod
    def from_list(cls, raw: list) -> Signal:
        return cls(date.fromisoformat(raw[0]), raw[1], raw[2] if len(raw) > 2 else "")


@dataclass(frozen=True)
class Adjustment:
    factor: float
    reason: str  # why, for the task's detail


def reported(kind: str, text: str) -> bool:
    """Whether a reported problem is about this kind of job."""
    text = text.lower()
    return any(
        re.search(rf"\b{re.escape(word)}", text) for word in REPORT_WORDS.get(kind, ())
    )


def adjust(
    current: Adjustment | None, signals: list[Signal], today: date
) -> tuple[Adjustment | None, list[Signal]]:
    """Apply the signals: a new adjustment (or the same), and those left over.

    Signals that led to a change are used up, so one bad month moves the
    interval one step, not several.
    """
    factor = current.factor if current else 1.0
    recent = [s for s in signals if today - s.day <= RECENT]
    sooner = [s for s in recent if s.kind in (REPORT, EARLY)]
    if len(sooner) >= SIGNALS_TO_TIGHTEN:
        reports = sum(1 for s in sooner if s.kind == REPORT)
        early = len(sooner) - reports
        why = []
        if reports:
            why.append(f"reported {reports} time{'s' if reports > 1 else ''}")
        if early:
            why.append(f"done early {early} time{'s' if early > 1 else ''}")
        return _step(factor, TIGHTEN, " and ".join(why)), []
    done = [s for s in recent if s.kind in COMPLETIONS]
    if len(done) >= LATE_TO_LOOSEN and all(
        s.kind == LATE for s in done[-LATE_TO_LOOSEN:]
    ):
        return _step(factor, LOOSEN, f"done late {LATE_TO_LOOSEN} times running"), []
    return current, recent


def _step(factor: float, by: float, why: str) -> Adjustment:
    new = min(MAX_FACTOR, max(MIN_FACTOR, factor * by))
    return Adjustment(factor=round(new, 4), reason=why)


def interval(every_days: int, adjustment: Adjustment | None) -> int:
    if adjustment is None:
        return every_days
    return max(1, round(every_days * adjustment.factor))


def describe(every_days: int, adjustment: Adjustment | None) -> str:
    """'Every 6 days, usually 7: reported 2 times.' Empty when not adjusted."""
    days = interval(every_days, adjustment)
    if adjustment is None or days == every_days:
        return ""
    return f"Every {days} days, usually {every_days}: {adjustment.reason}."
