# CLAUDE.md — ReportBack TO

ReportBack links Toronto City Council's report-back directives (from the City Clerk's Report Request Log) to the later staff reports that answered them, and answers questions about it. The pipeline order is fixed: **input guardrail → query router/rewriter → hybrid search + metadata filter (top 20) → reranker (top 5) → link expansion + cited structured output + verifier → observability → evals.** The full plan is in `docs/PLAN.md`. Read it before each phase, work **one phase at a time**, stop when the acceptance criteria pass, summarize, and wait for me.

## Non-negotiables
- **Neutral and factual.** Never rate councillors, take political positions, or give voting advice. Summaries describe records ("No later report was found that responds to this directive"), with no evaluative words about people.
- **Every item ID, date, vote count, and quote is verified in code** against DB rows or source text (`generate/verifier.py`). Status labels come only from the deterministic status function. Never weaken the verifier to pass a test.
- **Always show the Clerk's log status and ReportBack's evidence-based status side by side.** Never overwrite the official status.
- **Privacy:** do not index public communications or letters. Strip emails and phone numbers from indexed text. Never store personal information in `query_trace`.
- **Sources come from an allowlist only:** Toronto Open Data (CKAN) datasets, TMMIS agenda item pages, and the background files linked from them. 1 req/sec, descriptive User-Agent, cache raw files in `data/raw/` (gitignored). Inspect real page structure; never guess selectors or URL patterns. Attribution: "Contains information licensed under the Open Government Licence – Toronto."

## Engineering conventions
- Python 3.12, `uv`, ruff + mypy (strict on `reportback/`), pytest. Run `uv run pytest -q && uv run ruff check .` before saying you're done.
- One module per pipeline stage, with typed Pydantic I/O; each stage writes its part of the trace. No LangChain/LlamaIndex.
- All model calls go through `reportback/llm/`. Model names, deployments, and embedding dimensions live in config/env. Use the OpenAI Python SDK for Foundry. Don't use GitHub Models (retired) or the Azure AI Inference SDK.
- The reranker runs via ONNX Runtime on CPU. No paid rerank APIs without asking.
- **Protect the $100 credit:** cache every LLM judgment by content hash; respect `LINK_MAX_COST_USD`; cheap evals per PR, full evals nightly or on manual trigger only (ask before `--suite full`); `minReplicas: 0`; ask before creating any paid SKU.
- SQL migrations are numbered files in `backend/migrations/`. Keep every downloaded snapshot of the Report Request Log.
- Log decisions in `docs/DECISIONS.md`, failures in `docs/LESSONS.md`, and sources and terms in `docs/DATASHEET.md`.

## Commands (keep updated)
- `uv sync` · `uv run pre-commit install`
- Checks: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy` (set `TEST_DATABASE_URL` to also run the Postgres tests)
- `uv run python scripts/smoke_llm.py` · `uv run python scripts/smoke_ckan.py`
- `uv run reportback serve` (local API, `GET /healthz`)
- `uv run reportback db migrate` · `uv run reportback db dump|restore [file]`
- `uv run python scripts/db_release.py publish` (DB dump → `db-snapshot` branch → GitHub Release) · `uv run python scripts/db_release.py fetch` (download, verify, restore)
- `uv run reportback snapshot` (archive new Report Request Log versions into `data/snapshots/`; also runs daily in GitHub Actions)
- Ingest order: `uv run reportback ingest log` → `ingest votes` → `ingest tmmis` → `ingest background` → `ingest citegraph` → repeat `ingest tmmis` (next hop) / `background` / `citegraph`
- `uv run reportback coverage` (writes `docs/COVERAGE.md`)
- `uv run python evals/citegraph_sample.py` · `uv run python evals/citegraph_precision.py`
- `uv run reportback link --max-cost 5`
- `uv run python evals/run_evals.py --suite cheap` / `--suite full`
- `azd up`
