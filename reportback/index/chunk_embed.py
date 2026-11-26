"""Build retrieval chunks from the ingested tables, then embed them (cached by content hash).

Chunk kinds (`doc_type`):
  directive     one per directive: agenda title (heading) + directive text
  decision      the item's final decision text, split into passages
  item_summary  item origin + summary, split into passages
  staff_report  sections of reports, briefing notes and answers, split into passages
  letter        sections of letters
Every chunk's `text` is an exact substring of its source text, so later stages can verify
quotes against it. Long sections are split (DECISIONS D22): ~1,600-character passages fit the
embedding model well and the 512-token limit of the Phase 4 cross-encoder.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from reportback.config import Settings
from reportback.db import Conn
from reportback.ingest.ids import FULL_ID, term_for_year_label
from reportback.llm import LLMClient

TARGET_CHARS = 1600
MAX_CHARS = 2200
OVERLAP_CHARS = 200
SKIP_SECTIONS = frozenset({"SIGNATURE", "CONTACT", "CONTACTS"})  # names/phones only: no retrieval value
DOC_KIND_TO_TYPE = {
    "report": "staff_report",
    "briefing_note": "staff_report",
    "answer": "staff_report",
    "letter": "letter",
}
ITEM_URL = "https://secure.toronto.ca/council/agenda-item.do?item={item_id}"

# The Clerk's committee code index (Report Request Log glossary) + codes seen in item IDs.
COMMITTEES = {
    "CC": "City Council", "MM": "City Council (Member Motions)", "EX": "Executive Committee",
    "AU": "Audit Committee", "HL": "Board of Health", "BU": "Budget Committee",
    "CA": "Civic Appointments Committee", "EC": "Economic and Community Development Committee",
    "GG": "General Government Committee", "IE": "Infrastructure and Environment Committee",
    "PH": "Planning and Housing Committee", "EY": "Etobicoke York Community Council",
    "NY": "North York Community Council", "SC": "Scarborough Community Council",
    "TE": "Toronto and East York Community Council", "ST": "Striking Committee",
    "DI": "Toronto Accessibility Advisory Committee", "PB": "Toronto Preservation Board",
    "SE": "Service Excellence Committee", "CR": "Confronting Anti-Black Racism Advisory Committee",
    "RA": "CreateTO", "PA": "Toronto Parking Authority", "CT": "Board of Directors of TO Live",
    "TM": "Subcommittee on Metrolinx's Ontario Line Construction", "TTC": "Toronto Transit Commission",
}  # fmt: skip


# --------------------------------------------------------------------------- splitting


def split_passages(
    text: str, target: int = TARGET_CHARS, max_len: int = MAX_CHARS, overlap: int = OVERLAP_CHARS
) -> list[tuple[int, int]]:
    """(start, end) offsets of passages covering `text`; each text[start:end] is a substring.

    Boundaries prefer line ends, then sentence ends, then whitespace; consecutive passages
    overlap by up to `overlap` characters (starting at a boundary)."""
    n = len(text)
    if n <= max_len:
        return [(0, n)] if text.strip() else []
    line_b = [m.end() for m in re.finditer(r"\n", text)]
    sent_b = [m.end() for m in re.finditer(r"[.;:!?][)\"'”’]?\s", text)]
    space_b = [m.end() for m in re.finditer(r"\s", text)]

    def last_boundary(cands: list[int], lo: int, hi: int) -> int | None:
        best = None
        for b in cands:
            if b > hi:
                break
            if b >= lo:
                best = b
        return best

    def first_boundary(cands: list[int], lo: int, hi: int) -> int | None:
        for b in cands:
            if b >= lo:
                return b if b <= hi else None
        return None

    out: list[tuple[int, int]] = []
    start = 0
    while start < n:
        if n - start <= max_len:
            end = n
        else:
            lo, hi = start + target // 2, start + max_len
            end = (
                last_boundary(line_b, lo, min(hi, start + target + 300))
                or last_boundary(sent_b, lo, hi)
                or last_boundary(space_b, lo, hi)
                or hi
            )
        out.append((start, end))
        if end >= n:
            break
        nxt = (
            first_boundary(line_b, end - overlap, end - 1)
            or first_boundary(sent_b, end - overlap, end - 1)
            or end
        )
        start = max(nxt, start + 1)
    # trim whitespace at the edges (still substrings)
    trimmed = []
    for s, e in out:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            trimmed.append((s, e))
    return trimmed


# --------------------------------------------------------------------------- chunk rows


@dataclass(frozen=True)
class ChunkRow:
    chunk_key: str
    doc_type: str
    source_id: str
    item_id: str | None
    meeting_date: date | None
    committee_code: str | None
    committee: str | None
    divisions: list[str]
    wards: list[str]
    term: str | None
    status: str | None
    section: str | None
    page: int | None
    page_end: int | None
    heading: str | None
    text: str
    url: str

    def embed_input(self) -> str:
        return f"{self.heading}\n\n{self.text}" if self.heading else self.text


def embed_hash(model: str, dim: int, text: str) -> str:
    return hashlib.sha256(f"{model}|{dim}|{text}".encode()).hexdigest()


def _code_term(item_id: str | None) -> tuple[str | None, str | None]:
    m = FULL_ID.fullmatch(item_id or "")
    if not m:
        return None, None
    return m.group(2), term_for_year_label(int(m.group(1)))


def build_chunks(conn: Conn) -> Iterator[ChunkRow]:
    items: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        """SELECT i.item_id, i.title, i.committee, i.meeting_date, i.council_date, i.wards, i.origin,
                  i.summary, i.decision_text, i.url,
                  coalesce((SELECT array_agg(DISTINCT x) FROM doc d, unnest(d.divisions) x
                             WHERE d.item_id = i.item_id), '{}')
           FROM item i WHERE i.fetch_status = 'ok'"""
    ):
        (iid, title, committee, mdate, cdate, wards, origin, summary, decision, url, divs) = r
        items[iid] = dict(title=title, committee=committee, meeting_date=mdate, council_date=cdate,
                          wards=wards or [], origin=origin, summary=summary, decision=decision, url=url,
                          divisions=sorted(divs or []))  # fmt: skip

    # Directives: one chunk each.
    for did, iid, raw_no, mdate, title, text, divs, status, body in conn.execute(
        """SELECT directive_id, item_id, item_number_raw, meeting_date, agenda_title, directive_text,
                  divisions, log_status_norm, requesting_body FROM directive"""
    ):
        it = items.get(iid or "")
        code, term = _code_term(iid)
        yield ChunkRow(
            chunk_key=f"directive:{did}", doc_type="directive", source_id=did, item_id=iid,
            meeting_date=mdate, committee_code=code,
            committee=(it or {}).get("committee") or COMMITTEES.get(code or "") or COMMITTEES.get(body or ""),
            divisions=list(divs or []), wards=list((it or {}).get("wards", [])), term=term, status=status,
            section=None, page=None, page_end=None, heading=title, text=text,
            url=ITEM_URL.format(item_id=iid or raw_no),
        )  # fmt: skip

    # Item decisions and summaries.
    for iid, it in items.items():
        code, term = _code_term(iid)
        for doc_type, text, when in (
            ("decision", it["decision"], it["council_date"] or it["meeting_date"]),
            ("item_summary", "\n".join(x for x in (it["origin"], it["summary"]) if x), it["meeting_date"]),
        ):
            if not text:
                continue
            for i, (s, e) in enumerate(split_passages(text)):
                yield ChunkRow(
                    chunk_key=f"{doc_type}:{iid}:{i}", doc_type=doc_type, source_id=iid, item_id=iid,
                    meeting_date=when, committee_code=code, committee=it["committee"],
                    divisions=it["divisions"], wards=it["wards"], term=term, status=None, section=None,
                    page=None, page_end=None, heading=it["title"], text=text[s:e], url=it["url"],
                )  # fmt: skip

    # Staff reports, briefing notes, answers, letters: one or more passages per section.
    for doc_id, kind, iid, rdate, title, divs, url, seq, section, p0, p1, text in conn.execute(
        """SELECT d.doc_id, d.kind, d.item_id, d.report_date, coalesce(d.title, d.description), d.divisions,
                  d.url, s.seq, s.section, s.page_start, s.page_end, s.text
           FROM doc_section s JOIN doc d USING (doc_id)
           WHERE d.fetch_status = 'ok' ORDER BY d.doc_id, s.seq"""
    ):
        if section in SKIP_SECTIONS or kind not in DOC_KIND_TO_TYPE:
            continue
        it = items.get(iid, {})
        code, term = _code_term(iid)
        heading = f"{title} — {section.title()}" if section not in ("PREAMBLE", "BODY") else title
        for i, (s, e) in enumerate(split_passages(text)):
            yield ChunkRow(
                chunk_key=f"doc:{doc_id}:{seq}:{i}", doc_type=DOC_KIND_TO_TYPE[kind], source_id=doc_id,
                item_id=iid, meeting_date=it.get("meeting_date") or rdate, committee_code=code,
                committee=it.get("committee"), divisions=list(divs or []), wards=list(it.get("wards", [])),
                term=term, status=None, section=section, page=p0, page_end=p1, heading=heading,
                text=text[s:e], url=f"{url}#page={p0}",
            )  # fmt: skip


_COLS = (
    "chunk_key", "doc_type", "source_id", "item_id", "meeting_date", "committee_code", "committee",
    "divisions", "wards", "term", "status", "section", "page", "page_end", "heading", "text", "url",
    "embed_hash",
)  # fmt: skip


def write_chunks(conn: Conn, settings: Settings) -> dict[str, Any]:
    """Rebuild `chunk` idempotently: upsert by chunk_key, keep embeddings whose input is unchanged,
    attach cached embeddings, delete chunks whose source disappeared."""
    model = settings.llm_embedding_model or "unset"
    dim = settings.embedding_dim
    rows = list(build_chunks(conn))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS chunk_new")  # in case an outer transaction still holds one
        cur.execute(
            """CREATE TEMP TABLE chunk_new (
                 chunk_key text PRIMARY KEY, doc_type text, source_id text, item_id text, meeting_date date,
                 committee_code text, committee text, divisions text[], wards text[], term text, status text,
                 section text, page int, page_end int, heading text, text text, url text, embed_hash text
               ) ON COMMIT DROP"""
        )
        with cur.copy(f"COPY chunk_new ({', '.join(_COLS)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([getattr(r, c) for c in _COLS[:-1]] + [embed_hash(model, dim, r.embed_input())])
        cur.execute(
            f"""INSERT INTO chunk ({", ".join(_COLS)}) SELECT {", ".join(_COLS)} FROM chunk_new
                ON CONFLICT (chunk_key) DO UPDATE SET
                {", ".join(f"{c} = EXCLUDED.{c}" for c in _COLS[1:])},
                emb = CASE WHEN chunk.embed_hash = EXCLUDED.embed_hash THEN chunk.emb END"""  # noqa: S608
        )  # column names are the fixed _COLS tuple
        cur.execute(
            "DELETE FROM chunk c WHERE NOT EXISTS (SELECT 1 FROM chunk_new n WHERE n.chunk_key = c.chunk_key)"
        )
        deleted = cur.rowcount
        cur.execute(
            """UPDATE chunk c SET emb = e.emb FROM embedding_cache e
               WHERE c.emb IS NULL AND e.embed_hash = c.embed_hash"""
        )
        from_cache = cur.rowcount
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r.doc_type] = by_type.get(r.doc_type, 0) + 1
    return {
        "chunks": len(rows),
        "by_type": by_type,
        "deleted_stale": deleted,
        "embeddings_from_cache": from_cache,
    }


# --------------------------------------------------------------------------- embedding


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def embed_missing(
    conn: Conn,
    settings: Settings,
    llm: LLMClient,
    *,
    max_cost_usd: float,
    batch_tokens: int = 30_000,
    tokens_per_minute: int = 180_000,
    limit: int | None = None,
) -> dict[str, Any]:
    """Embed chunks without a vector. Paced under the deployment's tokens/minute, cached by
    content hash, committed batch by batch (resumable), and refused up front if the estimated
    cost exceeds `max_cost_usd`."""
    price = settings.embedding_price_per_m_tokens
    todo = conn.execute(
        """SELECT DISTINCT ON (embed_hash) embed_hash, heading, text FROM chunk
           WHERE emb IS NULL ORDER BY embed_hash"""
    ).fetchall()
    if limit is not None:
        todo = todo[:limit]
    inputs = [(h, f"{hd}\n\n{t}" if hd else t) for h, hd, t in todo]
    est_tokens = sum(estimate_tokens(x) for _, x in inputs)
    est_cost = est_tokens / 1e6 * price
    stats: dict[str, Any] = {
        "to_embed": len(inputs),
        "estimated_tokens": est_tokens,
        "estimated_usd": round(est_cost, 4),
    }
    if est_cost > max_cost_usd:
        stats["refused"] = f"estimated ${est_cost:.2f} exceeds --max-cost ${max_cost_usd:.2f}"
        return stats
    used_tokens = 0
    window_start, window_tokens = time.monotonic(), 0
    batch: list[tuple[str, str]] = []
    batches = [batch]
    acc = 0
    for h, x in inputs:
        t = estimate_tokens(x)
        if batch and (acc + t > batch_tokens or len(batch) >= 512):
            batch, acc = [], 0
            batches.append(batch)
        batch.append((h, x[: 8000 * 4]))
        acc += t
    for b in (b for b in batches if b):
        need = sum(estimate_tokens(x) for _, x in b)
        elapsed = time.monotonic() - window_start
        if elapsed >= 60:
            window_start, window_tokens = time.monotonic(), 0
        elif window_tokens + need > tokens_per_minute:
            time.sleep(60 - elapsed)
            window_start, window_tokens = time.monotonic(), 0
        for attempt in range(6):
            try:
                res = llm.embed([x for _, x in b])
                break
            except Exception as e:  # 429s/5xx after the SDK's own retries: back off, then retry
                if attempt == 5:
                    raise
                stats.setdefault("retries", []).append(f"{type(e).__name__}: {str(e)[:120]}")
                time.sleep(20 * (attempt + 1))
        window_tokens += need
        used_tokens += res.usage.prompt_tokens or need
        with conn.transaction(), conn.cursor() as cur:
            with cur.copy("COPY embedding_cache (embed_hash, model, dim, emb) FROM STDIN") as cp:
                for (h, _), v in zip(b, res.vectors, strict=True):
                    cp.write_row([h, res.model, len(v), "[" + ",".join(f"{f:.7g}" for f in v) + "]"])
            cur.execute(
                """UPDATE chunk c SET emb = e.emb FROM embedding_cache e
                   WHERE c.emb IS NULL AND e.embed_hash = c.embed_hash AND e.embed_hash = ANY(%s)""",
                ([h for h, _ in b],),
            )
    stats["embedded"] = len(inputs)
    stats["tokens_used"] = used_tokens
    stats["usd_spent_estimate"] = round(used_tokens / 1e6 * price, 4)
    return stats
