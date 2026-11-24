"""Compute citation-graph precision from the labelled sample.

uv run python evals/citegraph_precision.py
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

SAMPLE = Path(__file__).with_name("citegraph_precision.csv")
TARGET = 0.95


def main() -> int:
    rows = list(csv.DictReader(SAMPLE.open(encoding="utf-8")))
    unlabelled = [r["n"] for r in rows if r["label"] not in ("0", "1")]
    if unlabelled:
        print(f"{len(unlabelled)} rows unlabelled: {unlabelled[:10]}")
        return 2
    tot, ok = Counter(), Counter()
    for r in rows:
        for key in (r["method"], "all"):
            tot[key] += 1
            ok[key] += r["label"] == "1"
    for key in ("full", "short_inferred", "all"):
        if tot[key]:
            print(f"{key:15s} {ok[key]:3d}/{tot[key]:3d} = {ok[key] / tot[key]:.1%}")
    passed = ok["all"] / tot["all"] >= TARGET
    print("PASS" if passed else "FAIL", f"(target >= {TARGET:.0%})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
