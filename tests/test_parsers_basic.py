from __future__ import annotations

from datetime import date, datetime

import pytest

from reportback.ingest.divisions import default_mapper, split_assignees
from reportback.ingest.ids import (
    MeetingIndex,
    normalize_item_id,
    split_item_refs,
    term_for_year_label,
    term_years,
    year_label_for_date,
)
from reportback.ingest.text import EMAIL_TOKEN, PHONE_TOKEN, decode_html, scrub_pii
from reportback.ingest.timing import parse_timing

# ----------------------------------------------------------------------------------- timing

M = date(2024, 5, 22)  # meeting date used for relative rules


@pytest.mark.parametrize(
    ("raw", "due", "rule"),
    [
        (datetime(2024, 2, 23), date(2024, 2, 23), "date"),
        ("Q1 2024", date(2024, 3, 31), "quarter"),
        ("Q3, 2025", date(2025, 9, 30), "quarter"),
        ("Q4 of 2024", date(2024, 12, 31), "quarter"),
        ("Q2 2024/Q4 2025", date(2024, 6, 30), "quarter"),  # first deadline
        ("second quarter of 2024", date(2024, 6, 30), "quarter"),
        ("first quarter", date(2025, 3, 31), "quarter_after_meeting"),
        ("First half of 2025", date(2025, 6, 30), "half"),
        ("In 2025", date(2025, 12, 31), "year"),
        ("End of 2024", date(2024, 12, 31), "year"),
        ("By end of 2019", date(2019, 12, 31), "year"),
        ("July 2024", date(2024, 7, 31), "month"),
        ("January 2024 City Council meeting", date(2024, 1, 31), "month"),
        ("Before 23-Jan-2026", date(2026, 1, 23), "date_text"),
        ("31/03/2024", date(2024, 3, 31), "date_text"),
        ("06/30/2024 06/30/2024", date(2024, 6, 30), "date_text"),
        ("10/11/2023", None, "ambiguous_date"),
        ("*12/09/2024 *2025-07-11", None, "ambiguous_date"),
        ("spring 2025", date(2025, 6, 20), "season"),
        ("In one year", date(2025, 5, 22), "after_meeting"),
        ("not indicated", None, "not_indicated"),
        ("Not Indicated", None, "not_indicated"),
        ("prior to consideration of the 2025 budget", None, "budget_relative"),
        ("Through 2025 Budget Process", None, "budget_relative"),
        ("quarterly", None, "recurring"),
        ("October (annually)", None, "recurring"),
        ("the earliest opportunity", None, "meeting_relative"),
        ("next FWC meeting", None, "meeting_relative"),
        ("If necessary", None, "conditional"),
        ("Once Provincial Regulations are released", None, "conditional"),
        ("202--7-03-31", None, "unparsed"),
        ("In the 2027 Consumption Based Emissions Inventory report", None, "unparsed"),
        (None, None, "missing"),
        ("  ", None, "missing"),
    ],
)
def test_parse_timing(raw: object, due: date | None, rule: str) -> None:
    t = parse_timing(raw, M)
    assert (t.due_date, t.rule) == (due, rule)


# ----------------------------------------------------------------------------------- item IDs


def test_term_year_labels() -> None:
    assert term_years("2022-2026") == (2023, 2024, 2025, 2026)
    assert term_for_year_label(2023) == "2022-2026"
    assert term_for_year_label(2022) == "2018-2022"
    # A new term's first meetings carry the next year's label (2022-11-24 -> 2023.CC1).
    assert year_label_for_date(date(2022, 11, 24)) == 2023
    assert year_label_for_date(date(2023, 12, 13)) == 2023


@pytest.mark.parametrize(
    ("raw", "expected", "method"),
    [
        ("2023.EX3.1", "2023.EX3.1", "exact"),
        (" 2023.EX3.1. ", "2023.EX3.1", "exact"),
        ("2023MM11.16", "2023.MM11.16", "repaired"),
        ("2021-EX28.2", "2021.EX28.2", "repaired"),
        ("2026.AU.13.3", "2026.AU13.3", "repaired"),
        ("2025 NY21.15", "2025.NY21.15", "repaired"),
    ],
)
def test_normalize_item_id(raw: str, expected: str, method: str) -> None:
    ref = normalize_item_id(raw)
    assert ref is not None and (ref.item_id, ref.method) == (expected, method)


