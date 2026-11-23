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
