from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from reportback.db import Conn
from reportback.ingest.ckan import Resource
from reportback.ingest.divisions import default_mapper
from reportback.ingest.report_log import (
    LogFormatError,
    SnapshotStore,
    ingest_report_log,
    read_workbook,
    status_norm,
    term_of,
)
from tests.conftest import FIXTURES

FIXTURE = FIXTURES / "report_log" / "odd_rows.xlsx"  # real rows copied verbatim from the log
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def resource(name: str = "Report Request Log 2022-2026", modified: str = "2026-11-13T13:52:59") -> Resource:
    return Resource(
        id="res-1", name=name, format="XLSX", url="https://example.invalid/x.xlsx", last_modified=modified
    )


def test_read_workbook_maps_newline_headers() -> None:
    rows = read_workbook(FIXTURE.read_bytes())
    assert len(rows) == 17
    first = rows[0]
    assert first.get("item_number") == "2023.TE1.30"
    assert set(first.values) >= {"status", "req_body", "meeting_date", "closed_by", "date_for_order"}


def test_read_workbook_rejects_wrong_layout(tmp_path: Path) -> None:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    assert wb.active is not None
    wb.active.title = "Main Data"
    wb.active.append(["Status", "Something else"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(LogFormatError, match="missing columns"):
        read_workbook(buf.getvalue())


def test_status_norm_and_term() -> None:
    assert [status_norm(s) for s in ("closed", "CLOSED", " Closed ", "NCF", None, "weird")] == [
        "closed", "closed", "closed", "ncf", "unknown", "unknown",
    ]  # fmt: skip
    assert term_of("Report Request Log 2018-2022") == "2018-2022"


def test_snapshot_store_archives_once_and_heartbeats(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "report_request_log")
    content = FIXTURE.read_bytes()
    info, new = store.save(resource(), content, NOW)
    assert new and info.as_of == date(2026, 11, 13) and info.n_rows == 17
    assert (store.root / info.path).read_bytes() == content
    assert info.path.startswith("2022-2026/2026-11-13_")
    _, again = store.save(resource(), content, NOW)
    assert not again and len(store.manifest()) == 1
    with (store.root / "2022-2026" / "latest.csv").open(newline="", encoding="utf-8") as f:
        recs = list(csv.DictReader(f))
    assert len(recs) == 17 and recs == sorted(
        recs, key=lambda r: (r["item_number"], r["directive"], r["closed_by"], r["assigned_to"])
    )
    assert store.heartbeat(date(2026, 11, 23)) is True  # first time
    assert store.heartbeat(date(2026, 12, 1)) is False  # < 30 days
    assert store.heartbeat(date(2026, 12, 24)) is True


def test_ingest_report_log(db: Conn, tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "report_request_log")
    store.save(resource(), FIXTURE.read_bytes(), NOW)
    stats = ingest_report_log(db, store, default_mapper())
    assert stats.rows == 17 and stats.directives == 17

    row = db.execute(
        "SELECT log_status, log_status_norm, closed_by_item, due_date, due_rule, divisions, first_seen "
        "FROM directive WHERE item_id = '2023.EX1.4'"
    ).fetchone()
    assert row is not None
    _, _, closed, due, rule, divisions, first_seen = row
    assert closed == ["2023.PH2.1", "2023.PH5.1"]  # ground-truth label, both items
    assert (due, rule) == (date(2023, 3, 31), "quarter")
    assert divisions and first_seen == date(2026, 11, 13)

    upper = db.execute(
        "SELECT log_status, log_status_norm FROM directive WHERE item_id = '2023.GG1.1'"
    ).fetchone()
    assert upper == ("CLOSED", "closed")  # verbatim status kept, normalized alongside

    bn = db.execute(
        "SELECT closed_by_item, closed_by_other FROM directive WHERE item_id = '2023.EX7.1'"
    ).fetchone()
    assert bn == ([], ["BN14-Jan 26"])
    assert db.execute("SELECT item_id FROM directive WHERE item_number_raw = '2023MM11.16'").fetchone() == (
        "2023.MM11.16",
    )

    # Idempotent: a second run changes nothing.
    before = db.execute(
        "SELECT md5(string_agg(directive_id || log_status_norm, ',' ORDER BY directive_id)) FROM directive"
    ).fetchone()
    stats2 = ingest_report_log(db, store, default_mapper())
    after = db.execute(
        "SELECT md5(string_agg(directive_id || log_status_norm, ',' ORDER BY directive_id)) FROM directive"
    ).fetchone()
    assert before == after and stats2.orphans_deleted == 0
    assert db.execute("SELECT count(*) FROM log_entry").fetchone() == (17,)