def test_short_ids_need_an_unambiguous_year() -> None:
    idx = MeetingIndex(["2025.IE26.1", "2019.EX1.1", "2023.EX1.4"])
    ref = normalize_item_id("IE26.3", idx)
    assert ref is not None and (ref.item_id, ref.method) == ("2025.IE26.3", "inferred_year")
    assert normalize_item_id("EX1.4", idx) is None  # EX1 exists in two terms
    ref = normalize_item_id("EX1.4", idx, allowed_years=term_years("2022-2026"))
    assert ref is not None and ref.item_id == "2023.EX1.4"
    assert normalize_item_id("20263MM42.22.") is None  # stray digit, unconfirmed
    assert normalize_item_id("20263MM42.22.", MeetingIndex(["2026.MM42.1"])) is not None
    assert normalize_item_id("2019.M3.20") is None


def test_split_closed_by() -> None:
    refs, other = split_item_refs("2023.PH2.1\n2023.PH5.1")
    assert [r.item_id for r in refs] == ["2023.PH2.1", "2023.PH5.1"] and other == []
    refs, other = split_item_refs("BN14-Jan 26")
    assert refs == [] and other == ["BN14-Jan 26"]
    refs, other = split_item_refs("(Part 10.d.) (Part 10.b.)")
    assert refs == [] and other == ["(Part 10.d.) (Part 10.b.)"]
    refs, other = split_item_refs(datetime(2023, 2, 23))
    assert refs == [] and other == ["2023-02-23"]
    refs, _ = split_item_refs("2019.EX6.2, ")
    assert [r.item_id for r in refs] == ["2019.EX6.2"]


# ----------------------------------------------------------------------------------- divisions


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("General Manager, Transportation Services", ["Transportation Services"]),
        ("City Manager", ["City Manager's Office"]),
        ("Deputy City Manager, Corporate Services", ["Deputy City Manager, Corporate Services"]),
        ("City Solicitor", ["Legal Services"]),
        ("Medical Officer of Health", ["Toronto Public Health"]),
        ("Executive Director, Customer Experience (311)", ["Customer Experience"]),
        (
            "Deputy City Manager – Community & Social Services",
            ["Deputy City Manager, Community and Social Services"],
        ),
        (
            "General Manager, Parks, Forestry and Recreation",
            ["Parks, Forestry and Recreation", "Parks and Recreation", "Environment, Climate and Forestry"],
        ),
        ("Transportation Services - Toronto Police", ["Transportation Services", "Toronto Police Service"]),
        ("Director, Traffic Management, Transportation Services", ["Transportation Services"]),
        ("Deputy City Manager", []),  # too generic: deliberately unmapped
        ("Appropriate Staff", []),
    ],
)
def test_division_mapping(raw: str, expected: list[str]) -> None:
    assert default_mapper().map(split_assignees(raw)) == expected


def test_split_assignees_keeps_title_commas() -> None:
    assert split_assignees(" General Manager, Parks and Recreation, ") == [
        "General Manager, Parks and Recreation"
    ]
    assert split_assignees("A - B\nC") == ["A", "B", "C"]
    assert split_assignees("Long-Term Care") == ["Long-Term Care"]


# ----------------------------------------------------------------------------------- text


def test_scrub_pii() -> None:
    s = "Contact: Jane Doe, Manager, 416-392-7033, jane.doe@toronto.ca or (416) 338-0889 ext. 21"
    out = scrub_pii(s)
    assert "jane.doe@toronto.ca" not in out and "392-7033" not in out and "338-0889" not in out
    assert out.count(PHONE_TOKEN) == 2 and EMAIL_TOKEN in out
    # Things that look numeric but are not phone numbers survive.
    keep = "Item 2023.EX3.1 on 2024-06-30 cost $1,234,567 in Ward 12 (By-law 286-2023)"
    assert scrub_pii(keep) == keep


def test_decode_html_mixed_encoding() -> None:
    raw = "Résumé ".encode() + b"\xd7 close"
    assert decode_html(raw) == "Résumé × close"
