from __future__ import annotations

import io
import json
from datetime import date

import httpx
import openpyxl
import pytest

from reportback.config import Settings
from reportback.ingest.ckan import CkanClient, CkanError, normalize_header, xlsx_headers
from reportback.ingest.http import PoliteClient
from tests.conftest import FIXTURES
from tests.test_http import NoSleepLimiter

BASE = "https://ckan0.cf.opendata.inter.prod-toronto.ca"


def ckan(settings: Settings, handler: httpx.MockTransport) -> CkanClient:
    http = PoliteClient(settings, transport=handler, rate_limiter=NoSleepLimiter())
    return CkanClient(http, BASE)


def test_package_search_parses_real_response(settings: Settings) -> None:
    body = json.loads((FIXTURES / "ckan" / "package_search_report_request_log.json").read_text())

    def handler(r: httpx.Request) -> httpx.Response:
        assert r.url.path == "/api/3/action/package_search"
        assert r.url.params["q"] == '"Report Request Log"'
        return httpx.Response(200, json=body)

    [pkg] = ckan(settings, httpx.MockTransport(handler)).package_search('"Report Request Log"')
    assert pkg.name == "report-request-log-city-council-and-its-committees"
    assert pkg.license_title == "open-government-licence-toronto"
    xlsx = [r for r in pkg.resources if r.format == "XLSX"]
    assert [r.name for r in xlsx] == ["Report Request Log 2022-2026", "Report Request Log 2018-2022"]
    assert all(not r.datastore_active for r in xlsx)


def test_unsuccessful_action_raises(settings: Settings) -> None:
    t = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"success": False, "error": {"message": "Not found"}})
    )
    with pytest.raises(CkanError, match="Not found"):
        ckan(settings, t).package_show("nope")


def test_datastore_fields_drops_internal_id(settings: Settings) -> None:
    body = {
        "success": True,
        "result": {
            "total": 46815,
            "fields": [
                {"id": "_id", "type": "int"},
                {"id": "Agenda Item #", "type": "text"},
                {"id": "Vote", "type": "text"},
            ],
            "records": [],
        },
    }

    def handler(r: httpx.Request) -> httpx.Response:
        assert r.url.params["limit"] == "0"  # metadata only, no rows
        return httpx.Response(200, json=body)

    fields, total = ckan(settings, httpx.MockTransport(handler)).datastore_fields("res-1")
    assert [f.id for f in fields] == ["Agenda Item #", "Vote"]
    assert total == 46815


def test_normalize_header_collapses_embedded_newlines() -> None:
    assert normalize_header("Closed \nby") == "Closed by"
    assert normalize_header("Req.\nBody") == "Req. Body"
    assert normalize_header("  Rpt \nto: ") == "Rpt to:"


def test_xlsx_headers_matches_clerk_layout() -> None:
    # Mirrors the real workbook: newline headers, trailing empty columns, blank trailing rows.
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Main Data"
    ws.append(["Status", "Req.\nBody", "Meeting \nDate", "Item Number", "Closed \nby", None, None])
    ws.append(["Closed", "CC ", date(2022, 11, 24), "2023.CC1.2", "2023.PH2.10", None, None])
    ws.append(["NCF", "TE", date(2022, 11, 30), "2023.TE1.30", None, None, None])
    ws.append([None, None, None, None, None, None, None])
    wb.create_sheet("Glossary")
    buf = io.BytesIO()
    wb.save(buf)

    sheet, cols, n = xlsx_headers(buf.getvalue())
    assert sheet == "Main Data"
    assert cols == ["Status", "Req. Body", "Meeting Date", "Item Number", "Closed by"]
    assert n == 2
