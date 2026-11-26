from __future__ import annotations

import random
from pathlib import Path

import pytest

from reportback.config import Settings
from reportback.db import Conn
from reportback.index.chunk_embed import (
    MAX_CHARS,
    build_chunks,
    embed_hash,
    split_passages,
    write_chunks,
)
from reportback.ingest.background import parse_pdf, save_sections
from reportback.ingest.divisions import default_mapper
from reportback.ingest.text import decode_html
from reportback.ingest.tmmis import ITEM_URL, parse_item_page, save_item
from tests.conftest import FIXTURES, make_settings


def _check(text: str) -> list[tuple[int, int]]:
    spans = split_passages(text)
    for s, e in spans:
        assert text[s:e] in text and e - s <= MAX_CHARS
    # Coverage: every non-space character belongs to some passage.
    covered = [False] * len(text)
    for s, e in spans:
        for i in range(s, e):
            covered[i] = True
    assert all(c or text[i].isspace() for i, c in enumerate(covered))
    return spans


def test_short_text_is_one_passage() -> None:
    assert split_passages("One directive.") == [(0, 14)]
    assert split_passages("   ") == []


def test_long_section_splits_on_lines_with_overlap() -> None:
    lines = [f"Paragraph {i}: " + "word " * random.Random(i).randint(20, 60) for i in range(80)]
    text = "\n".join(lines)
    spans = _check(text)
    assert len(spans) > 3
    # passages end at line ends, and consecutive passages overlap
    assert all(text[e - 1] != " " for _, e in spans)
    assert any(spans[i + 1][0] < spans[i][1] for i in range(len(spans) - 1))


def test_one_giant_line_falls_back_to_sentences_and_spaces() -> None:
    text = " ".join(f"Sentence number {i} says something about transit." for i in range(400))
    _check(text)
    _check("x" * 10_000)  # no boundaries at all: hard cuts, still substrings


def test_real_comments_section_is_covered() -> None:
    r = parse_pdf((FIXTURES / "background" / "backgroundfile-234496.pdf").read_bytes())
    comments = next(s for s in r.sections if s.section == "COMMENTS").text
    assert len(comments) > MAX_CHARS
    _check(comments)


def test_embed_hash_depends_on_model_and_text() -> None:
    a = embed_hash("m1", 1536, "text")
    assert a == embed_hash("m1", 1536, "text")
    assert a != embed_hash("m2", 1536, "text") and a != embed_hash("m1", 1536, "text2")


def _settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path, llm_embedding_model="text-embedding-3-small")


def test_build_and_write_chunks(db: Conn, tmp_path: Path) -> None:
    html = decode_html((FIXTURES / "tmmis" / "2023.PH2.1.html").read_bytes())
    save_item(
        db, parse_item_page(html, "2023.PH2.1"), ITEM_URL.format(item_id="2023.PH2.1"), "h", default_mapper()
    )
    save_sections(
        db,
        "backgroundfile-234496",
        parse_pdf((FIXTURES / "background" / "backgroundfile-234496.pdf").read_bytes()),
        "fh",
    )
    db.execute("UPDATE doc SET fetch_status = 'ok' WHERE doc_id = 'backgroundfile-234496'")

    rows = list(build_chunks(db))
    types = {r.doc_type for r in rows}
    assert {"decision", "item_summary", "staff_report"} <= types
    report = [r for r in rows if r.doc_type == "staff_report"]
    assert all(r.section not in ("SIGNATURE", "CONTACT") for r in report)
    dh = [r for r in report if r.section == "DECISION HISTORY"]
    assert dh and dh[0].url.endswith("#page=2") and dh[0].heading and "Decision History" in dh[0].heading
    assert all(r.term == "2022-2026" and r.committee_code == "PH" for r in rows)
    assert all(r.divisions == ["City Planning", "Development Review"] for r in report)

    s = _settings(tmp_path)
    first = write_chunks(db, s)
    # Simulate an embedding for one chunk, then rebuild: unchanged chunks keep their vector.
    db.execute(
        "UPDATE chunk SET emb = array_fill(0.1, ARRAY[1536])::vector WHERE chunk_key LIKE 'decision:%'"
    )
    second = write_chunks(db, s)
    assert first["chunks"] == second["chunks"] and second["deleted_stale"] == 0
    kept = db.execute(
        "SELECT count(*) FROM chunk WHERE chunk_key LIKE 'decision:%' AND emb IS NOT NULL"
    ).fetchone()
    assert kept is not None and kept[0] > 0
    # Every chunk text is an exact substring of its source section.
    bad = db.execute(
        """SELECT count(*) FROM chunk c JOIN doc_section s
             ON c.source_id = s.doc_id AND c.chunk_key LIKE 'doc:' || s.doc_id || ':' || s.seq || ':%'
           WHERE position(c.text IN s.text) = 0"""
    ).fetchone()
    assert bad == (0,)


@pytest.mark.parametrize("n", [1, 5])
def test_split_is_deterministic(n: int) -> None:
    text = "\n".join("line " * 50 for _ in range(30 * n))
    assert split_passages(text) == split_passages(text)
