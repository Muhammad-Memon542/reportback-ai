"""Parse the log's "Report Back Timing" into a due date, conservatively.

The due date is the END of the stated window ("Q2 2024" -> 2024-06-30). Anything we cannot
pin down (budget-relative, recurring, "next meeting", ambiguous numeric dates) gets
due_date=None plus a rule label saying why, so nothing is reported as overdue on a guess.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
SEASON_END = {"winter": (3, 20), "spring": (6, 20), "summer": (9, 22), "fall": (12, 21), "autumn": (12, 21)}


@dataclass(frozen=True)
class Timing:
    due_date: date | None
    rule: str


def _eom(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _quarter_after(d: date, q: int) -> date:
    m, day = QUARTER_END[q]
    end = date(d.year, m, day)
    return end if end >= d else date(d.year + 1, m, day)


def _numeric_dates(s: str) -> list[date | None]:
    """Dates like 31/03/2024, 06/30/2024, 31-12-2024, 2024-07-11. None = ambiguous d/m."""
    out: list[date | None] = []
    for m in re.finditer(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", s):
        y, mo, d = (int(x) for x in m.groups())
        try:
            out.append(date(y, mo, d))
        except ValueError:
            continue
    for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d\d)\b", s):
        a, b, y = (int(x) for x in m.groups())
        if a > 12 >= b:
            cand = (y, b, a)  # day first
        elif b > 12 >= a:
            cand = (y, a, b)  # month first
        elif a == b:
            cand = (y, a, b)
        else:
            out.append(None)
            continue
        try:
            out.append(date(*cand))
        except ValueError:
            continue
    for m in re.finditer(rf"\b(\d{{1,2}})[-\s]({_MONTH_RE})[a-z]*[-\s,]+(20\d\d)\b", s):
        out.append(date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))))
    for m in re.finditer(rf"\b({_MONTH_RE})[a-z]*\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b", s):
        try:
            out.append(date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))))
        except ValueError:
            continue
    return out


def parse_timing(value: object, meeting_date: date | None = None) -> Timing:
    if value is None:
        return Timing(None, "missing")
    if isinstance(value, datetime):
        return Timing(value.date(), "date")
    if isinstance(value, date):
        return Timing(value, "date")
    s = " ".join(str(value).split()).lower().replace("’", "'")
    if not s:
        return Timing(None, "missing")
    if re.fullmatch(r"not\s+indicated\.?|n/?a|tbd|none", s):
        return Timing(None, "not_indicated")
    if re.search(
        r"\b(annual|annually|quarterly|semi-annual|semi-annually|bi-annual|bi-annually|"
        r"biannually|twice a year|monthly|ongoing|every|each|yearly)\b",
        s,
    ):
        return Timing(None, "recurring")
    if "budget" in s:
        return Timing(None, "budget_relative")
    if re.search(r"\b(earliest|soonest|as soon as|next\b.*\bmeeting|first (regular )?meeting)", s):
        return Timing(None, "meeting_relative")
    if re.match(
        r"(if|when|once|following|subsequent|after|upon|as needed|as necessary|as required|"
        r"as part of|as further|in due course|new term|through regular)\b",
        s,
    ):
        return Timing(None, "conditional")
    rel = re.fullmatch(r"(?:in|within)\s+(one|two|three|six|twelve|\d{1,2})\s+(year|month)s?(?:'? time)?", s)
    if rel and meeting_date is not None:
        words = {"one": 1, "two": 2, "three": 3, "six": 6, "twelve": 12}
        n = words.get(rel.group(1)) or int(rel.group(1))
        months = n * 12 if rel.group(2) == "year" else n
        y, m = divmod(meeting_date.month - 1 + months, 12)
        target_y, target_m = meeting_date.year + y, m + 1
        day = min(meeting_date.day, calendar.monthrange(target_y, target_m)[1])
        return Timing(date(target_y, target_m, day), "after_meeting")

    dates = _numeric_dates(s)
    if dates:
        # Any ambiguous d/m date could be the earliest deadline, so don't guess around it.
        if any(d is None for d in dates):
            return Timing(None, "ambiguous_date")
        return Timing(min(d for d in dates if d is not None), "date_text")

    q = re.search(r"\bq([1-4])\b\W*(?:of\s+)?(20\d\d)\b", s)
    if q:
        quarters = [(int(y), int(n)) for n, y in re.findall(r"\bq([1-4])\b\W*(?:of\s+)?(20\d\d)\b", s)]
        y, n = min(quarters)
        return Timing(date(y, *QUARTER_END[n]), "quarter")
    q = re.search(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter(?:\s+of)?\s*(20\d\d)?", s)
    if q:
        n = ORDINALS[q.group(1)]
        if q.group(2):
            return Timing(date(int(q.group(2)), *QUARTER_END[n]), "quarter")
        if meeting_date is not None:
            return Timing(_quarter_after(meeting_date, n), "quarter_after_meeting")
        return Timing(None, "unparsed")
    h = re.search(r"\b(first|second|1st|2nd)\s+half(?:\s+of)?\s+(20\d\d)\b", s)
    if h:
        y = int(h.group(2))
        return Timing(date(y, 6, 30) if h.group(1) in ("first", "1st") else date(y, 12, 31), "half")
    se = re.search(r"\b(winter|spring|summer|fall|autumn)(?:\s+of)?\s+(20\d\d)\b", s)
    if se:
        return Timing(date(int(se.group(2)), *SEASON_END[se.group(1)]), "season")
    mo = re.search(rf"\b({_MONTH_RE})[a-z]*\.?,?\s+(20\d\d)\b", s)
    if mo:
        return Timing(_eom(int(mo.group(2)), MONTHS[mo.group(1)]), "month")
    ap = re.search(r"\b(early|mid|late)[\s-]+(20\d\d)\b", s)
    if ap:
        y = int(ap.group(2))
        return Timing(
            {"early": date(y, 4, 30), "mid": date(y, 8, 31), "late": date(y, 12, 31)}[ap.group(1)], "approx"
        )
    yr = re.fullmatch(
        r"(?:(?:by|in|year|through|before|prior to)\s+)?(?:(?:the\s+)?end\s+of\s+)?(20\d\d)\.?", s
    )
    if yr:
        return Timing(date(int(yr.group(1)), 12, 31), "year")
    return Timing(None, "unparsed")
