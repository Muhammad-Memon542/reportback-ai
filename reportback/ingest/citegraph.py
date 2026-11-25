"""Citation graph: later items, staff-report sections and directives that mention item IDs.

Full IDs ('2023.EX1.4') are matched directly. Short forms ('EX1.4', 'Item PH2.1') are only
accepted when the committee code is a real one and the year label can be pinned down: the
nearest preceding date ("At its meeting on December 14 and 15, 2022, City Council adopted
Item EX1.4") narrows the year to {Y, Y+1}, and the meeting index (codes/meeting numbers seen
in well-formed IDs) must leave exactly one candidate. Ambiguous short forms are skipped.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from reportback.db import Conn
from reportback.ingest.ids import FULL_ID, MeetingIndex, infer_year, year_label_for_date
from reportback.ingest.text import norm_ws
from reportback.ingest.tmmis import parse_first_date

SHORT_IN_TEXT = re.compile(r"(?<![\w.])([A-Z]{2,4})(\d{1,3})\.(\d{1,3})(?![\w]|\.\d)")
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE_ANY = re.compile(
    rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:(?:,\s*(?:and\s+)?|\s+and\s+|\s*-\s*)\d{{1,2}})*,?\s+\d{{4}}"
)  # incl. the serial comma: 'July 14, 15, and 16, 2021' (precision sample row 39)
_MONTH_YEAR = re.compile(rf"\b({_MONTHS})\s+(\d{{4}})\b")  # 'In May 2017, through MM29.47'
_MONTH_NO = {m: i for i, m in enumerate(_MONTHS.split("|"), start=1)}
HINT_WINDOW = 400
CONTEXT = 150


@dataclass(frozen=True)
class Citation:
    item_id: str
    method: str  # full | short_inferred
    raw: str
    start: int
    end: int
    context: str


def _hint_before(text: str, pos: int) -> date | None:
    """The date mentioned closest before `pos`: 'March 20, 2024' or 'May 2017'."""
    window = text[max(0, pos - HINT_WINDOW) : pos]
    best: tuple[int, date] | None = None
    for m in _DATE_ANY.finditer(window):
        d = parse_first_date(m.group(0))
        if d is not None and (best is None or m.end() > best[0]):
            best = (m.end(), d)
    for m in _MONTH_YEAR.finditer(window):
        if best is None or m.end() > best[0]:
            best = (m.end(), date(int(m.group(2)), _MONTH_NO[m.group(1)], 1))
    return best[1] if best else None


def extract_citations(
    text: str, index: MeetingIndex, codes: set[str], max_year: int | None = None
) -> tuple[list[Citation], int]:
    """Return (citations in order of appearance, number of ambiguous short forms skipped).

    `max_year`: year label of the citing document; a short form cannot resolve to a later item.
    """
    out: list[Citation] = []
    skipped = 0

    def ctx(a: int, b: int) -> str:
        return norm_ws(text[max(0, a - CONTEXT) : b + CONTEXT])

    for m in FULL_ID.finditer(text):
        out.append(Citation(m.group(0), "full", m.group(0), m.start(), m.end(), ctx(m.start(), m.end())))
    for m in SHORT_IN_TEXT.finditer(text):
        code, meeting, item = m.group(1), int(m.group(2)), int(m.group(3))
        if code not in codes or meeting < 1 or item < 1:  # 'CR3.0' is an Official Plan land-use code
            continue
        year = infer_year(code, meeting, index, hint=_hint_before(text, m.start()))
        if year is None or (max_year is not None and year > max_year):
            skipped += 1
            continue
        out.append(
            Citation(f"{year}.{code}{meeting}.{item}", "short_inferred", m.group(0), m.start(), m.end(),
                     ctx(m.start(), m.end()))
        )  # fmt: skip
    out.sort(key=lambda c: c.start)
    return out, skipped


def _known_ids(conn: Conn) -> list[str]:
    rows = conn.execute(
        """SELECT item_id FROM item WHERE fetch_status = 'ok'
           UNION SELECT item_id FROM directive WHERE item_id IS NOT NULL
           UNION SELECT unnest(closed_by_item) FROM directive
           UNION SELECT item_id FROM vote WHERE item_id IS NOT NULL"""
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _sources(conn: Conn) -> Iterable[tuple[str, str, str | None, date | None, str]]:
    """(from_type, from_id, own item_id to exclude, date of the citing text, text)."""
    for item_id, when, text in conn.execute(
        """SELECT i.item_id, coalesce(i.council_date, i.meeting_date),
                  concat_ws(E'\\n', i.origin, i.summary, i.decision_text,
                  (SELECT string_agg(c.decision_text, E'\\n' ORDER BY c.seq)
                     FROM item_consideration c WHERE c.item_id = i.item_id))
           FROM item i WHERE i.fetch_status = 'ok'"""
    ):
        yield "item", item_id, item_id, when, text or ""
    for doc_id, item_id, when, text in conn.execute(
        """SELECT s.doc_id, d.item_id, coalesce(d.report_date, i.meeting_date),
                  string_agg(s.text, E'\\n' ORDER BY s.seq)
           FROM doc_section s JOIN doc d USING (doc_id) LEFT JOIN item i ON i.item_id = d.item_id
           GROUP BY s.doc_id, d.item_id, d.report_date, i.meeting_date"""
    ):
        yield "doc", doc_id, item_id, when, text or ""
    for directive_id, item_id, when, text in conn.execute(
        "SELECT directive_id, item_id, meeting_date, directive_text FROM directive"
    ):
        yield "directive", directive_id, item_id, when, text or ""


def build_citegraph(conn: Conn) -> dict[str, Any]:
    """Rebuild the cites table from scratch (derived data, so a rebuild is idempotent)."""
    index = MeetingIndex(_known_ids(conn))
    codes = {code for code, _ in index._years}
    fetched: dict[str, str] = {r[0]: r[1] for r in conn.execute("SELECT item_id, fetch_status FROM item")}
    edges: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    skipped = repaired = dropped = 0
    for from_type, from_id, own_item, when, text in list(_sources(conn)):
        max_year = year_label_for_date(when) if when else None
        cites, amb = extract_citations(text, index, codes, max_year)
        skipped += amb
        for c in cites:
            target, method = c.item_id, c.method
            if fetched.get(target) == "not_found":
                # Footnote markers glue onto IDs ('2025.EX20.12' + footnote 3 = '2025.EX20.123'):
                # if the ID minus its last digit exists, that is what the text cites.
                shorter = target[:-1] if not target.endswith(".") else ""
                if shorter and not shorter.endswith(".") and fetched.get(shorter) == "ok":
                    target, method = shorter, f"{method}_footnote_repaired"
                    repaired += 1
                else:
                    dropped += 1  # TMMIS has no such item (typo in the source or bad inference)
                    continue
            if target == own_item:
                continue  # an item/report mentioning its own item number is not a citation
            edges.setdefault((from_id, target), (from_type, method, c.raw, c.context))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM cites")
        cur.executemany(
            "INSERT INTO cites (from_id, to_item_id, from_type, method, raw, context) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [(f, t, *v) for (f, t), v in edges.items()],
        )
    by = Counter((v[0], v[1]) for v in edges.values())
    return {
        "edges": len(edges),
        "by_source_and_method": {f"{a}/{b}": n for (a, b), n in sorted(by.items())},
        "ambiguous_short_forms_skipped": skipped,
        "footnote_repaired": repaired,
        "dropped_nonexistent_target": dropped,
        "unverified_target_edges": len([t for (_, t) in edges if fetched.get(t) is None]),
        "meeting_index_size": len(index),
    }
