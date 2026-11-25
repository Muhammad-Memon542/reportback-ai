"""TMMIS agenda item pages: parser + polite one-hop crawler.

Page structure (inspected 2026-11-23, see DATASHEET): an accordion with one card per
consideration (most recent first: City Council, then the originating committee). Each card has
h3.heading ("PH2.1 - Title"), a .d-info list (Decision Type / Status / Ward(s)) and h4-titled
sections. h4s are not consistently siblings (e.g. "City Council Decision" sits inside .d-info),
so a card is segmented in document order: every h4 becomes a marker and the card text is split
on the markers. Links become inline markers so Background Information keeps description->URL.

Privacy: "Communications" (letters from the public) and "Speakers" (deputants) name private
individuals. We store only their counts, never their content.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from reportback.db import Conn
from reportback.ingest.divisions import DivisionMapper, default_mapper
from reportback.ingest.http import PoliteClient
from reportback.ingest.ids import FULL_ID, term_for_year_label, term_years
from reportback.ingest.text import decode_html, norm_ws, scrub_pii
from reportback.ingest.timing import MONTHS

ITEM_URL = "https://secure.toronto.ca/council/agenda-item.do?item={item_id}"
_H4 = "\x00H4\x00"
_A = "\x00A\x00"
_END = "\x00"
_DATE_IN_TEXT = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:(?:,\s*(?:and\s+)?|\s+and\s+|\s*-\s*)\d{1,2})*,?\s+(\d{4})"
)  # 'November 30, 2022', 'December 14 and 15, 2022', 'March 29, 30 and 31, 2023', 'July 14, 15, and 16, 2021'
_DOC_ID = re.compile(r"/(backgroundfile-\d+)\.pdf$", re.IGNORECASE)
_COMM = re.compile(r"/communicationfile-\d+\.pdf$", re.IGNORECASE)
DECISION_SECTIONS = ("decision", "committee recommendations", "decision advice and other information")


def parse_first_date(text: str) -> date | None:
    """'March 29, 30 and 31, 2023' -> 2023-03-29."""
    m = _DATE_IN_TEXT.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    except ValueError:
        return None


@dataclass
class BackgroundDoc:
    doc_id: str
    url: str
    description: str
    kind: str
    report_date: date | None
    from_officials: list[str]
    title: str | None


@dataclass
class Consideration:
    seq: int
    body: str | None
    meeting_date_text: str | None
    meeting_date: date | None
    status: str | None
    decision_type: str | None
    wards_raw: str | None
    heading: str | None
    sections: dict[str, str] = field(default_factory=dict)  # section title -> text (no links)

    @property
    def decision(self) -> tuple[str | None, str | None]:
        parts = [(t, s) for t, s in self.sections.items() if _is_decision(t) and s]
        if not parts:
            return None, None
        return parts[0][0], "\n\n".join(f"{t}:\n{s}" if len(parts) > 1 else s for t, s in parts)


@dataclass
class ItemPage:
    item_id: str
    found: bool
    title: str | None = None
    considerations: list[Consideration] = field(default_factory=list)
    docs: list[BackgroundDoc] = field(default_factory=list)
    communications_count: int = 0
    speakers_count: int = 0


def _is_decision(title: str) -> bool:
    t = title.lower()
    return t.endswith("decision") or t in DECISION_SECTIONS


def classify_doc(description: str) -> str:
    """Kind of a background file, from its listing text on the item page."""
    d = description.lower()
    d = re.sub(r"^\(\w+ \d{1,2}, \d{4}\)\s*", "", d)
    if re.match(
        r"((revised|updated|public|confidential)\s+)*"
        r"(attachment|appendix|appendices|schedule|map|figure|table|exhibit|annex)\b",
        d,
    ):
        return "attachment"
    if "presentation" in d:
        return "presentation"
    if re.match(r"(revised\s+)?member motion\b", d):
        return "motion"
    if re.match(r"mayoral (direction|decision)", d):
        return "mayoral_direction"
    if re.match(r"(revised\s+)?briefing note\b", d):
        return "briefing_note"
    if re.search(r"budget notes|capital plan detail|operating budget|capital budget", d):
        return "budget"
    if re.match(r"(fiscal|financial) impact (statement|summary)", d):
        return "financial_impact"
    if (
        re.match(r"((revised|supplementary|updated|joint)\s+)*report\b", d)
        or "report from" in d
        or "supplementary report" in d
        or re.search(r"\breport:", d)
    ):
        return "report"
    if re.match(r"answer from\b", d):
        return "answer"  # staff answer to an administrative inquiry
    if re.match(r"(joint\s+)?letter\b", d) or "letter from" in d or re.match(r"transmittal from\b", d):
        return "letter"
    if re.match(r"(public\s+)?notice\b", d):
        return "notice"
    return "other"


def parse_doc_description(description: str) -> tuple[date | None, list[str], str | None]:
    """'(Feb 9, 2023) Report from the Chief Planner ..., City Planning on Bill 109 ...' ->
    (date, ['Chief Planner ..., City Planning'], 'Bill 109 ...')."""
    d = norm_ws(description)
    rdate = None
    m = re.match(r"^\((.*?)\)\s*", d)
    if m:
        rdate = parse_first_date(m.group(1))
        d = d[m.end() :]
    officials: list[str] = []
    title: str | None = None
    fm = re.search(r"\bfrom\s+(.*)", d, flags=re.IGNORECASE)
    if fm:
        rest = fm.group(1)
        # Reports: 'from <officials> on <title>'; councillor letters: 'from <name> - <title>'.
        sep = re.search(r"\s+on\s+", rest) or re.search(r"\s+-\s+", rest)
        who = rest[: sep.start()] if sep else rest
        title = rest[sep.end() :].strip() or None if sep else None
        for part in re.split(r",?\s+and\s+the\s+|;\s*", who):
            part = re.sub(r"^the\s+", "", part.strip(), flags=re.IGNORECASE).strip(" ,")
            if part:
                officials.append(part)
    elif d:
        title = d
    return rdate, officials, title or None


def _segment(card_body: LexborNode) -> list[tuple[str, str]]:
    """Split a card body into (h4 title, raw text with link markers) in document order."""
    for a in card_body.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if href and not href.startswith("#"):
            a.replace_with(f" {_A}{href}{_END} ")
    for h in card_body.css("h4"):
        h.replace_with(f"\n{_H4}{norm_ws(h.text())}{_END}\n")
    text = card_body.text(separator="\n")
    out: list[tuple[str, str]] = []
    for chunk in text.split(_H4)[1:]:
        title, _, rest = chunk.partition(_END)
        out.append((title.strip(), rest))
    return out


def _strip_links(s: str) -> str:
    return norm_ws(re.sub(re.escape(_A) + r".*?" + re.escape(_END), " ", s))


def _clean_para(s: str) -> str:
    lines = [norm_ws(x) for x in _strip_links_keep_lines(s).split("\n")]
    return scrub_pii("\n".join(x for x in lines if x))


def _strip_links_keep_lines(s: str) -> str:
    return re.sub(re.escape(_A) + r".*?" + re.escape(_END), " ", s)


def _docs_from_section(raw: str) -> list[BackgroundDoc]:
    docs: list[BackgroundDoc] = []
    pos = 0
    for m in re.finditer(re.escape(_A) + r"(.*?)" + re.escape(_END), raw):
        desc = norm_ws(raw[pos : m.start()])
        pos = m.end()
        url = m.group(1).strip()
        dm = _DOC_ID.search(url)
        if not dm:
            continue
        desc = re.sub(r"https?://\S+", "", desc).strip()
        rdate, officials, title = parse_doc_description(desc)
        docs.append(BackgroundDoc(dm.group(1), url, desc, classify_doc(desc), rdate, officials, title))
    return docs


def parse_item_page(html: str, item_id: str) -> ItemPage:
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    if tree.css_first("#agenda-item-accordion") is None or (
        title_node is not None and title_node.text(strip=True).lower() == "error"
    ):
        return ItemPage(item_id=item_id, found=False)
    page = ItemPage(item_id=item_id, found=True)
    comm_urls: set[str] = set()
    speakers: set[str] = set()
    seen_docs: set[str] = set()
    for seq, card in enumerate(tree.css("#agenda-item-accordion > div.card")):
        header_node = card.css_first(".card-header")
        header = norm_ws(header_node.text()) if header_node is not None else ""
        body_name, _, when = header.partition(" consideration on ")
        cbody = card.css_first(".card-body")
        if cbody is None:
            continue
        heading_node = cbody.css_first("h3.heading")
        heading = norm_ws(heading_node.text()) if heading_node else None
        info: dict[str, str] = {}
        for dl in cbody.css(".d-info dl"):
            dt, dd = dl.css_first("dt"), dl.css_first("dd")
            if dt is not None and dd is not None:
                info[norm_ws(dt.text()).rstrip(":").lower()] = norm_ws(dd.text())
        cons = Consideration(
            seq=seq,
            body=body_name.strip() or None,
            meeting_date_text=when.strip() or None,
            meeting_date=parse_first_date(when),
            status=info.get("status"),
            decision_type=info.get("decision type"),
            wards_raw=info.get("wards") or info.get("ward"),
            heading=heading,
        )
        for title, raw in _segment(cbody):
            low = title.lower()
            if low.startswith("vote"):
                continue  # per-motion vote tables: votes come from the CKAN dataset
            if low.startswith("communications"):
                comm_urls.update(
                    u for u in re.findall(re.escape(_A) + r"(.*?)" + re.escape(_END), raw) if _COMM.search(u)
                )
                continue
            if low.startswith("speakers"):
                speakers.update(
                    x for x in (norm_ws(line) for line in _strip_links_keep_lines(raw).split("\n")) if x
                )
                continue
            if low.startswith("background information"):
                for d in _docs_from_section(raw):
                    if d.doc_id not in seen_docs:
                        seen_docs.add(d.doc_id)
                        page.docs.append(d)
                continue
            if low.startswith(("motions", "declared interests")):
                continue
            cons.sections[title] = _clean_para(raw)
        page.considerations.append(cons)
    if page.considerations and page.considerations[0].heading:
        h = page.considerations[0].heading
        page.title = h.split(" - ", 1)[1].strip() if " - " in h else h
    page.communications_count = len(comm_urls)
    page.speakers_count = len(speakers)
    return page


def parse_wards(raw: str | None) -> list[str]:
    if not raw:
        return []
    if raw.strip().lower().startswith("all"):
        return ["All"]
    return sorted({str(int(n)) for n in re.findall(r"(?:^|,\s*)(\d{1,2})\s*-", raw)}, key=int)


# --------------------------------------------------------------------------- persistence + crawl


def _item_row(page: ItemPage, url: str, content_hash: str) -> tuple[Any, ...]:
    m = FULL_ID.fullmatch(page.item_id)
    code = m.group(2) if m else None
    term = term_for_year_label(int(m.group(1))) if m else None
    cons = page.considerations
    origin_c = cons[-1] if cons else None
    council = next((c for c in cons if (c.body or "").lower() == "city council"), None)
    latest = cons[0] if cons else None
    decision_c = council if council and council.decision[1] else latest
    wards: list[str] = []
    for c in cons:
        for w in parse_wards(c.wards_raw):
            if w not in wards:
                wards.append(w)

    def sec(c: Consideration | None, name: str) -> str | None:
        if c is None:
            return None
        for t, v in c.sections.items():
            if t.lower() == name:
                return v or None
        return None

    return (
        page.item_id, term, code,
        origin_c.body if origin_c else None, origin_c.meeting_date if origin_c else None,
        council.meeting_date if council else None, page.title, wards,
        latest.decision_type if latest else None, latest.status if latest else None,
        sec(origin_c, "origin"), sec(origin_c, "summary"),
        decision_c.decision[1] if decision_c else None,
        page.communications_count, page.speakers_count, url,
        "ok" if page.found else "not_found", datetime.now(UTC), content_hash,
    )  # fmt: skip


def save_item(conn: Conn, page: ItemPage, url: str, content_hash: str, mapper: DivisionMapper) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """INSERT INTO item (item_id, term, committee_code, committee, meeting_date, council_date,
                   title, wards, decision_type, status, origin, summary, decision_text,
                   communications_count, speakers_count, url, fetch_status, fetched_at, content_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (item_id) DO UPDATE SET term = EXCLUDED.term,
                   committee_code = EXCLUDED.committee_code, committee = EXCLUDED.committee,
                   meeting_date = EXCLUDED.meeting_date, council_date = EXCLUDED.council_date,
                   title = EXCLUDED.title, wards = EXCLUDED.wards,
                   decision_type = EXCLUDED.decision_type, status = EXCLUDED.status,
                   origin = EXCLUDED.origin, summary = EXCLUDED.summary,
                   decision_text = EXCLUDED.decision_text,
                   communications_count = EXCLUDED.communications_count,
                   speakers_count = EXCLUDED.speakers_count, url = EXCLUDED.url,
                   fetch_status = EXCLUDED.fetch_status, fetched_at = EXCLUDED.fetched_at,
                   content_hash = EXCLUDED.content_hash""",
            _item_row(page, url, content_hash),
        )
        cur.execute("DELETE FROM item_consideration WHERE item_id = %s", (page.item_id,))
        cur.executemany(
            """INSERT INTO item_consideration (item_id, seq, body, meeting_date, meeting_date_text,
                   status, decision_type, decision_heading, decision_text)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [
                (page.item_id, c.seq, c.body, c.meeting_date, c.meeting_date_text, c.status,
                 c.decision_type, *c.decision)
                for c in page.considerations
            ],
        )  # fmt: skip
        for d in page.docs:
            cur.execute(
                """INSERT INTO doc (doc_id, item_id, kind, description, title, report_date,
                       from_officials, divisions, url)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (doc_id) DO UPDATE SET kind = EXCLUDED.kind,
                       description = EXCLUDED.description, title = EXCLUDED.title,
                       report_date = coalesce(EXCLUDED.report_date, doc.report_date),
                       from_officials = EXCLUDED.from_officials,
                       divisions = EXCLUDED.divisions, url = EXCLUDED.url""",
                (d.doc_id, page.item_id, d.kind, d.description, d.title, d.report_date,
                 d.from_officials, mapper.map(d.from_officials), d.url),
            )  # fmt: skip


