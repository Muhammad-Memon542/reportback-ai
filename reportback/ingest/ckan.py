"""Minimal typed client for the Toronto Open Data CKAN API (action API v3)."""

from __future__ import annotations

import io
import re
import warnings
from typing import Any

from pydantic import BaseModel, ConfigDict

from reportback.ingest.http import PoliteClient


class CkanError(RuntimeError):
    pass


class Resource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    format: str = ""
    url: str
    datastore_active: bool = False
    last_modified: str | None = None
    size: int | None = None


class Package(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    title: str
    license_title: str | None = None
    owner_division: str | None = None
    refresh_rate: str | None = None
    last_refreshed: str | None = None
    resources: list[Resource] = []


class DatastoreField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str


_WS = re.compile(r"\s+")


def normalize_header(value: object) -> str:
    """'Closed \\nby' -> 'Closed by'. The log's XLSX headers contain embedded newlines."""
    return _WS.sub(" ", str(value)).strip()


def xlsx_headers(content: bytes, sheet: str | None = None) -> tuple[str, list[str], int]:
    """Return (sheet title, normalized header row, count of non-empty data rows).

    Uses the first worksheet unless `sheet` is given. Trailing empty header cells are dropped.
    """
    import openpyxl

    with warnings.catch_warnings():
        # The Clerk's workbooks trigger harmless openpyxl warnings (header/footer, data
        # validation). read_only mode parses lazily, so keep the filter around iteration too.
        warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            ws = wb[sheet] if sheet else wb.worksheets[0]
            rows = ws.iter_rows(values_only=True)
            header = next(rows, ())
            cols = [normalize_header(c) for c in header if c is not None and str(c).strip()]
            n = sum(1 for r in rows if any(c is not None and str(c).strip() for c in r))
            return ws.title, cols, n
        finally:
            wb.close()


class CkanClient:
    def __init__(self, http: PoliteClient, base_url: str) -> None:
        self.http = http
        self.api = base_url.rstrip("/") + "/api/3/action"

    def _action(self, action: str, **params: Any) -> Any:
        body = self.http.get_json(f"{self.api}/{action}", params=params)
        if not isinstance(body, dict) or not body.get("success"):
            err = body.get("error") if isinstance(body, dict) else body
            raise CkanError(f"CKAN {action} failed: {err}")
        return body["result"]

    def package_search(self, q: str, rows: int = 10) -> list[Package]:
        result = self._action("package_search", q=q, rows=rows)
        return [Package.model_validate(p) for p in result["results"]]

    def package_show(self, package_id: str) -> Package:
        return Package.model_validate(self._action("package_show", id=package_id))

    def datastore_fields(self, resource_id: str) -> tuple[list[DatastoreField], int]:
        """Column names/types and total row count, without downloading any rows."""
        result = self._action("datastore_search", resource_id=resource_id, limit=0)
        fields = [DatastoreField.model_validate(f) for f in result["fields"]]
        return [f for f in fields if f.id != "_id"], int(result.get("total", 0))
