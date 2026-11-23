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

**D13. `NCF` counts as unresolved, like Open/Due.** (Resolves the earlier Q2; decided by the project owner, 2026-11-23.)
Both term logs were closed out, and every outstanding request became `NCF` (Not Carried Forward). For discrepancy finding, `log_status ∈ {Open, Due, Ongoing, Pending, NCF}` is treated as "unresolved per the log". So "log says unresolved, but a later report responds" is a discrepancy whether the row says Open or NCF. The published status is still stored and shown verbatim; only the normalized `log_status_norm` and the discrepancy rule group them.

## 2026-11-23 · Phase 1

**D14. One `directive` per (item, text); every log row kept in `log_entry`.**
The log sometimes repeats a directive: one row per closing item, or the same directive in both term logs (NCF in 2018–2022, Closed in 2022–2026). `directive_id = sha256(normalized item ID | whitespace-normalized text)[:20]`.
- Aggregates come from the **most recent** snapshot that lists the directive: verbatim `log_status` (distinct values joined with ` | `), `log_status_norm` (or `mixed`), the union of closing items, and the assignees.
- Raw rows are never discarded.
- Directives that no log row points to any more (e.g. after an item-number repair rule changes their key) are deleted, so re-runs converge.

**D15. Due dates are conservative.**
`due_date` is the end of the stated window (Q2 2024 → 2024-06-30; "In 2025" → 2025-12-31; "July 2024" → 2024-07-31). These get no due date, and a `due_rule` saying why: budget-relative, recurring, conditional ("If necessary", "Once regulations are released") and meeting-relative timings, and ambiguous d/m dates (`10/11/2023`). The Clerk's "Date for Order" is kept as `clerk_order_date` but **not** used: 1,191 rows carry the term-end placeholders 2022-11-14 / 2026-11-14.

**D16. Item-ID repair and year inference.** Malformed IDs in the log are repaired only where the intent is unambiguous: a missing dot, an extra dot, a hyphen, a space. Year labels for short forms come from a meeting index built from well-formed IDs, restricted to the row's term or narrowed by a nearby date. If more than one year fits, the reference is skipped. A stray-digit year (`20263MM42.22`) is accepted only if the index confirms it.

**D17. TMMIS crawl scope and privacy.** Seeds are the 2022–2026 directives' items, their closing items, and voted items, plus `cites` targets whose year label falls in the term. Expansion is one hop per run. "Communications" (public letters) and "Speakers" (deputants) are reduced to counts; motions and per-motion vote tables on the page are skipped (votes come from CKAN). Text we store is passed through `scrub_pii`.

**D18. Background files downloaded by default: `report`, `letter`, `briefing_note`.** Briefing notes are included because the log's "Closed by" sometimes names one (`BN14-Jan 26`). Attachments, notices, presentations, budget notes and mayoral directions are listed in `doc` but not downloaded unless asked (`--kinds`). PDFs over 40 MB or not starting with `%PDF` are marked `skipped`.

**D19. DB connections are autocommit.** Each `with conn.transaction():` is then a real transaction (see LESSONS L9), so the crawler commits item by item and an interruption loses nothing.

**D20. PyMuPDF is AGPL-3.0.** The plan names it, and it's the most reliable extractor for these PDFs (fonts and bold flags drive the heading detection). Using it means the deployed service's source must be available under AGPL terms. That's compatible with a public repository, but the project licence you choose must allow it. If that's a problem: pdfplumber (MIT) is the fallback, at the cost of slower parsing.
