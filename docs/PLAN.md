# ReportBack TO — Build Plan

**"Council asked for it. Did City Hall ever report back?"**
A cited answer engine that tracks what Toronto City Council *asks staff to do*, and whether staff ever delivered.

Toronto Council constantly directs staff to "report back" (on a pilot, a policy, a cost study). Those directions pile up:
- In 2022, Spacing counted **1,734 report requests** in under four years of the Clerk's log, with **393 overdue**.
- In 2020, the City Manager reported **over 600 outstanding directives** from Committees and Council in that term.
- The City Clerk publishes a **Report Request Log** (status, item number, directive text, who it's assigned to, report-back timing, and what closed it) as an **open dataset**. But it's a spreadsheet, so nobody can ask it questions in plain language.

ReportBack lets a resident, tenant association, community group, or student journalist ask:
> *"Did the City ever report back on the ward-based maintenance teams pilot?"*
> *"What has Council asked Transportation Services to report on since 2023 that's still open?"*
> *"Our residents' association deputed on the Dufferin bike lanes. What did Council direct afterwards, and what happened?"*

You get back the **original directive** (with the item number and link), **who it was assigned to and when it was due**, the **status according to the Clerk's log**, and **ReportBack's own evidence**: later staff reports that cite or respond to the directive, each with a quote and link. When the log and the evidence disagree, it says so.

> Put this file in `docs/PLAN.md` and `CLAUDE.md` in the repo root. Work one phase at a time and stop at each phase's acceptance criteria.

---

## 0. Why this is unique (the three tests)

**1. Nobody has built it (as far as research found, Nov 2026).**
- Plenty of tools *search or summarize meetings*: Hamlet (3,000+ US bodies), citymeetings.nyc, CivicSearch (547 US/Canadian places), Civicly.ai, an AI podcast on Toronto council, and Toronto vote trackers (Climate Voting Records, TrackYourVoteTO, Councillor Voting Behaviour).
- Some cities keep **internal** motion trackers (Vancouver's SharePoint tracker, York UK), and Toronto's Auditor General tracks *its own* recommendations.
- **No public tool found links Council's report-back directives to the staff reports that answered them and lets you ask questions about it.** Put a "Prior art" section in the README with these names. Saying "here's what exists and here's the gap" is itself a strong signal.

**2. Unusual technical twist: follow-through linking + a free, real ground truth.**
- The core problem isn't "retrieve a passage." It's **"link directive A (2023) to response B (2025)"** across years, divisions, and renamed committees. That takes temporal metadata filters, citation-graph edges, and reranking.
- The Clerk's log already records **what closed each directive**. That's **ground-truth labels for your retrieval evals that you didn't have to write by hand.** Few student projects have real labels.
- **Discrepancy finding:** items the log calls *open* that a later report clearly answered, and items marked *closed* whose closing report doesn't seem to address the directive. That's a genuinely new finding you can write a blog post about.

**3. Unusual audience: the people who show up.** Residents who give deputations, tenant and residents' associations, small community papers, high-school civics classes. Lobbyists and big newsrooms already have tools; these people have a PDF and a search box. They care less about "what was said" and more about **"what did Council actually ask for, and did it happen?"**

**Real-user hook:** Toronto Open Data runs public events (e.g., Open Data Demo Night, Nov 25, 2026, 6–8:30pm, Metro Hall Room 308/309: go meet the community before you build) and the Open Data Gallery/Awards, and Civic Tech Toronto is very active. Demo it there and to one or two residents' associations. "Used by X community groups" beats any metric.

**Neutrality (non-negotiable):** ReportBack describes public records. It **never rates councillors, takes political positions, or gives voting advice.** Answers report what the documents say, with citations.

---

## 1. Your pipeline, applied to this domain

```
Question
  ▼ 1. INPUT GUARDRAIL
  │    scope (Toronto council/committee records), injection, PII masking,
  │    political-opinion requests ("who's the worst councillor?", "who should I vote for?") → neutral reframe/refusal
  ▼ 2. QUERY ROUTER + REWRITER
  │    intents: directive_status | directive_list | decision_lookup | item_history | vote_lookup | out_of_scope
  │    entities: topic, item_id (normalize "EX22.13" → "2021.EX22.13"), committee, division (synonyms:
  │    "TTC", "Transportation", "Transportation Services"), ward, date range ("last spring" → 2025-03-20..06-20),
  │    status filter (open/overdue/closed)
  │    rewrite: bureaucratic phrasing + 1–3 sub-queries
  │    vote_lookup + directive_list → structured SQL path (not vector); the rest → retrieval path
  ▼ 3. HYBRID SEARCH + METADATA FILTER → top 20
  │    BM25 (tsvector) ∪ pgvector → RRF
  │    filters: doc_type (directive|decision|staff_report|item_summary), meeting_date range,
  │    committee, assigned_division, ward, term, status
  ▼ 4. RERANKER → top 5 (ONNX cross-encoder on CPU)
  ▼ 5. LINK EXPANSION + CITED STRUCTURED OUTPUT
  │    for the top directive(s): pull precomputed follow-through links (Phase 3) → LLM writes the JSON answer
  │    → VERIFIER: every item ID, date, and quote must exist in the retrieved/linked records
  ▼ 6. OBSERVABILITY: OTel span per stage → App Insights (latency, tokens, $, filter hits, rerank scores)
  ▼ 7. RAG EVALS: Clerk's-log ground truth + hand-written QA → CI gate
```

**Stack:** Python 3.12, FastAPI, Pydantic v2, psycopg 3, `uv`, pytest, httpx + selectolax (HTML), PyMuPDF (PDF), `sentence-transformers` → ONNX cross-encoder, Next.js 15, Bicep + `azd`, GitHub Actions. No LangChain/LlamaIndex.

---

## 2. Azure for Students constraints (re-check in the portal)

1. **$100 credit / 12 months, no credit card.** When it runs out, the subscription is disabled. Set Budget alerts at $5 / $15 / $30.
2. **Foundry / Azure OpenAI is unreliable on student subscriptions.** → Phase 0 smoke test, a provider-agnostic LLM layer, and an Ollama fallback. Use the OpenAI Python SDK. GitHub Models was retired on July 30, 2026.
3. **AI Search Free = 50 MB, no semantic ranker.** → **Postgres Flexible + pgvector + tsvector**. Check whether the free B1MS offer applies to you; otherwise stop the server when idle.
4. **Container Apps** free grant (180k vCPU-s, 360k GiB-s, 2M requests per month). Use scale-to-zero. The reranker runs on CPU inside `api`.
5. **The expensive part is offline linking** (LLM-judging thousands of directive↔report pairs). Use cheap prefilters first (citation graph + BM25), judge only the top candidates, cache every judgment by content hash, and use the **Batch API discount** if your Foundry deployment supports it.
6. Use ghcr.io instead of Azure Container Registry.

---

## 3. Data sources (allowlist)

| Source | What | How |
|---|---|---|
| **Report Request Log – City Council and its Committees** (Toronto Open Data, CKAN) | One row per directive: status, requesting body, meeting date, item number, agenda title, directive text, report-to body, timing, assigned to, **closed by** | CKAN `package_show` → resources (CSV/XLSX). Keep every snapshot you download, because status changes over time are data too |
| **Members of Toronto City Council – Voting Record** (Toronto Open Data) | Recorded votes per motion/member | CKAN. Caveat: not all votes are recorded electronically (show this in the UI) |
| **TMMIS agenda item pages** (`secure.toronto.ca/council/agenda-item.do?item=YYYY.XXN.N`) | Item summary, origin, decision text, links to background files | Polite crawl of item IDs found in the log, voting data, and citations. 1 req/sec, cache raw HTML. **Inspect the page structure; don't guess selectors** |
| **Background files** (`toronto.ca/legdocs/mmis/YYYY/xx/bgrd/backgroundfile-NNNNN.pdf`) | Staff reports, which usually begin with "At its meeting on …, City Council considered item …" | Download only PDFs linked from crawled items; PyMuPDF; cache by hash |
| *(Excluded by default)* **Communications** (letters from the public) | Contain private individuals' names and addresses | **Do not index.** Store only counts per item |

Licence: Open Government Licence – Toronto. Put the attribution in the footer and README. Log the terms in `docs/DATASHEET.md`.

**MVP scope:** the **2022–2026 council term** (directives + the items and reports they lead to through today). Add 2018–2022 later for longer follow-through histories.

---

## 4. Data model (Postgres)

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE directive (                      -- one row per report request (from the Clerk's log)
  directive_id    text PRIMARY KEY,           -- stable hash of (item_id, directive text)
  item_id         text NOT NULL,              -- "2023.EX3.1"
  requesting_body text, meeting_date date, agenda_title text,
  directive_text  text NOT NULL,
  report_to       text, timing_text text, due_date date NULL,   -- parsed from timing ("second quarter of 2024")
  assigned_to     text[], divisions text[],   -- normalized
  log_status      text,                       -- as published (Closed / Open / Overdue / …)
  closed_by_item  text[] NULL,                -- ground truth when present
  first_seen date, last_seen date,            -- across log snapshots
  emb vector(1536),
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(agenda_title,'')||' '||directive_text)) STORED
);

CREATE TABLE item (                           -- TMMIS agenda items
  item_id text PRIMARY KEY, meeting_date date, committee text, title text,
  wards text[], origin text, summary text, decision_text text,
  status text, url text NOT NULL, fetched_at timestamptz, content_hash text
);

CREATE TABLE doc (                            -- staff reports / background files
  doc_id text PRIMARY KEY, item_id text REFERENCES item, title text, report_date date,
  from_officials text[], divisions text[], url text NOT NULL, file_hash text
);

CREATE TABLE chunk (
  chunk_id bigserial PRIMARY KEY,
  doc_type text NOT NULL,          -- directive | decision | item_summary | staff_report
  source_id text NOT NULL,         -- directive_id / item_id / doc_id
  item_id text, meeting_date date, committee text, divisions text[], wards text[], term text,
  section text, page int, text text NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  emb vector(1536), url text NOT NULL, content_hash text
);
CREATE INDEX ON chunk USING gin (tsv);
CREATE INDEX ON chunk USING hnsw (emb vector_cosine_ops);
CREATE INDEX ON chunk (doc_type, meeting_date);
CREATE INDEX ON chunk USING gin (divisions);

CREATE TABLE cites (                          -- citation graph: a later doc/item references an earlier item ID
  from_id text, to_item_id text, context text, PRIMARY KEY (from_id, to_item_id)
);

CREATE TABLE followthrough (                  -- ReportBack's own linking results
  directive_id text REFERENCES directive, candidate_item_id text, candidate_doc_id text,
  method text,                    -- cites_edge | retrieval | both
  judge_verdict text,             -- responds_fully | responds_partly | mentions_only | unrelated
  evidence_quote text, judge_model text, judged_at timestamptz,
  PRIMARY KEY (directive_id, candidate_item_id)
);

CREATE TABLE vote (item_id text, motion text, member text, vote text, result text, meeting_date date);

CREATE TABLE query_trace (id uuid PRIMARY KEY, ts timestamptz DEFAULT now(), stages jsonb,
                          latency_ms jsonb, tokens jsonb, cost_usd numeric, feedback text);
```

---

## 5. Phases

### Phase 0: Skeleton + smoke tests (½–1 day)
- uv project, ruff/mypy/pytest, pre-commit, FastAPI `/healthz`, CI.
- `scripts/smoke_llm.py` (chat + structured JSON + embeddings), and `scripts/smoke_ckan.py` (find the Report Request Log and Voting Record packages through CKAN `package_search`; print the resource URLs and column names).

**Accept when:** both scripts pass and CI is green.

### Phase 1: Ingestion (4–6 days) ⭐ data engineering
- `ingest/report_log.py`: download every available log resource, **keep each snapshot**, normalize columns, and parse `timing_text` → `due_date` (e.g., "second quarter of 2024" → 2024-06-30; "first quarter" → Mar 31; "in 2025" → Dec 31; unparseable → NULL). Normalize `assigned_to` → `divisions[]` with a synonyms YAML, because titles and division names change over time.
- `ingest/votes.py`: load the voting record.
- `ingest/tmmis.py`: crawl item pages for every item ID referenced by the log, the votes, or `cites`, expanding **one hop** at a time. Parse the summary, decision, wards, committee, and background-file links. Rate limit and cache.
- `ingest/background.py`: download linked staff-report PDFs and split them by headings (*SUMMARY*, *RECOMMENDATIONS*, *DECISION HISTORY*, *COMMENTS*…) with page numbers.
- `ingest/citegraph.py`: regex for item IDs (`\b(20\d\d)\.[A-Z]{2,4}\d{1,3}\.\d{1,3}\b` plus short forms like "EX22.13" with year inference from the surrounding "At its meeting on …" sentence) → `cites` edges.
- **Privacy:** skip communications; strip email/phone patterns from any indexed text.

**Accept when:** row counts per source are committed in a coverage report; parsers are tested on 15 saved fixtures (log rows with odd timing text, an item page, a staff report with a decision-history section); the cite-graph regex has ≥95% precision on 100 hand-checked matches; and re-runs are idempotent.

### Phase 2: Index + hybrid retrieval with metadata filters (2–3 days)
- `index/chunk_embed.py` → `chunk` (directives get one chunk each; staff reports get one chunk per section).
- `retrieval/{bm25,dense,fusion,filters}.py`: RRF over BM25 + pgvector; SQL `WHERE` built from router entities (date range, committee, division, ward, doc_type, status). Return the **top 20** with scores.

**Accept when:** retrieval-only evals (Phase 6 seed: 40 items) report Recall@20 for BM25 / dense / hybrid / hybrid+filters.

### Phase 3: Follow-through linker (4–5 days) ⭐ the unique part
For each directive:
1. **Candidates:**
   - (a) items/docs with a `cites` edge to the directive's `item_id` and a later date;
   - (b) hybrid search over `staff_report`/`decision` chunks with `meeting_date > directive.meeting_date`, divisions overlapping `assigned_to`, and the query built from directive text + agenda title;
   - (c) union → rerank → top 5.
2. **Judge:** an LLM with a strict schema → `{verdict, evidence_quote, reason}`. The quote must be a substring of the candidate's text (verified in code). Cache by (directive hash, candidate hash).
3. **Derived status** (deterministic), next to `log_status`:
   - `delivered` (≥1 `responds_fully`)
   - `partly_delivered`
   - `mentioned_only`
   - `no_evidence_found`
   - `not_yet_due` (due date in the future)
4. **Discrepancies:** `log_status=Open/Overdue` but `delivered`; `log_status=Closed` but the closing item judged `unrelated`. Write these to a `discrepancy` view.
- Budget: judge at most 5 candidates per directive, use the cheapest capable model, and cap the run with `--max-cost` in config.

**Accept when:** using the log's `closed_by_item` as ground truth (closed directives only), **Link Recall@5** and **Link Precision** (judge says `responds_*` and it's in `closed_by`) are reported for: cite-edges only / retrieval only / both / both + rerank. A hand-checked sample of 30 discrepancies is labelled true/false, and the precision is in the README.

### Phase 4: Reranker (1–2 days)
- `rerank/cross_encoder.py`: a small MS MARCO-class cross-encoder → ONNX + INT8, batch 20 → top 5. Compare against `none` and an LLM listwise rerank on quality, p95 latency, and $.

**Accept when:** the reranker comparison table exists (Recall@5, nDCG@5, MRR, ms, $) for both question retrieval and link candidates.

### Phase 5: Guardrail + router (2–3 days)
- **Guardrail:** rules (length, injection strings, PII masking) → LLM classifier with strict labels: `in_scope | off_topic | political_opinion | injection | pii`.
  - `political_opinion` → answer neutrally with records only ("Here's what Council directed and the recorded votes"), or decline to rank or recommend.
  - Red-team set includes benign questions that **must pass** ("how did my councillor vote on X?" is factual, so allowed).
- **Router:** one LLM call → Pydantic `{intent, entities, rewrites[], needs_clarification}`, then deterministic normalization (item IDs, division synonyms, relative dates → absolute using today's date).
  - `vote_lookup` and `directive_list` (e.g., "open requests for Parks since 2024") go to **SQL**, not vectors.
  - If the topic is too vague → a clarifying question.

**Accept when:** router intent accuracy + entity F1 on 80 items is reported; guardrail precision/recall on 60 red-team items is reported; and the benign false-block rate is under 3%.

### Phase 6: Cited structured output + verifier (2–3 days)
```json
{
  "answer_type": "directive_status | directive_list | decision | item_history | votes | clarify | cannot_answer",
  "summary": "Plain-language answer, 2–4 sentences, neutral.",
  "directives": [{
    "directive_id": "…", "item_id": "2026.MM40.30", "meeting_date": "2026-04-22",
    "text_quote": "…directed the City Manager to report to City Council…", "assigned_to": ["City Manager"],
    "due": "2026-Q4", "log_status": "Open",
    "reportback_status": "no_evidence_found",
    "evidence": [{"item_id": "…", "doc_url": "…", "verdict": "responds_partly", "quote": "…"}],
    "discrepancy": null
  }],
  "votes": [{"item_id": "…", "motion": "…", "result": "Carried", "yes": 18, "no": 6, "url": "…"}],
  "caveats": ["Not all votes are recorded electronically.", "Status reflects documents published as of <date>."],
  "citations": [{"id": "c:123", "url": "…", "quote": "…"}]
}
```
- **Verifier:** every `item_id`, date, and vote count must match a DB row; every quote must be a substring of its source chunk; status words must come from the deterministic status function, never free text. Retry once with the errors; otherwise `cannot_answer`.
- **Neutral-language check:** a small wordlist + LLM check that the summary has no evaluative adjectives about people ("failed", "ignored", "lazy"). Report facts only, e.g. "No later report was found that responds to this directive."

**Accept when:** 30 scripted end-to-end cases pass (delivered, partly delivered, overdue with no evidence, not yet due, a discrepancy, vote lookup, vague question → clarify, opinion question → neutral answer, injection).

### Phase 7: Evals as a CI gate (2–3 days) ⭐
Suites:
- **Linking (auto-labelled from the Clerk's log):** Link Recall@5, Link Precision, status agreement with the log (where the log is closed).
- **QA (hand-written, 120 items):** Recall@20 / Recall@5 / nDCG@5, answer correctness on IDs/dates/status (exact match), citation precision, faithfulness via LLM-as-judge (hand-check 25 and report agreement), correct clarify/cannot_answer.
- **Router / guardrail:** as in Phase 5.
- **System:** p50/p95 per stage, $/query.

CI: on each PR, run retrieval + rerank + router (cached) + guardrail rules + linking on a fixed 200-directive sample with cached judgments. Run full generation/judge suites nightly or on manual trigger. **Fail** if Link Recall@5 or QA Recall@5 drops more than 2 points, exact match drops more than 2 points, or benign false-block rises above 3%.

**Accept when:** a demo PR that removes the date filter or the cite-edge candidates gets blocked.

### Phase 8: Observability (1–2 days)
OpenTelemetry → App Insights. One trace per question with spans per stage; attributes for model, tokens, $, filters applied, top rerank score, verifier result, and `answer_type`. Offline linking runs emit a **cost report** (pairs judged, cache hit rate, $). An `/admin/metrics` page or workbook shows p95 per stage, $/query, decline rate, and thumbs-down rate. Semantic cache keyed on normalized entities.

### Phase 9: Web UI (3–4 days)
- **Ask** page: question box with streaming stage indicator; answer cards showing the directive quote → the status chip (*Clerk's log* vs *ReportBack evidence*, side by side) → an evidence timeline (directive date → due date → linked reports), every element linked to TMMIS.
- **Browse** page: filters (division, committee, ward, status, year) over the directive table; an "Overdue with no evidence" view; a **Discrepancies** view.
- **"Why this answer"** drawer: router output, filters, top 20 → top 5 with scores.
- Footer: neutrality statement, data currency date, Open Government Licence – Toronto attribution, "not an official City service."

### Phase 10: Deploy on Azure (2 days)
Bicep: Container Apps env; `api` (with the ONNX reranker), `web`; Jobs: `ingest-daily` (log + votes + new items) and `link-weekly` (link new directives/reports, with a cost cap); Postgres Flexible B1MS (`VECTOR,PG_TRGM`); Log Analytics + App Insights; managed identity; Budget alerts. `azd up`; GitHub OIDC; ghcr.io; `minReplicas: 0`; per-IP rate limit.

**Accept when:** the public URL works and 7-day spend is under $5.

### Stretch
- **Watch a topic or item:** email when a new report cites a directive you follow.
- **MCP server:** `find_directives`, `get_followthrough(item_id)`, `get_votes(item_id)`.
- **Second city:** Vancouver publishes a quarterly motion-status report. Use it to test whether the linker generalizes.
- **Blog post with findings:** e.g., median days from directive to report by division, the share of directives with no evidence after their due date, and how often the log disagrees with the documents. Facts only, with methodology and limitations.

---

## 6. MVP cut line
Phases 0–3, 5–7, plus a minimal Ask page and Phase 10, on directives from the **2022–2026 term only**. That covers every stage of your pipeline plus the unique linker with real ground truth. About 8 weeks at 10 h/week.

## 7. README must-haves
1. A one-line pitch + a GIF showing a question → directive → evidence timeline → "Clerk's log says Open; we found a 2025 report that responds."
2. **Prior art & gap** section (Hamlet, citymeetings.nyc, CivicSearch, Civicly, Toronto vote trackers, internal city trackers).
3. Pipeline diagram.
4. **Eval tables:** linking ablation (cite-edges / retrieval / both / +rerank) against the Clerk's ground truth; QA retrieval; reranker comparison; exact match; guardrail and router.
5. Discrepancy findings with hand-checked precision.
6. Neutrality and privacy design; limitations (log coverage, unrecorded votes, PDF parsing, judge errors).
7. Monthly cost on Azure for Students.

## 8. Resume bullets (fill in real numbers)
- Built **ReportBack TO**, a RAG system that links N Toronto City Council report-back directives to the staff reports that answered them. Pipeline: **input guardrail → LLM query router → hybrid BM25+pgvector search with temporal/division metadata filters → ONNX cross-encoder reranker → citation-verified structured output.**
- Used the City Clerk's own closure records as **ground-truth labels**: Link Recall@5 X% (vs Y% retrieval-only), and surfaced Z hand-verified cases where the public log and the documents disagree.
- Shipped **evals as a CI gate**, **OpenTelemetry per-stage tracing** (latency, tokens, $/query), and Bicep/azd deployment on **Azure Container Apps + Postgres** for under $5/month. Demoed to Toronto's civic-tech/open-data community.

## 9. Interview talking points
- Why "linking across time" is harder than question answering, and which signal mattered most (cite edges vs. retrieval vs. rerank, with numbers).
- Using operational data (the Clerk's log) as labels, and its limits (noisy, incomplete, statuses change across snapshots).
- How date and division filters prevent false links to earlier, similarly worded reports.
- Designing for political neutrality and privacy in a civic tool.
- Cost control for offline LLM judging (prefilters, caching, caps).
