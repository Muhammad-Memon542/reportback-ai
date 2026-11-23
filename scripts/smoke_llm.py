"""Smoke test for the LLM layer: chat, structured JSON, embeddings.

    uv run python scripts/smoke_llm.py

Reads LLM_* settings from the environment / .env (see .env.example). Makes 3 small calls
(well under $0.01 on any current small model). Exit 0 on success, 1 on failure, 2 if unconfigured.
"""

from __future__ import annotations

import math
import sys
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from reportback.config import get_settings
from reportback.llm import LLMClient, LLMConfigError


class ItemRef(BaseModel):
    item_id: str = Field(description="TMMIS item number, e.g. 2024.EX14.3")
    committee_code: str = Field(description="Committee code inside the item number, e.g. EX")
    meeting_year: int


SENTENCE = (
    "At its meeting on May 22, 2024, City Council adopted Item 2024.EX14.3 and requested the "
    "General Manager, Transportation Services to report back in the fourth quarter of 2024."
)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def main() -> int:
    s = get_settings()
    host = urlsplit(s.llm_base_url).netloc if s.llm_base_url else "(provider default)"
    print(f"provider={s.llm_provider} endpoint={host}")
    print(f"chat_model={s.llm_chat_model} embedding_model={s.llm_embedding_model} dim={s.embedding_dim}")
    try:
        llm = LLMClient(s)
    except LLMConfigError as e:
        print(f"\nSMOKE LLM: NOT CONFIGURED: {e}\nCopy .env.example to .env and fill in LLM_*.")
        return 2

    failures: list[str] = []
    tokens = 0

    print("\n[1] chat")
    try:
        r = llm.chat([{"role": "user", "content": "Reply with exactly the word: pong"}], max_tokens=2000)
        tokens += r.usage.total_tokens
        ok = "pong" in r.text.lower()
        print(f"    {r.text.strip()!r}  model={r.model} tokens={r.usage.total_tokens} {r.latency_ms:.0f}ms")
        if not ok:
            failures.append("chat: reply did not contain 'pong'")
    except Exception as e:  # report every stage, don't stop at the first failure
        failures.append(f"chat: {type(e).__name__}: {e}")

    print("\n[2] structured JSON")
    try:
        j = llm.chat_json(
            [
                {"role": "system", "content": "Extract the agenda item reference. Use only the text."},
                {"role": "user", "content": SENTENCE},
            ],
            ItemRef,
            max_tokens=4000,
        )
        tokens += j.usage.total_tokens
        print(f"    {j.value.model_dump()}  tokens={j.usage.total_tokens} {j.latency_ms:.0f}ms")
        if (j.value.item_id, j.value.committee_code, j.value.meeting_year) != ("2024.EX14.3", "EX", 2024):
            failures.append(f"json: unexpected extraction {j.value.model_dump()}")
    except Exception as e:
        failures.append(f"json: {type(e).__name__}: {e}")

    print("\n[3] embeddings")
    try:
        texts = [
            "ward-based maintenance teams pilot",
            "pilot program for maintenance crews organized by ward",
            "recorded vote on the Speaker election",
        ]
        e = llm.embed(texts)
        tokens += e.usage.total_tokens
        near, far = cosine(e.vectors[0], e.vectors[1]), cosine(e.vectors[0], e.vectors[2])
        print(
            f"    dim={len(e.vectors[0])} cos(paraphrase)={near:.3f} cos(unrelated)={far:.3f} "
            f"model={e.model} {e.latency_ms:.0f}ms"
        )
        if not near > far:
            failures.append("embed: paraphrase not closer than unrelated text")
    except Exception as e2:
        failures.append(f"embed: {type(e2).__name__}: {e2}")

    llm.close()
    print(f"\ntotal tokens: {tokens}")
    if failures:
        print("SMOKE LLM: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("SMOKE LLM: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
