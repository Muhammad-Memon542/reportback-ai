"""Members of Toronto City Council - Voting Record (one CSV per term) -> vote table.

Not every vote is recorded electronically; absence of a vote row is not evidence of anything.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from datetime import date
from typing import Any

from reportback.db import Conn
from reportback.ingest.ckan import CkanClient
from reportback.ingest.http import PoliteClient
from reportback.ingest.ids import normalize_item_id

PACKAGE = "members-of-toronto-city-council-voting-record"
_RESULT = re.compile(r"^\s*(?P<label>[^,]+?)\s*,\s*(?P<yes>\d+)\s*-\s*(?P<no>\d+)\s*$")


def parse_result(raw: str) -> tuple[str, int | None, int | None]:
    """'Carried, 25-1' -> ('Carried', 25, 1); 'Lost (tie), 12-12' -> ('Lost (tie)', 12, 12)."""
    m = _RESULT.match(raw or "")
    if not m:
        return (raw or "").strip(), None, None
    return m.group("label"), int(m.group("yes")), int(m.group("no"))


def parse_vote_date(raw: str) -> date | None:
    """'2022-11-23 15:17 PM' (24-hour clock with a stray AM/PM) or '2026-08-05' -> the date."""
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", raw or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_vote_rows(text: str, term: str) -> list[tuple[Any, ...]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    seen: Counter[str] = Counter()
    out: list[tuple[Any, ...]] = []
    for r in rows:
        fields = [r.get(k, "") or "" for k in (
            "Term", "First Name", "Last Name", "Committee", "Date/Time", "Agenda Item #",
            "Agenda Item Title", "Motion Type", "Vote", "Result", "Vote Description",
        )]  # fmt: skip
        h = hashlib.sha256("\x1f".join(fields).encode()).hexdigest()[:20]
        seen[h] += 1
        vote_id = f"{h}-{seen[h]}"  # occurrence suffix: identical rows stay distinct, re-runs stable
        _, first, last, committee, dt, item_raw, title, motion_type, vote, result, desc = fields
        ref = normalize_item_id(item_raw)
        label, yes, no = parse_result(result)
        out.append(
            (
                vote_id, term, ref.item_id if ref else (item_raw.strip() or None), title.strip() or None,
                committee.strip() or None, motion_type.strip() or None, desc.strip() or None,
                f"{first.strip()} {last.strip()}".strip(), vote.strip(), label or None, yes, no,
                parse_vote_date(dt), dt.strip() or None,
            )
        )  # fmt: skip
    return out


def load_votes(conn: Conn, rows: list[tuple[Any, ...]], term: str) -> int:
    """Replace the term's votes in one transaction (idempotent; nothing references vote rows)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM vote WHERE term = %s", (term,))
        cur.executemany(
            """INSERT INTO vote (vote_id, term, item_id, agenda_item_title, committee, motion_type,
                   vote_description, member, vote, result, yes_count, no_count, meeting_date,
                   vote_datetime_raw)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )
    return len(rows)


def ingest_votes(conn: Conn, http: PoliteClient, ckan: CkanClient, term: str) -> dict[str, Any]:
    pkg = ckan.package_show(PACKAGE)
    want = f"member-voting-record-{term}.csv"
    matches = [r for r in pkg.resources if r.name == want and r.format.upper() == "CSV"]
    if not matches:
        raise RuntimeError(f"resource {want!r} not found in {PACKAGE}")
    res = matches[0]
    fetched = http.fetch_cached(res.url, version=res.last_modified)
    rows = parse_vote_rows(fetched.content.decode("utf-8-sig"), term)
    n = load_votes(conn, rows, term)
    return {
        "term": term,
        "resource": res.name,
        "last_modified": res.last_modified,
        "rows": n,
        "items": len({r[2] for r in rows if r[2]}),
        "unparsed_item_ids": sorted({str(r[2]) for r in rows if r[2] and "." not in str(r[2])})[:10],
    }
