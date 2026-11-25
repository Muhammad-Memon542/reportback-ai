from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from reportback.db import Conn
from reportback.ingest.background import parse_pdf, save_sections
from reportback.ingest.citegraph import build_citegraph, extract_citations
from reportback.ingest.divisions import default_mapper
from reportback.ingest.ids import MeetingIndex
from reportback.ingest.text import EMAIL_TOKEN, decode_html
from reportback.ingest.tmmis import (
    ITEM_URL,
    classify_doc,
    parse_doc_description,
    parse_first_date,
    parse_item_page,
    parse_wards,
    save_item,
)
from tests.conftest import FIXTURES

TMMIS = FIXTURES / "tmmis"  # raw pages as served by secure.toronto.ca (2026-11-23)
PDF = FIXTURES / "background" / "backgroundfile-234496.pdf"  # staff report with DECISION HISTORY


def page(item_id: str):  # type: ignore[no-untyped-def]
    return parse_item_page(decode_html((TMMIS / f"{item_id}.html").read_bytes()), item_id)


# ----------------------------------------------------------------------------------- TMMIS


def test_community_council_item() -> None:
    p = page("2023.TE1.30")
    assert p.found and p.title == "Yonge Street and St. Clair Avenue Construction Hub Co-ordination"
    [c] = p.considerations
    assert (c.body, c.meeting_date, c.status, c.decision_type) == (
        "Toronto and East York Community Council", date(2022, 11, 30), "Adopted", "ACTION",
    )  # fmt: skip
    assert parse_wards(c.wards_raw) == ["12"]
    heading, text = c.decision
    assert (
        heading == "Community Council Decision"
        and text
        and "General Manager, Transportation Services" in text
    )
    [d] = p.docs
    assert (d.doc_id, d.kind, d.report_date, d.from_officials) == (
        "backgroundfile-230216", "letter", date(2022, 11, 23), ["Councillor Josh Matlow"],
    )  # fmt: skip


def test_committee_then_council_item_with_public_communications() -> None:
    p = page("2023.PH2.1")
    assert [c.body for c in p.considerations] == ["City Council", "Planning and Housing Committee"]
    assert p.considerations[0].meeting_date == date(2023, 3, 29)  # 'March 29, 30 and 31, 2023'
    assert p.considerations[0].decision[0] == "City Council Decision"
    assert {"Committee Recommendations", "Decision Advice and Other Information", "Origin", "Summary"} <= set(
        p.considerations[1].sections
    )
    kinds = {d.doc_id: d.kind for d in p.docs}
    assert kinds["backgroundfile-234496"] == "report" and kinds["backgroundfile-234497"] == "attachment"
    assert len(p.docs) == len(kinds)  # docs repeated under "(Committee)" headings are deduped
    # Members of the public: counted, never stored.
    assert p.communications_count == 2
    all_text = " ".join(t for c in p.considerations for t in c.sections.values())
    assert "Harbord Village" not in all_text and "Goodmans" not in all_text


def test_council_decision_nested_in_dinfo_is_found() -> None:
    p = page("2023.EX1.4")
    heading, text = p.considerations[0].decision
    assert heading == "City Council Decision"
    assert text and "City Council request the Chief Planner and Executive Director, City Planning" in text
    assert not any(t.lower().startswith("vote") for c in p.considerations for t in c.sections)


def test_member_motion_and_missing_item() -> None:
    p = page("2023.MM11.16")
    assert [c.body for c in p.considerations] == ["City Council"]
    assert p.docs[0].kind == "motion"
    missing = page("2023.EX99.99")
    assert not missing.found and missing.considerations == []


@pytest.mark.parametrize(
    ("desc", "kind"),
    [
        (
            "(February 9, 2023) Report from the Chief Planner and Executive Director, City Planning on X",
            "report",
        ),
        ("Supplementary Report from the City Manager on Y", "report"),
        ("Attachment 1: Recommended Official Plan Amendment", "attachment"),
        ("Public Attachment 6 - Compatibility & Mitigation Study", "attachment"),
        ("Confidential Appendix B - Confidential Information", "attachment"),
        ("(November 23, 2022) Letter from Councillor Josh Matlow - Construction Hub", "letter"),
        ("Briefing Note 7 - 2023 Capital Budget Briefing Note", "briefing_note"),
        ("2023 Capital and Operating Budget Notes - Toronto Water", "budget"),
        ("Mayoral Direction to City Officials to direct the City Manager", "mayoral_direction"),
        ("Member Motion MM11.16", "motion"),
        ("Revised Member Motion MM6.23", "motion"),
        ("Revised Report and Attachment 1 from the General Manager, Toronto Water", "report"),
        ("Ombudsman Toronto Report: An Investigation into the City", "report"),
        ("Answer from the City Manager on Administrative Inquiry", "answer"),
        ("Fiscal Impact Statement from the Chief Financial Officer", "financial_impact"),
        ("Financial Impact Summary Sheet for Member Motions MM19.1", "financial_impact"),
        ("Transmittal from the Director, Commission Services", "letter"),
        ("(February 3, 2023) Notice of Public Meeting", "notice"),
        ("(December 6, 2022) Presentation from the Chief Planner", "presentation"),
    ],
)
def test_classify_doc(desc: str, kind: str) -> None:
    assert classify_doc(desc) == kind


def test_doc_description_and_dates() -> None:
    desc = (
        "(February 9, 2023) Report from the City Manager and the Chief Financial Officer"
        " and Treasurer on Budget"
    )
    expected = (date(2023, 2, 9), ["City Manager", "Chief Financial Officer and Treasurer"], "Budget")
    assert parse_doc_description(desc) == expected
    assert parse_first_date("December 14 and 15, 2022") == date(2022, 12, 14)
    assert parse_first_date("no date here") is None


