Database snapshot of ReportBack TO, made 2026-11-26 at commit `f501075`.

Restore into Postgres 16 with `vector` and `pg_trgm` installed:

```bash
uv run python scripts/db_release.py fetch --tag data-2026-11-26
```

| table | rows |
|---|---|
| `log_snapshot` | 3 |
| `directive` | 4,395 |
| `log_entry` | 6,413 |
| `vote` | 46,815 |
| `item` | 2,943 |
| `item_consideration` | 4,151 |
| `doc` | 7,553 |
| `doc_section` | 23,657 |
| `cites` | 7,998 |

Migrations applied: 001_init.sql.
File: `reportback-db-2026-11-26.pgdump` (26.5 MB, pg_dump custom format).
sha256: 16f141c010e38abcbd5825180861975912cb08b6fbacdaf5ac9d059de713262f

What is inside: the City Clerk's Report Request Log (both terms), the 2022–2026 voting record, parsed
TMMIS agenda items, staff-report text split by section, and the citation graph. Letters and deputations
from members of the public are not stored (counts only); emails and phone numbers are removed from text.
See docs/COVERAGE.md and docs/DATASHEET.md.

Contains information licensed under the Open Government Licence – Toronto. ReportBack is not an official City of Toronto service.