def seed_item_ids(conn: Conn, term: str) -> set[str]:
    """Item IDs referenced by the term's directives (incl. closing items), votes and cites."""
    years = term_years(term)
    rows = conn.execute(
        """SELECT item_id FROM directive WHERE term = %(t)s AND item_id IS NOT NULL
           UNION SELECT unnest(closed_by_item) FROM directive WHERE term = %(t)s
           UNION SELECT item_id FROM vote WHERE term = %(t)s AND item_id IS NOT NULL
           UNION SELECT to_item_id FROM cites""",
        {"t": term},
    ).fetchall()
    out = set()
    for (i,) in rows:
        m = FULL_ID.fullmatch(i or "")
        if m and int(m.group(1)) in years:
            out.add(i)
    return out


def crawl_items(
    conn: Conn,
    http: PoliteClient,
    *,
    term: str,
    limit: int | None = None,
    reparse: bool = False,
    mapper: DivisionMapper | None = None,
) -> dict[str, Any]:
    """Fetch the current frontier (one hop per run), or with `reparse` re-parse every
    already-fetched item from the raw cache (no new pages, e.g. after a parser change)."""
    mapper = mapper or default_mapper()
    seeds = seed_item_ids(conn, term)
    done = {r[0] for r in conn.execute("SELECT item_id FROM item WHERE fetch_status IN ('ok', 'not_found')")}
    frontier = sorted(done if reparse else seeds - done)
    if limit is not None:
        frontier = frontier[:limit]
    stats: dict[str, Any] = {
        "seeds": len(seeds),
        "frontier": len(frontier),
        "ok": 0,
        "not_found": 0,
        "errors": [],
    }
    for item_id in frontier:
        url = ITEM_URL.format(item_id=item_id)
        try:
            fetched = http.fetch_cached(url)
            page = parse_item_page(decode_html(fetched.content), item_id)
            save_item(conn, page, url, hashlib.sha256(fetched.content).hexdigest(), mapper)
            stats["ok" if page.found else "not_found"] += 1
        except Exception as e:  # keep crawling; the item is retried next run
            stats["errors"].append(f"{item_id}: {type(e).__name__}: {e}"[:200])
    stats["error_count"] = len(stats["errors"])
    stats["errors"] = stats["errors"][:20]
    return stats
