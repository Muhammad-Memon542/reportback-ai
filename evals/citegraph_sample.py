"""Draw the citation-graph precision sample (Phase 1 acceptance: >=95% on 100 hand-checked matches).

    uv run python evals/citegraph_sample.py            # writes evals/citegraph_precision.csv
    uv run python evals/citegraph_sample.py --force    # overwrite an existing (labelled!) file

Stratified: up to 50 short-form matches (the riskier kind: year inferred) + full-ID matches to
reach 100. Fixed seed. For targets we have not crawled, the TMMIS page is fetched (politely,
cached) so the checker can compare the target item's real title with the citing context.

Labelling rule (column `label`): 1 = the matched text refers to an agenda item AND the
normalized ID (including the inferred year) is the item the text means; 0 = otherwise.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

from reportback.config import get_settings
from reportback.db import connect
from reportback.ingest.http import PoliteClient
from reportback.ingest.text import decode_html
from reportback.ingest.tmmis import ITEM_URL, parse_item_page

OUT = Path(__file__).with_name("citegraph_precision.csv")
FIELDS = [
    "n",
    "method",
    "from_type",
    "from_id",
    "raw",
    "to_item_id",
    "target_title",
    "context",
    "label",
    "note",
]
SEED = 20261123


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if OUT.exists() and not args.force:
        print(f"{OUT} exists (may hold labels); use --force to overwrite")
        return 1
    s = get_settings()
    rng = random.Random(SEED)
    with connect(s) as conn:
        rows = conn.execute(
            """SELECT c.method, c.from_type, c.from_id, c.raw, c.to_item_id, i.title, c.context
               FROM cites c LEFT JOIN item i ON i.item_id = c.to_item_id AND i.fetch_status = 'ok'
               ORDER BY c.from_id, c.to_item_id"""
        ).fetchall()
    short = [r for r in rows if r[0] == "short_inferred"]
    full = [r for r in rows if r[0] == "full"]
    pick = rng.sample(short, min(50, len(short)))
    pick += rng.sample(full, min(100 - len(pick), len(full)))
    out = []
    with PoliteClient(s) as http:
        for n, (method, ftype, fid, raw, to, title, ctx) in enumerate(pick, 1):
            if title is None:
                page = parse_item_page(
                    decode_html(http.fetch_cached(ITEM_URL.format(item_id=to)).content), to
                )
                title = page.title if page.found else "(TMMIS: item not found)"
            out.append(dict(zip(FIELDS, [n, method, ftype, fid, raw, to, title, ctx, "", ""], strict=True)))
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    n_short = sum(o["method"] == "short_inferred" for o in out)
    print(f"wrote {len(out)} rows ({n_short} short-form) to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
