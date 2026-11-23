-- 001_init.sql: Phase 1 (ingestion) schema.
-- Later phases add their own numbered migrations (chunk, followthrough, query_trace, ...).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- One row per downloaded Report Request Log file (the file itself lives in data/snapshots/).
CREATE TABLE log_snapshot (
  snapshot_id   text PRIMARY KEY,            -- sha256 of the XLSX bytes
  term          text NOT NULL,               -- '2022-2026'
  resource_id   text NOT NULL,
  resource_name text NOT NULL,
  last_modified text,                        -- as published by CKAN
  as_of         date NOT NULL,               -- date the Clerk published this version
  path          text NOT NULL,               -- repo-relative path of the archived file
  n_rows        int NOT NULL,
  ingested_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per directive (report request). Several log rows can describe the same directive
-- (same item + text, e.g. one row per closing item, or carried into the next term's log);
-- they are kept verbatim in log_entry and aggregated here.
CREATE TABLE directive (
  directive_id    text PRIMARY KEY,          -- stable hash of (item number, normalized text)
  item_id         text,                      -- normalized, e.g. '2023.EX3.1'; NULL if unparseable
  item_number_raw text NOT NULL,
  term            text NOT NULL,             -- term of the most recent log listing it
  directive_no    text,                      -- leading number in the text ('16')
  requesting_body text,
  meeting_date    date,
  agenda_title    text,
  directive_text  text NOT NULL,
  report_to       text,
  timing_text     text,
  due_date        date,
  due_rule        text NOT NULL,             -- how due_date was derived (or why it is NULL)
  clerk_order_date date,                     -- the Clerk's own "Date for Order"
  assigned_to     text[] NOT NULL DEFAULT '{}',
  divisions       text[] NOT NULL DEFAULT '{}',
  log_status      text,                      -- verbatim from the most recent log (never rewritten)
  log_status_norm text NOT NULL,             -- closed|ncf|open|due|ongoing|pending|mixed|unknown
  closed_by_item  text[] NOT NULL DEFAULT '{}',  -- ground truth: normalized item IDs
  closed_by_other text[] NOT NULL DEFAULT '{}',  -- non-item references (briefing notes, dates)
  first_seen      date NOT NULL,
  last_seen       date NOT NULL,
  emb             vector(1536),
  tsv tsvector GENERATED ALWAYS AS
      (to_tsvector('english', coalesce(agenda_title, '') || ' ' || directive_text)) STORED
);
CREATE INDEX directive_item_idx ON directive (item_id);
CREATE INDEX directive_tsv_idx ON directive USING gin (tsv);
CREATE INDEX directive_divisions_idx ON directive USING gin (divisions);
CREATE INDEX directive_status_due_idx ON directive (log_status_norm, due_date);

CREATE TABLE log_entry (
  snapshot_id     text NOT NULL REFERENCES log_snapshot ON DELETE CASCADE,
  row_no          int  NOT NULL,             -- 1-based data row in the workbook
  directive_id    text NOT NULL REFERENCES directive ON DELETE CASCADE,
  status_raw      text,
  assigned_to_raw text,
  timing_raw      text,
  closed_by_raw   text,
  PRIMARY KEY (snapshot_id, row_no)
);
CREATE INDEX log_entry_directive_idx ON log_entry (directive_id);

-- TMMIS agenda items.
CREATE TABLE item (
  item_id        text PRIMARY KEY,
  term           text,
  committee_code text,                       -- 'PH' from the item number
  committee      text,                       -- originating decision body
  meeting_date   date,                       -- originating body's meeting
  council_date   date,                       -- City Council consideration, if any
  title          text,
  wards          text[] NOT NULL DEFAULT '{}',
  decision_type  text,
  status         text,                       -- latest consideration's status
  origin         text,
  summary        text,
  decision_text  text,                       -- final decision (Council's if it considered the item)
  communications_count int NOT NULL DEFAULT 0,   -- public letters: counted, never stored
  speakers_count       int NOT NULL DEFAULT 0,   -- deputants: counted, never stored
  url            text NOT NULL,
  fetch_status   text NOT NULL,              -- ok | not_found | error
  fetched_at     timestamptz NOT NULL,
  content_hash   text
);
CREATE INDEX item_meeting_idx ON item (meeting_date);

CREATE TABLE item_consideration (
  item_id          text NOT NULL REFERENCES item ON DELETE CASCADE,
  seq              int  NOT NULL,            -- page order: 0 = most recent
  body             text,                     -- 'City Council', 'Planning and Housing Committee'
  meeting_date     date,
  meeting_date_text text,
  status           text,
  decision_type    text,
  decision_heading text,
  decision_text    text,
  PRIMARY KEY (item_id, seq)
);

-- Background files listed on item pages (staff reports, letters, attachments).
CREATE TABLE doc (
  doc_id         text PRIMARY KEY,           -- 'backgroundfile-234496'
  item_id        text NOT NULL REFERENCES item ON DELETE CASCADE,
  kind           text NOT NULL,              -- report | letter | attachment | notice | presentation | other
  description    text NOT NULL,              -- listing text on the item page
  title          text,
  report_date    date,
  from_officials text[] NOT NULL DEFAULT '{}',
  divisions      text[] NOT NULL DEFAULT '{}',
  url            text NOT NULL,
  fetch_status   text,                       -- NULL = not attempted | ok | skipped | error | not_found
  file_hash      text,
  pages          int
);
CREATE INDEX doc_item_idx ON doc (item_id);

CREATE TABLE doc_section (
  doc_id     text NOT NULL REFERENCES doc ON DELETE CASCADE,
  seq        int  NOT NULL,
  section    text NOT NULL,                  -- 'SUMMARY', 'DECISION HISTORY', ... or 'PREAMBLE'
  page_start int  NOT NULL,
  page_end   int  NOT NULL,
  text       text NOT NULL,                  -- emails/phone numbers already stripped
  PRIMARY KEY (doc_id, seq)
);

-- Citation graph: a later item/doc/directive mentions an earlier item ID.
CREATE TABLE cites (
  from_id    text NOT NULL,
  to_item_id text NOT NULL,
  from_type  text NOT NULL,                  -- item | doc | directive
  method     text NOT NULL,                  -- full | short_inferred
  raw        text NOT NULL,                  -- the matched string
  context    text NOT NULL,                  -- surrounding text
  PRIMARY KEY (from_id, to_item_id)
);
CREATE INDEX cites_to_idx ON cites (to_item_id);

-- Members of Toronto City Council - Voting Record.
CREATE TABLE vote (
  vote_id          text PRIMARY KEY,         -- hash of the source row
  term             text NOT NULL,
  item_id          text,
  agenda_item_title text,
  committee        text,
  motion_type      text,
  vote_description text,
  member           text NOT NULL,
  vote             text NOT NULL,            -- Yes | No | Absent
  result           text,
  yes_count        int,
  no_count         int,
  meeting_date     date,
  vote_datetime_raw text
);
CREATE INDEX vote_item_idx ON vote (item_id);
