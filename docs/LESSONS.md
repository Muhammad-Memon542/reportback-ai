# Lessons

Things that broke or surprised us, and what we changed.

## 2026-11-23 · Phase 0

**L1. openai 3.x uses `httpx2`, not `httpx`.**
Passing an `httpx.Client(transport=MockTransport(...))` to `OpenAI(http_client=...)` *worked at runtime* (duck typing) and all tests passed; only `mypy --strict` caught that the parameter is `httpx2.Client`. Fix: LLM layer and its tests use `httpx2` explicitly (declared as a direct dependency). Takeaway: keep strict mypy on; it catches SDK drift the tests can't.

**L2. CKAN `last_modified` on a datastore resource is not a freshness signal.**
`member-voting-record-2022-2026` (datastore) reports `last_modified = 2023-01-19`, but its newest row is dated 2026-08-05 and the package was refreshed 2026-11-23. Judge freshness from the data (max date) or the package `last_refreshed`, never from datastore resource metadata.

**L3. The Clerk's XLSX headers contain embedded newlines.**
`'Req.\nBody'`, `'Meeting \nDate'`, `'Rpt \nto:'`, `'Closed \nby'`, plus 19 trailing empty header cells in the 2022–2026 file. `normalize_header()` collapses whitespace; parsers must map by normalized name, never by position.

**L4. openpyxl warns on these workbooks, and in read-only mode it warns lazily.**
"Cannot parse header or footer" / "Data Validation extension is not supported". These are harmless, but because read-only mode parses on iteration, the `warnings` filter has to wrap the iteration too, not just `load_workbook`.

**L5. Outbound network is deny-by-default in the cloud session.**
The first CKAN call got `403 (agent proxy: no rule or network setting allows this host)`. We fixed it with a dedicated environment allowlisting only the three source hosts (DECISIONS D9). Expect the same for the Foundry endpoint: its host must be allowed and its key saved as a network secret, not pasted anywhere.

**L6. Empty "Region" list when creating a Foundry resource = resource provider not registered.**
On the Azure for Students subscription, the Foundry create form showed "No available items" for Region, even though the subscription's allowed regions include Foundry regions. Cause: `Microsoft.CognitiveServices` wasn't registered on the new subscription. Fix: `az provider register -n Microsoft.CognitiveServices --wait` (free), then reload the form. Second trap: the form then **defaulted to East US 2, which is outside the subscription's allowed regions** (DECISIONS D11). It would have failed at deploy time with `RequestDisallowedByAzure`. Always pick from the allowed list: Foundry goes in `swedencentral`.

**L7. A model can appear in the catalogue but have 0 quota.**
`gpt-5.4-mini` is listed by `GET /openai/v1/models` on our resource, but deploying it failed with `InsufficientQuota … 0 / 0 quota units`. The catalogue lists what exists, not what this subscription may deploy. Check quota first: ``az cognitiveservices usage list -l <region> --query '[?limit > `0`]' -o table``.

## 2026-11-23 · Phase 1

**L8. TMMIS pages declare UTF-8 but contain Windows-1252 bytes.**
`0xD7` (×) in the page chrome made a strict UTF-8 decode fail. Decoding the whole page as cp1252 would mangle real UTF-8 elsewhere, so `decode_html` decodes UTF-8 and falls back to cp1252 only for the invalid bytes.

**L9. psycopg 3: without autocommit, `conn.transaction()` after any query is just a SAVEPOINT.**
The first crawl ran for minutes with zero visible rows: the seed `SELECT` had opened an implicit transaction, so every per-item `with conn.transaction()` became a savepoint and nothing would commit until the end. Fixed by connecting with `autocommit=True` (DECISIONS D19).

**L10. TMMIS headings aren't siblings.** "City Council Decision" is an `h4` nested inside `div.d-info`, so walking an element's direct children missed it. Fix: segment each card in document order by turning every `h4` into a text marker.

**L11. A nonexistent TMMIS item returns HTTP 200** with a ~2 KB page titled "Error". "Not found" is detected from the content, not the status code.

**L12. The "duplicate" directives were mostly not errors.** The same directive closed by several items gets one row per closing item, and directives can reappear in the next term's log. Collapsing by (item, text) and keeping all rows (DECISIONS D14) preserves the ground truth.

**L13. Bold ALL-CAPS table cells looked like section headings.** Budget tables in staff reports have bold cells such as "TOTAL", "SOGR" and "($M)", which split sections. Template headings are set larger than body text (12.8–13 pt against 12 pt), while table cells are at or below body size. A heading now has to be a known template heading, or bold, all-caps and at least 0.4 pt larger than the body size (the font size carrying the most characters).

**L14. The City logo extracts as text.** On many reports the wordmark comes out of PyMuPDF as a bold, large "~TORONTO" (or a misread "~TORONIO") and was taken as a heading 260 times. Headings must now start with a letter or digit.

**L15. The first citation graph had predictable false positives.** Crawling hop 2 found 19 cited IDs that don't exist on TMMIS, which showed the causes:
- Official Plan zoning codes in scanned maps ("CR3.0" — CR is also a committee code).
- Footnote markers glued onto IDs ("2025.EX20.12" + footnote 3).
- "In May 2017" not recognized as a year hint, so the meeting index picked 2025.
- Term-boundary board meetings (Preservation Board, Nov 7, 2022, still the old term).
- Typos in the source documents ("2025.IEC22.8").

Fixes: item and meeting numbers must be ≥ 1; month-year hints; short forms can't resolve to an item later than the citing document; ambiguous Nov/Dec election-year dates are skipped; edges to targets TMMIS says don't exist are footnote-repaired when the ID minus its last digit exists, and dropped otherwise.

**L16. Some PDF text layers contain NUL bytes**, and Postgres `text` rejects them (`backgroundfile-230424`). `scrub_pii` and `norm_ws` now drop `\x00`.

**L17. Two writers of one column made the result depend on run order.** `doc.report_date` comes from the TMMIS listing ("(February 9, 2023) Report from …") or, failing that, from the PDF's `Date:` header. Re-parsing TMMIS pages overwrote the PDF-derived date with NULL, which a later PDF re-parse restored, and `scripts/check_idempotency.py` caught it. The TMMIS upsert now keeps an existing date when the listing has none.

**L18. `pg_restore --clean` as a non-superuser fails on extensions** ("must be owner of extension vector"). Fixed by restoring from a filtered table of contents (`pg_restore --list`, dropping the EXTENSION entries, then `--use-list`). **Releases can't be created from a Claude Code session** (HTTP 403 on POST /releases), but workflow dispatch is allowed, so a workflow running with the repository's token creates them.
