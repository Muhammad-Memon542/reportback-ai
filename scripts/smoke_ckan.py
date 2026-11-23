"""Smoke test: find the Report Request Log and Voting Record packages on Toronto Open Data
via CKAN package_search, then print each resource's URL and column names.

    uv run python scripts/smoke_ckan.py

Exit code 0 when both packages are found and the columns later phases rely on are present.
Column names come from the datastore API when a resource is datastore-active (no download);
XLSX resources (the Report Request Log) are downloaded once and cached under data/raw/.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from reportback.api.app import ATTRIBUTION
from reportback.config import get_settings
from reportback.ingest.ckan import CkanClient, Package, Resource, xlsx_headers
from reportback.ingest.http import PoliteClient


@dataclass(frozen=True)
class Target:
    label: str
    query: str
    title_must_contain: str
    required_columns: frozenset[str]


TARGETS = (
    Target(
        label="Report Request Log",
        query='"Report Request Log"',
        title_must_contain="report request log",
        required_columns=frozenset(
            {"Status", "Meeting Date", "Item Number", "Directive", "Assigned to", "Closed by"}
        ),
    ),
    Target(
        label="Voting Record",
        query='"Voting Record"',
        title_must_contain="city council - voting record",
        required_columns=frozenset({"Agenda Item #", "Vote", "Result", "Date/Time"}),
    ),
)


def find_package(ckan: CkanClient, t: Target) -> Package | None:
    hits = ckan.package_search(t.query, rows=10)
    matches = [p for p in hits if t.title_must_contain in p.title.lower()]
    print(f"\n== {t.label}: package_search({t.query!r}) -> {len(hits)} hits, {len(matches)} match")
    if len(matches) > 1:
        print("   WARNING: several packages match; using the first:", [p.name for p in matches])
    return matches[0] if matches else None


def columns_for(ckan: CkanClient, http: PoliteClient, r: Resource) -> tuple[list[str], str] | None:
    if r.datastore_active:
        fields, total = ckan.datastore_fields(r.id)
        return [f.id for f in fields], f"datastore, {total:,} rows"
    if r.format.upper() == "XLSX":
        fetched = http.fetch_cached(r.url, version=r.last_modified)
        sheet, cols, n = xlsx_headers(fetched.content)
        src = "cache" if fetched.from_cache else "downloaded"
        return cols, f"xlsx sheet {sheet!r}, {n:,} rows, {src}: {fetched.path}"
    return None


def main() -> int:
    settings = get_settings()
    failures: list[str] = []
    with PoliteClient(settings) as http:
        ckan = CkanClient(http, settings.ckan_base_url)
        for t in TARGETS:
            pkg = find_package(ckan, t)
            if pkg is None:
                failures.append(f"{t.label}: package not found")
                continue
            print(f"   name:      {pkg.name}\n   title:     {pkg.title}")
            print(f"   licence:   {pkg.license_title}   owner: {pkg.owner_division}")
            print(f"   refreshed: {pkg.last_refreshed}   rate: {pkg.refresh_rate}")
            seen: set[str] = set()
            for r in pkg.resources:
                print(f"\n   - {r.name} [{r.format}] modified={r.last_modified} size={r.size}")
                print(f"     {r.url}")
                cols = columns_for(ckan, http, r)
                if cols is None:
                    print("     columns: (not inspected for this format)")
                    continue
                names, how = cols
                seen.update(names)
                print(f"     columns ({how}):")
                print("       " + " | ".join(names))
            missing = sorted(t.required_columns - seen)
            if missing:
                failures.append(f"{t.label}: required columns missing {missing}")

    print(f"\n{ATTRIBUTION}")
    if failures:
        print("\nSMOKE CKAN: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("\nSMOKE CKAN: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
