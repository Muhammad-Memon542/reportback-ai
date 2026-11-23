# ReportBack TO

**Council asked for it. Did City Hall ever report back?**

ReportBack links Toronto City Council's report-back directives (from the City Clerk's *Report Request Log*) to the later staff reports that answered them. You can ask it questions in plain language and get cited answers. Every answer shows the **Clerk's log status** and **ReportBack's evidence-based status** side by side.

> Status: **Phase 0 (skeleton + smoke tests)**. See [`docs/PLAN.md`](docs/PLAN.md).

## Pipeline

```
question → input guardrail → query router/rewriter → hybrid search + metadata filter (top 20)
         → reranker (top 5) → link expansion + cited structured output + verifier
         → observability → evals
```

## Quickstart

```bash
uv sync
cp .env.example .env   # fill in LLM_* for the LLM smoke test
uv run pre-commit install
uv run pytest -q && uv run ruff check . && uv run mypy
uv run python scripts/smoke_ckan.py   # finds the Clerk's datasets on Toronto Open Data
uv run python scripts/smoke_llm.py    # chat + structured JSON + embeddings
uv run reportback serve               # GET /healthz
```

## Prior art & gap

Meeting search and summarization tools exist (Hamlet, citymeetings.nyc, CivicSearch, Civicly.ai), as do Toronto vote trackers (Climate Voting Records, TrackYourVoteTO, Councillor Voting Behaviour) and internal city motion trackers (e.g. Vancouver, York UK). We found no public tool that links Council's report-back directives to the staff reports that answered them and lets you ask questions about it.

## Neutrality and privacy

ReportBack describes public records. It never rates councillors, takes political positions, or gives voting advice. Public communications (letters from residents) are not indexed, and emails and phone numbers are stripped from indexed text.

Data sources and their quirks: [`docs/DATASHEET.md`](docs/DATASHEET.md).

---

Contains information licensed under the Open Government Licence – Toronto. ReportBack is not an official City of Toronto service.
