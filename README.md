# ReportBack TO

**Council asked for it. Did City Hall ever report back?**

ReportBack links Toronto City Council's report-back directives (from the City Clerk's *Report Request Log*) to the later staff reports that answered them. You can ask it questions in plain language and get cited answers. Every answer shows the **Clerk's log status** and **ReportBack's evidence-based status** side by side.

> Status: **Phase 1 (ingestion)**. See [`docs/PLAN.md`](docs/PLAN.md) and [`docs/COVERAGE.md`](docs/COVERAGE.md).

## Pipeline

```
question → input guardrail → query router/rewriter → hybrid search + metadata filter (top 20)
         → reranker (top 5) → link expansion + cited structured output + verifier
         → observability → evals
```

## Quickstart

```bash
uv sync
cp .env.example .env   # fill in LLM_* and DATABASE_URL
uv run pre-commit install
uv run pytest -q && uv run ruff check . && uv run mypy
```

You need Postgres 16 with `pgvector` and `pg_trgm` (CI uses the `pgvector/pgvector:pg16` image):

```bash
uv run reportback db migrate
uv run reportback snapshot            # archive the Clerk's Report Request Log
uv run reportback ingest log          # directives (both term logs)
uv run reportback ingest votes        # 2022-2026 voting record
uv run reportback ingest tmmis        # agenda item pages (1 request/second)
uv run reportback ingest background   # staff-report PDFs, split by section
uv run reportback ingest citegraph    # item-ID citations between documents
uv run reportback coverage            # writes docs/COVERAGE.md
```

## Phase 1 results: the data

Full counts and parse quality are in [`docs/COVERAGE.md`](docs/COVERAGE.md); source quirks are in [`docs/DATASHEET.md`](docs/DATASHEET.md).

| Source | Loaded |
|---|---|
| Report Request Log (2018–2022 and 2022–2026, archived daily in `data/snapshots/`) | 4,434 rows → 4,395 directives; 1,862 name the item that closed them (ground truth) |
| Voting record, 2022–2026 | 46,815 recorded votes on 1,110 items |
| TMMIS agenda items (3 crawl hops) | 2,898 items, 4,134 committee/Council considerations |
| Staff reports, letters, briefing notes, answers | 2,818 PDFs → 23,561 sections; 93% of staff reports have a DECISION HISTORY section |
| Citation graph | 7,940 edges between items, reports and directives |

- **Citation-graph precision:** 99/100 on a hand-checked stratified sample (50/50 full IDs, 49/50 inferred short forms).
- **Idempotent:** re-running every ingest step leaves all tables unchanged (`scripts/check_idempotency.py`).
- **Early signal for linking:** for 95% of the Clerk's ground-truth (directive → closing item) pairs, the closing item or its report cites the directive's item number. That's recall of one signal; Phase 3 measures precision and full linking.

## Prior art & gap

Meeting search and summarization tools exist (Hamlet, citymeetings.nyc, CivicSearch, Civicly.ai), as do Toronto vote trackers (Climate Voting Records, TrackYourVoteTO, Councillor Voting Behaviour) and internal city motion trackers (e.g. Vancouver, York UK). We found no public tool that links Council's report-back directives to the staff reports that answered them and lets you ask questions about it.

## Neutrality and privacy

ReportBack describes public records. It never rates councillors, takes political positions, or gives voting advice. Public communications (letters from residents) are not indexed, and emails and phone numbers are stripped from indexed text.

Data sources and their quirks: [`docs/DATASHEET.md`](docs/DATASHEET.md).

---

Contains information licensed under the Open Government Licence – Toronto. ReportBack is not an official City of Toronto service.
