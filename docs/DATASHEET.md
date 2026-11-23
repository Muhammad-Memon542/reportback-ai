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
- Historical snapshots are **not** available from CKAN (each resource is replaced in place). Status-change history starts with the first snapshot we save (DECISIONS D10).
- **"Date for Order" is not a due date:** 1,191 rows carry the term-end placeholders 2022-11-14 or 2026-11-14 (DECISIONS D15).
- **Repeated directives:** 36 groups of rows share an item and directive text. Usually it's one row per closing item, or a directive carried into the next term's log (DECISIONS D14).
- **2018–2022 format differences:** `Assigned to` holds division names (several separated by " - "), while 2022–2026 holds official titles ("General Manager, Transportation Services"). The Clerk's division lookup sheet writes "&" where titles write "and".
- **Malformed item numbers** seen: `2023MM11.16`, `2021-EX28.2`, `IE26.3` (no year), `20263MM42.22.`, `2019.M3.20`, and one blank. **Malformed "Closed by" values** seen: `2026.AU.13.3`, `2025 NY21.15`, `EC27.7`, briefing-note references (`BN14-Jan 26`), part references (`(Part 10.d.)`).

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
- **CSV file** (`member-voting-record-2022-2026.csv`, 12 MB, 46,815 rows, no exact duplicates) has an extra `_id` column. `Vote` also takes `Absent(Interest Declared)`, and `Result` also takes `Lost (tie), N-N`. 2,903 rows have a date with no time.
- Member names are those of elected officials acting in their public role. No other personal data.

## 3. TMMIS agenda item pages

*Inspected 2026-11-23.* URL: `https://secure.toronto.ca/council/agenda-item.do?item=YYYY.XXN.N`. Server-rendered HTML; no API used.

- **Encoding:** pages declare `charset=utf-8` but contain stray Windows-1252 bytes (LESSONS L8).
- **Nonexistent items:** HTTP 200 with a small page titled "Error" (LESSONS L11).
- **Layout:** `section#main` holds `h1` "Item - 2023.PH2.1", a Tracking Status paragraph, and `#agenda-item-accordion` with **one card per consideration, most recent first** (e.g. "City Council consideration on March 29, 30 and 31, 2023", then "Planning and Housing Committee consideration on February 28, 2023").
- **Each card** has `h3.heading` ("PH2.1 - Title"), a `.d-info` list (Decision Type, Status, Ward/Wards), and `h4`-titled sections. Seen: *City Council Decision*, *Community Council Decision*, *Committee Recommendations*, *Decision Advice and Other Information*, *Origin*, *Summary*, *Background Information* (also suffixed "(Committee)"/"(City Council)" on Council cards), *Communications*, *Speakers*, *Motions*, *Declared Interests*, and one *Vote(...)* block per recorded vote. The `h4`s are not reliably siblings (LESSONS L10).
- **Background Information** lists `span.rpt-text` descriptions, each followed by an `a.rpt-text` link to `https://www.toronto.ca/legdocs/mmis/YYYY/xx/bgrd/backgroundfile-NNNNN.pdf`. Description pattern: `(Month D, YYYY) Report from <officials> on <title>`; councillor letters use `Letter from Councillor X - <title>`; attachments are "Attachment N: …" with no date.
- **Item-ID year labels** follow the council year, not the calendar: a new term's first meetings in Nov/Dec of an election year are labelled with the next year (2022-11-24 → `2023.CC1`). Meeting numbers restart each term.
- **Privacy:** *Communications* name members of the public and link to `communicationfile-*.pdf`; *Speakers* name deputants. **Neither is stored**: only `communications_count` and `speakers_count`.

## 4. Background files (staff reports)

*Inspected 2026-11-23 on `backgroundfile-234496.pdf` (6 pages; the test fixture) and the crawl.*
- **Producer:** Acrobat PDFMaker for Word. Metadata `title` = report title; `author` = "Report from …".
- **Template:** a preamble (`REPORT FOR ACTION` / `REPORT FOR INFORMATION`, the title, then `Date:` / `To:` / `From:` / `Wards:`), followed by bold ALL-CAPS 13 pt headings. Typical order: SUMMARY, RECOMMENDATIONS, FINANCIAL IMPACT, EQUITY IMPACT STATEMENT, DECISION HISTORY, COMMENTS, CONTACT, SIGNATURE, ATTACHMENTS. Mixed-case bold subheadings sit inside sections.
- **Every page** repeats a running header (short title) and "Page N of M"; both are dropped.
- **DECISION HISTORY** paragraphs name earlier decisions ("On December 15, 2022, City Council adopted …") and often include a TMMIS URL containing the full item ID, sometimes the short form "Item EX31.11". This is the main citation-graph signal.
- **CONTACT** lists staff names, phone numbers and emails. Phones and emails are scrubbed before storage; names of officials in their public role are kept.

## Excluded

**Communications (letters from the public)**: they contain private individuals' names and addresses. **Not indexed.** We store only counts per item.
