# Coverage report

Generated 2026-11-26 20:50 UTC by `uv run reportback coverage` from the local database. Re-running every ingest command produces the same counts (idempotent loads; see tests).

Contains information licensed under the Open Government Licence – Toronto.

## 1. Report Request Log

| term | snapshot as of | file rows | archived file |
|---|---|---|---|
| 2018-2022 | 2024-01-09 | 2,455 | 2018-2022/2024-01-09_f72a1677b79c.xlsx |
| 2022-2026 | 2026-11-13 | 1,979 | 2022-2026/2026-11-13_ab1de968f2c1.xlsx |
| 2022-2026 | 2026-11-25 | 1,979 | 2022-2026/2026-11-25_f902b4b4d655.xlsx |

6,413 log rows → **4,395 directives** (rows describing the same item + text are merged; every row is kept in `log_entry`).

| term | directives | closed | ncf | mixed | unknown | with closing item | with ≥1 division | with due date |
|---|---|---|---|---|---|---|---|---|
| 2018-2022 | 2,444 | 1,173 | 1,270 | 0 | 1 | 1,135 | 2,435 | 1,635 |
| 2022-2026 | 1,951 | 695 | 1,243 | 13 | 0 | 729 | 1,935 | 1,291 |

**How `due_date` was derived** (`due_rule`; NULL due dates are never treated as overdue):

| rule | directives |
|---|---|
| date | 1,589 |
| quarter | 1,177 |
| not_indicated | 926 |
| missing | 181 |
| budget_relative | 132 |
| year | 101 |
| recurring | 79 |
| conditional | 70 |
| unparsed | 47 |
| meeting_relative | 33 |
| month | 19 |
| date_text | 15 |
| approx | 11 |
| season | 7 |
| half | 5 |
| after_meeting | 2 |
| ambiguous_date | 1 |

Item numbers that could not be normalized: '', '2019.M3.20'.
Directives whose “Closed by” cites something other than an item (briefing note, date, part number): 58.

## 2. Voting record

| term | vote rows | items | first meeting | last meeting |
|---|---|---|---|---|
| 2022-2026 | 46,815 | 1,110 | 2022-11-23 | 2026-08-05 |

Not every Council vote is recorded electronically; a missing vote row is not evidence of anything.

## 3. TMMIS agenda items

| fetch status | items |
|---|---|
| ok | 2,911 |
| not_found | 32 |

2022–2026 seeds and how many were fetched:

| seed source | distinct IDs | fetched ok |
|---|---|---|
| directive items | 1,295 | 1,288 |
| closing items (ground truth) | 498 | 495 |
| voted items | 1,110 | 1,110 |

|  | count |
|---|---|
| considerations (committee/Council cards) | 4,151 |
| items with a decision text | 2,903 |
| public communications (counted, not stored) | 5,112 |
| speakers/deputants (counted, not stored) | 4,399 |

## 4. Background files

| kind | listed | downloaded ok | skipped | errors | not found | pages |
|---|---|---|---|---|---|---|
| attachment | 2,488 | 0 | 0 | 0 | 0 | 0 |
| report | 2,134 | 2,134 | 0 | 0 | 0 | 30,788 |
| budget | 701 | 0 | 0 | 0 | 0 | 0 |
| letter | 524 | 523 | 0 | 0 | 1 | 879 |
| motion | 482 | 0 | 0 | 0 | 0 | 0 |
| notice | 383 | 0 | 0 | 0 | 0 | 0 |
| presentation | 345 | 0 | 0 | 0 | 0 | 0 |
| other | 271 | 0 | 0 | 0 | 0 | 0 |
| briefing_note | 157 | 157 | 0 | 0 | 0 | 1,471 |
| financial_impact | 47 | 0 | 0 | 0 | 0 | 0 |
| answer | 17 | 17 | 0 | 0 | 0 | 61 |
| mayoral_direction | 4 | 0 | 0 | 0 | 0 | 0 |

23,657 sections extracted. Most common section headings:

| section | count |
|---|---|
| CONTACT | 2,318 |
| PREAMBLE | 2,210 |
| RECOMMENDATIONS | 2,166 |
| SUMMARY | 2,137 |
| SIGNATURE | 2,097 |
| FINANCIAL IMPACT | 2,076 |
| COMMENTS | 2,051 |
| DECISION HISTORY | 1,985 |
| ATTACHMENTS | 1,656 |
| BODY | 473 |
| REASON FOR CONFIDENTIAL INFORMATION | 291 |
| CONFIDENTIAL ATTACHMENT | 282 |
| THE APPLICATION | 225 |
| CONCLUSION | 191 |
| APPLICATION BACKGROUND | 170 |

Staff reports with a DECISION HISTORY section: 1,985 of 2,134 downloaded reports (93.0%).

## 5. Citation graph

| from | method | edges |
|---|---|---|
| directive | full | 77 |
| directive | short_inferred | 12 |
| doc | full | 4,464 |
| doc | full_footnote_repaired | 6 |
| doc | short_inferred | 3,046 |
| doc | short_inferred_footnote_repaired | 5 |
| item | full | 213 |
| item | short_inferred | 175 |

Distinct cited items: 3,123. Precision on 100 hand-checked matches: see section 7.

## 6. Preview: does the closing item cite the directive's item?

For closed 2022–2026 directives, each (directive item → closing item) pair from the Clerk's log is a ground-truth link. Phase 3 measures linking properly; this is only the raw cite-edge signal.

| ground-truth pairs | closing item fetched | closing item (or its reports) cites the directive's item |
|---|---|---|
| 747 | 737 | 698 (94.7%) |

## 7. Phase 1 acceptance checks

**Citation-graph precision** on a hand-checked, stratified random sample (`evals/citegraph_precision.csv`; labelling rule in `evals/citegraph_sample.py`):

| matches | correct | precision |
|---|---|---|
| full | 50/50 | 100.0% |
| short_inferred | 49/50 | 98.0% |
| all | 99/100 | 99.0% |

The one error (a serial-comma date gave no year hint) was fixed afterwards, with a regression test.

**Idempotency** (re-running every ingest step):

```
# scripts/check_idempotency.py, 2026-11-25 (run before the result file existed; output copied from the run log)
log_snapshot         rows       2 ->       2  unchanged
directive            rows   4,395 ->   4,395  unchanged
log_entry            rows   4,434 ->   4,434  unchanged
vote                 rows  46,815 ->  46,815  unchanged
item                 rows   2,930 ->   2,930  unchanged
item_consideration   rows   4,134 ->   4,134  unchanged
doc                  rows   7,530 ->   7,530  unchanged
doc_section          rows  23,561 ->  23,561  unchanged
cites                rows   7,940 ->   7,940  unchanged
IDEMPOTENT
```
