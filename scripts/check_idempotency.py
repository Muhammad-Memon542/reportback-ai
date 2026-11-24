"""Phase 1 acceptance: re-running every ingest step leaves the database unchanged.

    uv run python scripts/check_idempotency.py

Fingerprints each table (md5 over all rows, ordered by primary key, excluding fetch
timestamps), re-runs log / votes / tmmis (re-parse from the raw cache, no new requests) /
background (re-parse cached PDFs) / citegraph, and fingerprints again.
"""

from __future__ import annotations

import sys

from reportback.config import get_settings
from reportback.db import Conn, connect
from reportback.ingest.background import ingest_background
from reportback.ingest.citegraph import build_citegraph
from reportback.ingest.ckan import CkanClient
from reportback.ingest.divisions import default_mapper
from reportback.ingest.http import PoliteClient
from reportback.ingest.report_log import SnapshotStore, ingest_report_log
from reportback.ingest.tmmis import crawl_items
from reportback.ingest.votes import ingest_votes

TABLES = {  # table -> (order by, columns to exclude)
    "log_snapshot": ("snapshot_id", {"ingested_at"}),
    "directive": ("directive_id", set()),
    "log_entry": ("snapshot_id, row_no", set()),
    "vote": ("vote_id", set()),
    "item": ("item_id", {"fetched_at"}),
    "item_consideration": ("item_id, seq", set()),
    "doc": ("doc_id", set()),
    "doc_section": ("doc_id, seq", set()),
    "cites": ("from_id, to_item_id", set()),
}


def fingerprint(conn: Conn) -> dict[str, tuple[int, str]]:
    out = {}
    for table, (order, exclude) in TABLES.items():
        cols = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            if r[0] not in exclude and r[0] not in ("emb", "tsv")
        ]
        row = conn.execute(
            f"SELECT count(*), coalesce(md5(string_agg(row({', '.join(cols)})::text, '|'"
            f" ORDER BY {order})), '') FROM {table}"
        ).fetchone()
        out[table] = (int(row[0]), str(row[1])) if row else (0, "")
    return out


def main() -> int:
    s = get_settings()
    with connect(s) as conn, PoliteClient(s) as http:
        before = fingerprint(conn)
        ingest_report_log(conn, SnapshotStore(s.snapshots_dir / "report_request_log"), default_mapper())
        ingest_votes(conn, http, CkanClient(http, s.ckan_base_url), "2022-2026")
        # Re-parse only items already fetched (all served from data/raw): no new pages.
        crawl_items(conn, http, term="2022-2026", reparse=True)
        ingest_background(conn, http, reparse=True)  # cached PDFs only
        build_citegraph(conn)
        after = fingerprint(conn)
    ok = True
    for t in TABLES:
        same = before[t] == after[t]
        ok &= same
        print(f"{t:20s} rows {before[t][0]:>7,} -> {after[t][0]:>7,}  {'unchanged' if same else 'CHANGED'}")
    print("IDEMPOTENT" if ok else "NOT IDEMPOTENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
