"""Background files (staff reports, letters): download, extract text, split by headings.

City staff reports follow a template: a preamble (REPORT FOR ACTION/INFORMATION, title,
Date/To/From/Wards), then bold ALL-CAPS headings such as SUMMARY, RECOMMENDATIONS,
FINANCIAL IMPACT, DECISION HISTORY, COMMENTS, CONTACT. Each page repeats a running header
and "Page N of M", which we drop. Emails and phone numbers are scrubbed before storage.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
import pymupdf

from reportback.db import Conn
from reportback.ingest.http import PoliteClient
from reportback.ingest.text import norm_ws, scrub_pii
from reportback.ingest.tmmis import parse_first_date

KNOWN_HEADINGS = frozenset(
    {
        "SUMMARY", "RECOMMENDATIONS", "FINANCIAL IMPACT", "DECISION HISTORY", "COMMENTS",
        "EQUITY IMPACT STATEMENT", "EQUITY IMPACT", "ISSUE BACKGROUND", "BACKGROUND", "CONTACT",
        "CONTACTS", "SIGNATURE", "ATTACHMENTS", "ATTACHMENT", "DECLARATION OF INTEREST",
        "REASONS FOR CONFIDENTIAL INFORMATION", "CONCLUSION", "NEXT STEPS", "PURPOSE",
        "ANALYSIS", "DISCUSSION", "RECOMMENDATION", "ACCESSIBILITY IMPACT",
    }
)  # fmt: skip
REPORT_TYPE = re.compile(r"^REPORT FOR (ACTION|INFORMATION)\b.*$")
PAGE_LINE = re.compile(r"^page\s+\d+\s+of\s+\d+$", re.IGNORECASE)
MAX_PDF_BYTES = 40_000_000


@dataclass
class Section:
    section: str
    page_start: int
    page_end: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return scrub_pii("\n".join(self.lines).strip())


@dataclass
class ParsedPdf:
    pages: int
    report_type: str | None
    header: dict[str, str]
    sections: list[Section]

    @property
    def report_date(self) -> date | None:
        return parse_first_date(self.header.get("date", ""))


@dataclass(frozen=True)
class _Line:
    page: int
    text: str
    bold: bool


def _is_bold(span: dict[str, Any]) -> bool:
    return "bold" in str(span.get("font", "")).lower() or bool(int(span.get("flags", 0)) & 16)


def _extract_lines(doc: pymupdf.Document) -> list[_Line]:
    out: list[_Line] = []
    for pno in range(doc.page_count):
        page = doc[pno]
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if str(s.get("text", "")).strip()]
                if not spans:
                    continue
                text = norm_ws("".join(str(s["text"]) for s in line["spans"]))
                if text:
                    out.append(_Line(pno + 1, text, all(_is_bold(s) for s in spans)))
    return out


def _running_lines(lines: list[_Line], pages: int) -> set[str]:
    """Lines repeated on most pages (running headers/footers), compared with digits masked."""
    if pages < 3:
        return set()
    per_page: dict[str, set[int]] = {}
    for ln in lines:
        key = re.sub(r"\d+", "#", ln.text)
        per_page.setdefault(key, set()).add(ln.page)
    return {k for k, pg in per_page.items() if len(pg) >= max(3, int(pages * 0.6))}


def _is_heading(ln: _Line) -> bool:
    t = ln.text.strip().rstrip(":")
    if t in KNOWN_HEADINGS:
        return True
    letters = re.sub(r"[^A-Za-z]", "", t)
    return bool(
        ln.bold
        and 3 <= len(t) <= 60
        and letters
        and letters.isupper()
        and not REPORT_TYPE.match(t)
        and not re.search(r"\d{4}", t)
    )


def parse_pdf(content: bytes) -> ParsedPdf:
    doc = pymupdf.open(stream=content, filetype="pdf")
    try:
        lines = _extract_lines(doc)
        pages = doc.page_count
    finally:
        doc.close()
    running = _running_lines(lines, pages)
    report_type = None
    header: dict[str, str] = {}
    sections: list[Section] = [Section("PREAMBLE", 1, 1)]
    for ln in lines:
        if PAGE_LINE.match(ln.text) or re.sub(r"\d+", "#", ln.text) in running:
            continue
        if report_type is None and REPORT_TYPE.match(ln.text):
            report_type = ln.text
            continue
        if len(sections) == 1:
            m = re.match(r"^(Date|To|From|Wards?)\s*:\s*(.+)$", ln.text)
            if m:
                key = "wards" if m.group(1).lower().startswith("ward") else m.group(1).lower()
                header.setdefault(key, m.group(2))
        if _is_heading(ln):
            sections.append(Section(ln.text.strip().rstrip(":").upper(), ln.page, ln.page))
            continue
        cur = sections[-1]
        cur.lines.append(ln.text)
        cur.page_end = max(cur.page_end, ln.page)
    out = [s for s in sections if s.lines]
    if len(out) == 1 and out[0].section == "PREAMBLE":
        out[0].section = "BODY"  # no template headings (e.g. a councillor's letter)
    return ParsedPdf(pages=pages, report_type=report_type, header=header, sections=out)


def save_sections(conn: Conn, doc_id: str, parsed: ParsedPdf, file_hash: str) -> int:
    rows = [
        (doc_id, i, s.section, s.page_start, s.page_end, s.text)
        for i, s in enumerate(parsed.sections)
        if s.text
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM doc_section WHERE doc_id = %s", (doc_id,))
        cur.executemany(
            "INSERT INTO doc_section (doc_id, seq, section, page_start, page_end, text) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            rows,
        )
        cur.execute(
            """UPDATE doc SET fetch_status = 'ok', file_hash = %s, pages = %s,
                   report_date = coalesce(report_date, %s)
               WHERE doc_id = %s""",
            (file_hash, parsed.pages, parsed.report_date, doc_id),
        )
    return len(rows)


def _mark(conn: Conn, doc_id: str, status: str) -> None:
    with conn.transaction():
        conn.execute("UPDATE doc SET fetch_status = %s WHERE doc_id = %s", (status, doc_id))


def ingest_background(
    conn: Conn,
    http: PoliteClient,
    *,
    limit: int | None = None,
    kinds: tuple[str, ...] = ("report", "letter", "briefing_note"),
    retry_errors: bool = False,
) -> dict[str, Any]:
    statuses = "fetch_status IS NULL" + (" OR fetch_status = 'error'" if retry_errors else "")
    todo = conn.execute(
        f"SELECT doc_id, url FROM doc WHERE kind = ANY(%s) AND ({statuses}) ORDER BY doc_id",  # noqa: S608 (fixed strings only)
        (list(kinds),),
    ).fetchall()
    if limit is not None:
        todo = todo[:limit]
    stats: Counter[str] = Counter()
    errors: list[str] = []
    for doc_id, url in todo:
        try:
            fetched = http.fetch_cached(url)
            content = fetched.content
            if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF"):
                _mark(conn, doc_id, "skipped")
                stats["skipped"] += 1
                continue
            parsed = parse_pdf(content)
            stats["sections"] += save_sections(conn, doc_id, parsed, hashlib.sha256(content).hexdigest())
            stats["ok"] += 1
            stats["bytes"] += len(content)
        except httpx.HTTPStatusError as e:
            _mark(conn, doc_id, "not_found" if e.response.status_code == 404 else "error")
            stats["not_found" if e.response.status_code == 404 else "error"] += 1
        except Exception as e:  # a bad PDF must not stop the run
            _mark(conn, doc_id, "error")
            stats["error"] += 1
            errors.append(f"{doc_id}: {type(e).__name__}: {e}"[:200])
    return {"todo": len(todo), **stats, "errors": errors[:20]}
