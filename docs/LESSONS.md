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
