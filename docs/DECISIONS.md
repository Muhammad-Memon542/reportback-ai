# Decisions

Newest last. Format: date · decision · why · alternatives considered.

## 2026-11-23 · Phase 0

**D1. Flat `reportback/` package, not `src/reportback/`.**
CLAUDE.md and the plan refer to `reportback/llm/`, `generate/verifier.py`, and "mypy strict on `reportback/`". A flat layout keeps those paths literal. Built with `uv_build` (`module-root = ""`).

**D2. One LLM client, the OpenAI Python SDK, every provider via `base_url`.**
`reportback/llm/client.py` talks to Foundry (Azure OpenAI **v1** endpoint `https://<resource>.openai.azure.com/openai/v1/`), OpenAI, or a local Ollama (`http://localhost:11434/v1`). No Azure AI Inference SDK, no GitHub Models (retired), no LangChain. Model/deployment names and `EMBEDDING_DIM` are env-only. `temperature`/`max_completion_tokens` are sent only when a caller asks, because several current models reject a non-default `temperature`.
Structured output uses `chat.completions.parse(response_format=<Pydantic model>)` (strict JSON schema); refusals and empty parses raise `LLMResponseError`. `embed()` rejects vectors whose length ≠ `EMBEDDING_DIM`, so a wrong embedding deployment fails loudly before it can hit a `vector(1536)` column.

