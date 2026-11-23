"""Coverage report: row counts and parse quality per source, written to docs/COVERAGE.md."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reportback.config import Settings
from reportback.db import Conn


def _rows(conn: Conn, sql: str, params: Any = None) -> list[tuple[Any, ...]]:
    return conn.execute(sql, params).fetchall()


def _one(conn: Conn, sql: str, params: Any = None) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _table(headers: list[str], rows: list[tuple[Any, ...]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += [
        "| " + " | ".join("" if v is None else f"{v:,}" if isinstance(v, int) else str(v) for v in r) + " |"
        for r in rows
    ]
    return "\n".join(out)


def _pct(n: int, d: int) -> str:
    return f"{n / d:.1%}" if d else "n/a"


def build_report(conn: Conn) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        "# Coverage report",
        "",
        f"Generated {now} by `uv run reportback coverage` from the local database. Re-running every"
        " ingest command produces the same counts (idempotent loads; see tests).",
        "",
        "Contains information licensed under the Open Government Licence – Toronto.",
    ]

    # --- Report Request Log
    parts += ["", "## 1. Report Request Log", ""]
    parts.append(_table(
        ["term", "snapshot as of", "file rows", "archived file"],
        _rows(conn, "SELECT term, as_of, n_rows, path FROM log_snapshot ORDER BY term, as_of"),
    ))  # fmt: skip
    total = _one(conn, "SELECT count(*) FROM directive") or 0
    entries = _one(conn, "SELECT count(*) FROM log_entry") or 0
    parts += [
        "",
        f"{entries:,} log rows → **{total:,} directives** (rows describing the same item + text are merged; every row is kept in `log_entry`).",
        "",
    ]
    parts.append(_table(
        ["term", "directives", "closed", "ncf", "mixed", "unknown", "with closing item", "with ≥1 division", "with due date"],
        _rows(conn, """SELECT term, count(*),
                   count(*) FILTER (WHERE log_status_norm = 'closed'),
                   count(*) FILTER (WHERE log_status_norm = 'ncf'),
                   count(*) FILTER (WHERE log_status_norm = 'mixed'),
                   count(*) FILTER (WHERE log_status_norm = 'unknown'),
                   count(*) FILTER (WHERE cardinality(closed_by_item) > 0),
                   count(*) FILTER (WHERE cardinality(divisions) > 0),
                   count(*) FILTER (WHERE due_date IS NOT NULL)
                FROM directive GROUP BY term ORDER BY term"""),
    ))  # fmt: skip
    parts += [
        "",
        "**How `due_date` was derived** (`due_rule`; NULL due dates are never treated as overdue):",
        "",
    ]
    parts.append(
        _table(
            ["rule", "directives"],
            _rows(conn, "SELECT due_rule, count(*) FROM directive GROUP BY 1 ORDER BY 2 DESC"),
        )
    )
    unparsed = _rows(conn, "SELECT item_number_raw FROM directive WHERE item_id IS NULL ORDER BY 1")
    parts += [
        "",
        f"Item numbers that could not be normalized: {', '.join(repr(r[0]) for r in unparsed) or 'none'}.",
    ]
    other = _one(conn, "SELECT count(*) FROM directive WHERE cardinality(closed_by_other) > 0") or 0
    parts += [
        f"Directives whose “Closed by” cites something other than an item (briefing note, date, part number): {other:,}."
    ]

    # --- Votes
    parts += ["", "## 2. Voting record", ""]
    parts.append(_table(
        ["term", "vote rows", "items", "first meeting", "last meeting"],
        _rows(conn, "SELECT term, count(*), count(DISTINCT item_id), min(meeting_date), max(meeting_date) FROM vote GROUP BY 1 ORDER BY 1"),
    ))  # fmt: skip
    parts += [
        "",
        "Not every Council vote is recorded electronically; a missing vote row is not evidence of anything.",
    ]

    # --- TMMIS
    parts += ["", "## 3. TMMIS agenda items", ""]
    parts.append(
        _table(
            ["fetch status", "items"],
            _rows(conn, "SELECT fetch_status, count(*) FROM item GROUP BY 1 ORDER BY 2 DESC"),
        )
    )
    seeds = _rows(
        conn,
        """SELECT 'directive items', count(DISTINCT d.item_id), count(DISTINCT i.item_id)
                             FROM directive d LEFT JOIN item i ON i.item_id = d.item_id AND i.fetch_status = 'ok'
                            WHERE d.term = '2022-2026' AND d.item_id IS NOT NULL
                           UNION ALL
                           SELECT 'closing items (ground truth)', count(DISTINCT c), count(DISTINCT i.item_id)
                             FROM directive d CROSS JOIN unnest(d.closed_by_item) c
                             LEFT JOIN item i ON i.item_id = c AND i.fetch_status = 'ok'
                            WHERE d.term = '2022-2026'
                           UNION ALL
                           SELECT 'voted items', count(DISTINCT v.item_id), count(DISTINCT i.item_id)
                             FROM vote v LEFT JOIN item i ON i.item_id = v.item_id AND i.fetch_status = 'ok'
                            WHERE v.term = '2022-2026'""",
    )
    parts += ["", "2022–2026 seeds and how many were fetched:", ""]
    parts.append(_table(["seed source", "distinct IDs", "fetched ok"], seeds))
    parts += ["", _table(
        ["", "count"],
        [("considerations (committee/Council cards)", _one(conn, "SELECT count(*) FROM item_consideration") or 0),
         ("items with a decision text", _one(conn, "SELECT count(*) FROM item WHERE decision_text IS NOT NULL") or 0),
         ("public communications (counted, not stored)", _one(conn, "SELECT coalesce(sum(communications_count),0)::int FROM item") or 0),
         ("speakers/deputants (counted, not stored)", _one(conn, "SELECT coalesce(sum(speakers_count),0)::int FROM item") or 0)],
    )]  # fmt: skip

    # --- Docs
    parts += ["", "## 4. Background files", ""]
    parts.append(_table(
        ["kind", "listed", "downloaded ok", "skipped", "errors", "not found", "pages"],
        _rows(conn, """SELECT kind, count(*), count(*) FILTER (WHERE fetch_status = 'ok'),
                   count(*) FILTER (WHERE fetch_status = 'skipped'), count(*) FILTER (WHERE fetch_status = 'error'),
                   count(*) FILTER (WHERE fetch_status = 'not_found'), coalesce(sum(pages), 0)::int
                FROM doc GROUP BY kind ORDER BY 2 DESC"""),
    ))  # fmt: skip
    nsec = _one(conn, "SELECT count(*) FROM doc_section") or 0
    parts += ["", f"{nsec:,} sections extracted. Most common section headings:", ""]
    parts.append(
        _table(
            ["section", "count"],
            _rows(conn, "SELECT section, count(*) FROM doc_section GROUP BY 1 ORDER BY 2 DESC LIMIT 15"),
        )
    )
    with_dh = (
        _one(conn, "SELECT count(DISTINCT doc_id) FROM doc_section WHERE section = 'DECISION HISTORY'") or 0
    )
    ok_reports = _one(conn, "SELECT count(*) FROM doc WHERE kind = 'report' AND fetch_status = 'ok'") or 0
    parts += [
        "",
        f"Staff reports with a DECISION HISTORY section: {with_dh:,} of {ok_reports:,} downloaded reports ({_pct(with_dh, ok_reports)}).",
    ]

    # --- Cites
    parts += ["", "## 5. Citation graph", ""]
    parts.append(
        _table(
            ["from", "method", "edges"],
            _rows(conn, "SELECT from_type, method, count(*) FROM cites GROUP BY 1, 2 ORDER BY 1, 2"),
        )
    )
    targets = _one(conn, "SELECT count(DISTINCT to_item_id) FROM cites") or 0
    parts += [
        "",
        f"Distinct cited items: {targets:,}. Precision on 100 hand-checked matches: see `evals/citegraph_precision.csv` and the README.",
    ]

    # --- Preview for Phase 3
    pairs = _rows(
        conn,
        """
        WITH gt AS (
          SELECT d.directive_id, d.item_id AS dir_item, c AS close_item
            FROM directive d CROSS JOIN unnest(d.closed_by_item) c
           WHERE d.term = '2022-2026' AND d.log_status_norm = 'closed' AND d.item_id IS NOT NULL)
        SELECT count(*),
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item i WHERE i.item_id = gt.close_item AND i.fetch_status = 'ok')),
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM cites x LEFT JOIN doc dd ON dd.doc_id = x.from_id
                    WHERE x.to_item_id = gt.dir_item
                      AND (x.from_id = gt.close_item OR dd.item_id = gt.close_item)))
          FROM gt""",
    )
    n, fetched, cited = pairs[0] if pairs else (0, 0, 0)
    parts += ["", "## 6. Preview: does the closing item cite the directive's item?", "",
              "For closed 2022–2026 directives, each (directive item → closing item) pair from the Clerk's log is a"
              " ground-truth link. Phase 3 measures linking properly; this is only the raw cite-edge signal.", "",
              _table(["ground-truth pairs", "closing item fetched", "closing item (or its reports) cites the directive's item"],
                     [(n, fetched, f"{cited:,} ({_pct(cited, fetched)})")])]  # fmt: skip
    return "\n".join(parts) + "\n"


def write_coverage_report(conn: Conn, settings: Settings, out: str) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(conn), encoding="utf-8")
    return path
