"""TMMIS agenda item IDs: parsing, repair of malformed IDs, and year inference for short forms.

Full form: ``2023.EX3.1`` = <year label>.<committee code><meeting no>.<item no>.
The year label is NOT always the calendar year of the meeting: a new term's first meetings
(Nov/Dec of an election year) are labelled with the following year (the 2022-11-24 Council
meeting is ``2023.CC1``). Meeting numbers restart each term, so ``EX1`` exists in 2019 and 2023.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

FULL_ID = re.compile(r"\b(20\d\d)\.([A-Z]{2,4})(\d{1,3})\.(\d{1,3})\b")
_REPAIRABLE = re.compile(r"^(20\d\d)(\d)?\s*[.\-]?\s*([A-Z]{2,4})\s*\.?\s*(\d{1,3})\s*\.\s*(\d{1,3})\.?$")
SHORT_ID = re.compile(r"^([A-Z]{2,4})(\d{1,3})\.(\d{1,3})\.?$")

ELECTION_YEARS = frozenset({2006, 2010, 2014, 2018, 2022, 2026, 2030})


def term_years(term: str) -> tuple[int, ...]:
    """'2022-2026' -> year labels used by that term's items: (2023, 2024, 2025, 2026)."""
    start, end = (int(x) for x in term.split("-"))
    return tuple(range(start + 1, end + 1))


def term_for_year_label(year: int) -> str:
    """Year label 2023..2026 -> '2022-2026'."""
    start = max(y for y in ELECTION_YEARS if y < year)
    return f"{start}-{start + 4}"


def year_label_for_date(d: date) -> int:
    """Best guess of the item-ID year label for a meeting held on `d`."""
    if d.year in ELECTION_YEARS and d.month >= 11:
        return d.year + 1
    return d.year


@dataclass(frozen=True)
class ItemRef:
    item_id: str
    method: str  # exact | repaired | inferred_year


class MeetingIndex:
    """(committee code, meeting no) -> year labels seen in well-formed IDs."""

    def __init__(self, ids: Iterable[str] = ()) -> None:
        self._years: dict[tuple[str, int], set[int]] = defaultdict(set)
        for i in ids:
            self.add(i)

    def add(self, item_id: str) -> None:
        m = FULL_ID.fullmatch(item_id)
        if m:
            self._years[(m.group(2), int(m.group(3)))].add(int(m.group(1)))

    def years(self, code: str, meeting_no: int) -> set[int]:
        return set(self._years.get((code, meeting_no), set()))

    def __len__(self) -> int:
        return len(self._years)


def infer_year(
    code: str,
    meeting_no: int,
    index: MeetingIndex,
    *,
    allowed_years: Iterable[int] | None = None,
    hint: date | None = None,
) -> int | None:
    """Pick the year label for a short-form ID, or None if it is ambiguous/unknown."""
    candidates = index.years(code, meeting_no)
    if allowed_years is not None:
        candidates &= set(allowed_years)
    if hint is not None:
        near = {hint.year, hint.year + 1}
        if candidates:
            candidates &= near
        elif allowed_years is None:
            # No index entry: trust the date's year, except across a term boundary (Nov/Dec of
            # an election year), where boards/committees switch terms at different times.
            if hint.year in ELECTION_YEARS and hint.month >= 11:
                return None
            candidates = {hint.year}
    return candidates.pop() if len(candidates) == 1 else None


def normalize_item_id(
    raw: object,
    index: MeetingIndex | None = None,
    *,
    allowed_years: Iterable[int] | None = None,
    hint: date | None = None,
) -> ItemRef | None:
    """Normalize one item reference as written in the Clerk's log. Returns None if not an item."""
    if raw is None:
        return None
    s = str(raw).strip().rstrip(".").strip()
    if FULL_ID.fullmatch(s):
        return ItemRef(s, "exact")
    m = _REPAIRABLE.match(s)
    if m:
        year, stray, code, meeting, item = m.groups()
        candidate = f"{year}.{code}{int(meeting)}.{int(item)}"
        if stray is None:
            return ItemRef(candidate, "repaired")
        # e.g. '20263MM42.22': only accept if the meeting index confirms the year.
        if index is not None and int(year) in index.years(code, int(meeting)):
            return ItemRef(candidate, "repaired")
        return None
    m = SHORT_ID.match(s)
    if m and index is not None:
        code, meeting, item = m.group(1), int(m.group(2)), int(m.group(3))
        y = infer_year(code, meeting, index, allowed_years=allowed_years, hint=hint)
        if y is not None:
            return ItemRef(f"{y}.{code}{meeting}.{item}", "inferred_year")
    return None


def split_item_refs(
    raw: object,
    index: MeetingIndex | None = None,
    *,
    allowed_years: Iterable[int] | None = None,
) -> tuple[list[ItemRef], list[str]]:
    """Split a 'Closed by' cell into item refs and other references (briefing notes, dates...)."""
    if raw is None:
        return [], []
    if isinstance(raw, date):
        return [], [raw.isoformat()[:10]]
    text = str(raw).strip()
    if not text:
        return [], []
    refs: list[ItemRef] = []
    others: list[str] = []
    # Newline / comma / semicolon separate references; '2025 NY21.15' keeps its inner space.
    for part in re.split(r"[\n,;]+", text):
        part = part.strip()
        if not part:
            continue
        ref = normalize_item_id(part, index, allowed_years=allowed_years)
        if ref is not None:
            refs.append(ref)
            continue
        found = [ItemRef(m.group(0), "exact") for m in FULL_ID.finditer(part)]
        if found:
            refs.extend(found)
        else:
            others.append(part)
    unique: list[ItemRef] = []
    for r in refs:
        if all(u.item_id != r.item_id for u in unique):
            unique.append(r)
    return unique, others
