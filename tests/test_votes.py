from __future__ import annotations

from datetime import date

from reportback.db import Conn
from reportback.ingest.votes import load_votes, parse_result, parse_vote_date, parse_vote_rows
from tests.conftest import FIXTURES

SAMPLE = (FIXTURES / "votes" / "sample_2022-2026.csv").read_text(encoding="utf-8")  # real rows


def test_parse_result_and_date() -> None:
    assert parse_result("Carried, 25-1") == ("Carried", 25, 1)
    assert parse_result("Lost (tie), 12-12") == ("Lost (tie)", 12, 12)
    assert parse_result("Withdrawn") == ("Withdrawn", None, None)
    assert parse_vote_date("2022-11-23 15:17 PM") == date(2022, 11, 23)  # 24h clock + stray PM
    assert parse_vote_date("2026-08-05") == date(2026, 8, 5)
    assert parse_vote_date("") is None


def test_parse_vote_rows_covers_real_shapes() -> None:
    rows = parse_vote_rows(SAMPLE, "2022-2026")
    assert len(rows) == 14
    assert {r[8] for r in rows} == {"Yes", "No", "Absent"}
    assert {r[9] for r in rows} == {"Carried", "Lost", "Lost (tie)"}
    assert all(r[12] is not None for r in rows)  # every Date/Time shape yields a date
    assert all(r[2] and r[2].count(".") == 2 for r in rows)  # item IDs normalized
    assert len({r[0] for r in rows}) == 14


def test_load_votes_is_idempotent(db: Conn) -> None:
    rows = parse_vote_rows(SAMPLE, "2022-2026")
    load_votes(db, rows, "2022-2026")
    load_votes(db, parse_vote_rows(SAMPLE, "2022-2026"), "2022-2026")
    assert db.execute("SELECT count(*) FROM vote").fetchone() == (14,)
