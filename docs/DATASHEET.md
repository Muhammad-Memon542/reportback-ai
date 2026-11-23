# Datasheet

Sources, terms, structure and known quirks. Facts here were observed directly from the live sources (dates noted). Update whenever a source changes.

## Licence and attribution

All sources are published by the City of Toronto under the **Open Government Licence – Toronto** (CKAN `license_title: open-government-licence-toronto`).
Required attribution, shown in the UI footer and README:

> Contains information licensed under the Open Government Licence – Toronto.

ReportBack is **not an official City service**. The Clerk's log itself says it "is intended for reference and convenience purposes. Users are encouraged to consult the official minutes of meetings for Council and committee decision information."

## Crawl policy (enforced in `reportback/ingest/http.py`)

- Allowlisted hosts only, HTTPS only, checked on every redirect hop: `ckan0.cf.opendata.inter.prod-toronto.ca`, `secure.toronto.ca`, `www.toronto.ca`.
- ≤1 request/second, descriptive User-Agent with no personal data.
- Raw files cached in `data/raw/` (gitignored), keyed by URL plus the source's `last_modified`.

## 1. Report Request Log – City Council and its Committees

*Observed 2026-11-23 via CKAN `package_search`.*

| Field | Value |
|---|---|
| CKAN package | `report-request-log-city-council-and-its-committees` (id `de0bd325-626d-4808-aecf-69e1cdd169fc`) |
| Owner | City Clerk's Office |
| Refresh rate | "As available"; `last_refreshed` 2026-11-13 13:52:59 |
| Datastore | **No.** XLSX + PDF files only |

Resources:

| Resource | Format | last_modified | Data rows | Meeting dates |
|---|---|---|---|---|
| Report Request Log 2022-2026 | XLSX (688 KB) | 2026-11-13T13:52:59 | 1,979 | 2022-11-24 … 2026-07-29 |
| Report Request Log 2018-2022 | XLSX (677 KB) | 2024-01-09T17:25:07 | 2,455 | 2018-12-04 … 2022-11-08 |
| (PDF renderings of both) | PDF | | | not used |

Workbook layout: sheet `Main Data` (data), `Glossary` (definitions), `_Lookup data do not edit` (status/division/committee lookups).

Columns (normalized; both terms identical):
`Status | Req. Body | Meeting Date | Item Number | Agenda title | Directive | Rpt to: | Report Back Timing | Assigned to | Closed by | Date for Order`

Glossary definitions (verbatim, abridged):
- **Status**: whether the request is open, closed or due.
- **Requesting Body**: the decision body where the request was finalized.
- **Meeting Date**: the meeting where the request was made.
- **Item Number**: hyperlink to the agenda item containing the request.
- **Report to**: the decision body the report goes to (committee code).
- **Report Back Timing**: when the report is expected.
- **Assigned to**: the City official or agency the report was requested from.
- **Closed by**: hyperlink to the agenda item that fulfils the request.
- **Date for Order**: enables sorting by due date ("specific or estimated dates").

Status vocabulary (Glossary + lookup sheet): `Open`, `Closed`, `Due`, `Ongoing`, `Pending`, `NCF`.
- *Closed*: "staff determine that the report request has been addressed by a subsequent agenda item."
- *Due*: "report back timing has been reached and no report has been submitted."
- *NCF (Not Carried Forward)*: "Outstanding report requests are not carried forward to the next term report log."

Observed status counts:

| Term | NCF | Closed (any case) | Other | Rows with an item in "Closed by" |
|---|---|---|---|---|
| 2022–2026 | 1,269 | 710 (`closed` 561, `Closed` 114, `CLOSED` 35) | 0 | 733 |
| 2018–2022 | 1,276 | 1,178 | 1 blank | 1,195 |

**There are currently no Open/Due/Ongoing rows in either file.** Both terms have been closed out: anything outstanding at term end became NCF. Ground truth for linking ≈ 1,900 rows with a `Closed by` item.

Quirks to handle in Phase 1:
- Header cells contain newlines; 19 trailing empty columns in the 2022–2026 file.
- `Status` casing varies (`closed`/`Closed`/`CLOSED`).
- `Req. Body` has trailing spaces (`"CC "`).
- `Closed by` holds item IDs, sometimes several separated by newlines (`"2023.PH2.1\n2023.PH5.1"`), and occasionally a **date** instead of an item. More rows have a `Closed by` value than are marked Closed, so some NCF rows carry one.
- `Report Back Timing` mixes Excel dates (703 rows in 2022–2026), `Qn YYYY`, `In YYYY`, `Not indicated` (three casings), blanks, and free text ("prior to consideration of …").
- `Meeting Date` and `Date for Order` are Excel datetimes.
- Historical snapshots are **not** available from CKAN (each resource is replaced in place). Status-change history starts with the first snapshot we save (DECISIONS Q1).

## 2. Members of Toronto City Council – Voting Record

*Observed 2026-11-23.*

| Field | Value |
|---|---|
| CKAN package | `members-of-toronto-city-council-voting-record` (id `7f5232d6-0d2a-4f95-864a-417cbf341cc4`) |
| Owner | City Clerk's Office |
| Refresh | "As available"; `last_refreshed` 2026-11-23 20:00 |
| Datastore | Yes, one datastore resource per term, plus CSV/JSON/XML file copies (12–188 MB) |

Datastore row counts: 2022–2026 **46,815** (latest row 2026-08-05) · 2018–2022 61,074 · 2014–2018 212,095 · 2010–2014 358,814 · 2006–2010 64,899.
2022–2026 datastore resource id: `55ead013-2331-4686-9895-9e8145b94189`.

Columns: `Term | First Name | Last Name | Committee | Date/Time | Agenda Item # | Agenda Item Title | Motion Type | Vote | Result | Vote Description`

From the dataset readme: Vote ∈ {`Yes`, `No`, `Absent`}; Result = outcome plus Yes/No totals (e.g. `Carried, 25-1`); Vote Description = the vote type required for the motion to carry.

Quirks:
- `Date/Time` is text and mixes formats: `2022-11-23 15:17 PM` (24-hour clock *with* an AM/PM suffix) and a bare date `2026-08-05`.
- Datastore resource `last_modified` is stale (2023-01-19) even though the data is current (LESSONS L2).
- **Not every vote is recorded electronically.** The UI must say so wherever votes are shown.
- Member names are those of elected officials acting in their public role. No other personal data.

## 3. TMMIS agenda item pages (Phase 1)

`https://secure.toronto.ca/council/agenda-item.do?item=YYYY.XXN.N`. Structure has not been inspected yet; we'll inspect real pages before writing any selector.

## 4. Background files (Phase 1)

`https://www.toronto.ca/legdocs/mmis/YYYY/xx/bgrd/backgroundfile-NNNNN.pdf`. We download only files linked from crawled item pages.

## Excluded

**Communications (letters from the public)**: they contain private individuals' names and addresses. **Not indexed.** We store only counts per item.