def test_save_item_is_idempotent(db: Conn) -> None:
    p = page("2023.PH2.1")
    url = ITEM_URL.format(item_id=p.item_id)
    for _ in range(2):
        save_item(db, p, url, "hash", default_mapper())
    row = db.execute(
        "SELECT committee, meeting_date, council_date, wards, communications_count, term FROM item"
    ).fetchone()
    assert row == (
        "Planning and Housing Committee",
        date(2023, 2, 28),
        date(2023, 3, 29),
        ["All"],
        2,
        "2022-2026",
    )
    assert db.execute("SELECT count(*) FROM item_consideration").fetchone() == (2,)
    report = db.execute("SELECT divisions FROM doc WHERE doc_id = 'backgroundfile-234496'").fetchone()
    assert report == (["City Planning", "Development Review"],)


# ----------------------------------------------------------------------------------- PDFs


def test_staff_report_sections() -> None:
    r = parse_pdf(PDF.read_bytes())
    assert r.pages == 6 and r.report_type == "REPORT FOR ACTION" and r.report_date == date(2023, 2, 9)
    assert r.header["from"] == "Chief Planner and Executive Director, City Planning"
    names = [s.section for s in r.sections]
    assert names == [
        "PREAMBLE", "SUMMARY", "RECOMMENDATIONS", "FINANCIAL IMPACT", "EQUITY IMPACT STATEMENT",
        "DECISION HISTORY", "COMMENTS", "CONTACT", "SIGNATURE", "ATTACHMENTS",
    ]  # fmt: skip
    dh = next(s for s in r.sections if s.section == "DECISION HISTORY")
    assert (dh.page_start, dh.page_end) == (2, 3)
    all_text = "\n".join(s.text for s in r.sections)
    assert "Page 1 of 6" not in all_text  # running footer removed
    contact = next(s for s in r.sections if s.section == "CONTACT").text
    assert EMAIL_TOKEN in contact and "@toronto.ca" not in contact


def test_citations_in_decision_history() -> None:
    dh = next(s for s in parse_pdf(PDF.read_bytes()).sections if s.section == "DECISION HISTORY")
    idx = MeetingIndex(["2023.EX1.4", "2019.EX1.1"])
    cites, skipped = extract_citations(dh.text, idx, {"EX", "PH"})
    found = {(c.item_id, c.method) for c in cites}
    assert ("2023.EX1.4", "full") in found
    assert ("2022.EX31.11", "short_inferred") in found  # 'Item EX31.11' after 'On March 30, 2022'
    assert skipped == 0


def test_short_forms_need_known_code_and_unambiguous_year() -> None:
    idx = MeetingIndex(["2019.EX1.1", "2023.EX1.4"])
    text = "See EX1.4 and AB1.2 and version V2.1. At its meeting on December 14, 2022, Council adopted EX1.4."
    cites, skipped = extract_citations(text, idx, {"EX"})
    assert [(c.item_id, c.raw) for c in cites] == [("2023.EX1.4", "EX1.4")]  # only the dated one
    assert skipped == 1  # the undated EX1.4 is ambiguous (2019 or 2023)


def test_build_citegraph(db: Conn) -> None:
    save_item(db, page("2023.PH2.1"), ITEM_URL.format(item_id="2023.PH2.1"), "h", default_mapper())
    save_sections(db, "backgroundfile-234496", parse_pdf(PDF.read_bytes()), "filehash")
    for _ in range(2):  # rebuild is idempotent
        stats = build_citegraph(db)
    edges = db.execute("SELECT from_id, to_item_id, from_type, method FROM cites ORDER BY 2").fetchall()
    assert ("backgroundfile-234496", "2023.EX1.4", "doc", "full") in edges
    assert all(to != "2023.PH2.1" for _, to, _, _ in edges)  # no self-citations
    assert stats["edges"] == len(edges)


def test_fixture_count() -> None:
    """Phase 1 acceptance: parsers are exercised on >= 15 saved real fixtures."""
    log_rows = 17  # rows in report_log/odd_rows.xlsx
    files = [p for p in FIXTURES.rglob("*") if p.is_file() and p.suffix in {".html", ".pdf", ".csv", ".json"}]
    assert log_rows + len(files) >= 15
    assert Path(TMMIS / "2023.EX99.99.html").exists()


def test_bold_table_cells_are_not_headings() -> None:
    """backgroundfile-230137 (2023.EX1.4 staff report) has bold ALL-CAPS budget-table cells
    ('TOTAL', 'OPA/ZBA', 'GRAND TOTAL') at body size; only the template headings split it."""
    r = parse_pdf((FIXTURES / "background" / "backgroundfile-230137.pdf").read_bytes())
    assert [s.section for s in r.sections] == [
        "PREAMBLE", "SUMMARY", "RECOMMENDATIONS", "FINANCIAL IMPACT", "EQUITY IMPACT STATEMENT",
        "DECISION HISTORY", "COMMENTS", "CONTACT", "SIGNATURE", "ATTACHMENTS",
    ]  # fmt: skip
    by_name = {s.section: s for s in r.sections}
    assert "TOTAL" in by_name["COMMENTS"].lines  # page-8 table cell stays inside COMMENTS
    assert "GRAND TOTAL" in by_name["ATTACHMENTS"].text  # page-20 table, after ATTACHMENTS (p. 17)