**D3. `httpx2` at the LLM boundary, `httpx` for crawling.**
openai 3.x depends on `httpx2` (pydantic's successor to httpx) and its `http_client=` parameter is typed `httpx2.Client`. The plan's stack names httpx for crawling; there is no reason to move the crawler. See LESSONS L1.

**D4. Every outbound fetch goes through `PoliteClient` (`reportback/ingest/http.py`).**
- Host allowlist enforced in code on **every request, including each redirect hop** (httpx request event hook); HTTPS only. Allowlist: `ckan0.cf.opendata.inter.prod-toronto.ca`, `secure.toronto.ca`, `www.toronto.ca`.
- ≥1.0 s between requests; the setting is validated `ge=1.0`, so it can't be configured faster.
- User-Agent `ReportBackTO/<version> (civic research on public council records[; +<HTTP_CONTACT_URL>])`. No personal email is hard-coded; a public project URL can be added via env.
- Raw cache under `data/raw/<host>/<sha16(url|version)>-<basename>`. `version` is the CKAN resource `last_modified`, so a re-published log file gets a **new** cache file rather than overwriting the old one (groundwork for "keep every snapshot").

**D5. Column discovery: datastore API when possible, XLSX download otherwise.**
Voting-record resources are datastore-active → `datastore_search?limit=0` gives fields + row count with zero rows downloaded (the CSV/JSON/XML files are 12–188 MB each). The Report Request Log is XLSX-only (not in the datastore) → download (~0.7 MB) and read the header row.

**D6. `smoke_ckan.py` doubles as a schema-drift check.**
It fails if any column later phases depend on disappears (`Status, Meeting Date, Item Number, Directive, Assigned to, Closed by`; `Agenda Item #, Vote, Result, Date/Time`).

**D7. CI runs no network or LLM calls on push/PR.**
`check` job: `uv sync --locked`, ruff check, ruff format --check, mypy, pytest. A manual `workflow_dispatch` input runs the live smoke tests (`ckan` is free; `ckan+llm` needs repo variables `LLM_*` and secret `LLM_API_KEY`).

**D8. Pre-commit hooks are local (`uv run …`), so they use the exact tool versions in `uv.lock`.**
Also blocks committing `data/raw/` and `.env`.

**D9. Dedicated cloud environment `reportback-to`.**
Its network allowlist is exactly the three source hosts above (plus package registries). It keeps project-specific hosts off the Default environment.

**D10. Report Request Log snapshots: committed to the project's own GitHub repo by a scheduled GitHub Actions job ("git scraping").** (Resolves the earlier Q1, 2026-11-23.)
- A daily workflow downloads each log XLSX through `PoliteClient`, hashes it, and commits only when the sha256 changes:
  - `data/snapshots/report_request_log/<term>/<last_modified>_<sha12>.xlsx`: the raw file, byte-for-byte. The glossary says `Item Number` and `Closed by` are hyperlinks, which an export would drop.
  - `…/<term>/latest.csv`: a normalized text export, so `git diff` and GitHub show exactly which rows changed status between snapshots.
  - `…/MANIFEST.csv`: url, resource last_modified, sha256, bytes, fetched_at.
- Heartbeat: if nothing has been committed under `data/snapshots/` for 30 days, the job updates `LAST_CHECKED`. This records "unchanged as of", which feeds `last_seen`. It also keeps the repo active, because GitHub disables scheduled workflows in public repos after 60 days without activity.
- Snapshotting moves from the Azure `ingest-daily` job to GitHub. Ingest reads the latest committed snapshot.
- Why: it's free (standard runners are free on public repos; a private repo would use about 30 of its monthly minutes), it outlives the Azure credit and the 12-month student subscription, and git history *is* the status-change history.
- Size: each changed snapshot adds about 0.7–1 MB of XLSX (zip doesn't delta-compress) plus a small CSV delta. At roughly monthly updates that's about 10–20 MB/year, far under GitHub's 1 GB repo guideline and its 100 MiB per-file limit.
- Rejected:
  - **Azure Storage**, even within the free 12-month quota: it needs a new resource, and the subscription (and the archive) is disabled when the credit or the term ends.
  - **Git LFS**: unnecessary at this size, and every LFS download counts against the bandwidth quota.
  - **GitHub Release assets**: no diffs, harder to browse.
  - **A separate data repo**: cleaner history, but long runs with no changes would trip the 60-day disable more easily.
  - Hugging Face and Zenodo stay options for *publishing* the archive later (e.g. a DOI for the blog post).

**D11. Azure regions are constrained by the student subscription's policy.** (2026-11-23)
The subscription carries the built-in "Allowed resource deployment regions" assignment: `belgiumcentral, swedencentral, canadacentral, northcentralus, francecentral`. No other policy assignments apply.
- **Foundry → `swedencentral`**: the widest Azure OpenAI model catalogue of the five, including Global Standard and Batch.
- **Postgres, Container Apps, App Insights (Phase 10) → `canadacentral`**: closest to Toronto users and data.
- The cross-region LLM calls add ~100 ms to calls that take seconds, and their payloads are a few KB, so the inter-region transfer cost is negligible.

**D12. Foundry deployments are chosen from the subscription's actual quota.** (2026-11-23)
`az cognitiveservices usage list -l swedencentral` shows non-zero Global Standard quota (thousands of tokens/min) for `gpt-5-mini` (500), `gpt4.1-mini` (200) and `text-embedding-3-small` (1000). `gpt-5.4-mini` has **0**.
- **Chat: `gpt-5-mini`.** It has the most headroom and is the newer model. Run with `LLM_REASONING_EFFORT=low` by default, because reasoning tokens bill as output.
- **`gpt-4.1-mini` is also deployed.** That costs nothing idle, and Phase 3/4 needs a second model for the judge-model and LLM-rerank comparisons.
- **Embeddings: `text-embedding-3-small`**, 1,536 dims, matching the schema.
- **No Batch API discount for these models.** The only Global Batch quota is for `gpt-35-turbo`. Phase 3 cost estimates must assume standard pricing, so caching, prefilters and `LINK_MAX_COST_USD` carry the whole cost control.

## Open questions (need a decision before/at Phase 1)

**Q2. What does "open/overdue" mean now that both term logs have been closed out?**
See DATASHEET: the 2022–2026 snapshot (2026-11-13) has **no** Open/Due/Ongoing rows; outstanding requests are `NCF` (Not Carried Forward). The plan's discrepancy rule "`log_status=Open/Overdue` but `delivered`" should become "`log_status ∈ {NCF, Open, Due, Ongoing}` but `delivered`". The 2026–2030 term log (new council sworn in Nov 2026) will reintroduce Open/Due rows as it's published.
