-- 002_chunk.sql: Phase 2 retrieval index.

-- Retrieval units. Directives are one chunk each; item decisions/summaries and staff-report
-- sections are split into ~1,600-character passages that are exact substrings of their
-- source text (so quotes can be verified against `text`). `heading` (document title and
-- section) is indexed with a higher weight than the passage but is not part of `text`.
CREATE TABLE chunk (
  chunk_id       bigserial PRIMARY KEY,
  chunk_key      text NOT NULL UNIQUE,         -- stable: '<doc_type>:<source_id>:<part>'
  doc_type       text NOT NULL,                -- directive | decision | item_summary | staff_report | letter
  source_id      text NOT NULL,                -- directive_id / item_id / doc_id
  item_id        text,
  meeting_date   date,
  committee_code text,                         -- 'EX', from the item number
  committee      text,
  divisions      text[] NOT NULL DEFAULT '{}',
  wards          text[] NOT NULL DEFAULT '{}',
  term           text,
  status         text,                         -- directive chunks: directive.log_status_norm
  section        text,
  page           int,
  page_end       int,
  heading        text,
  text           text NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(heading, '')), 'A')
     || setweight(to_tsvector('english', text), 'B')) STORED,
  emb            vector(1536),
  embed_hash     text NOT NULL,                -- sha256(model | embedding input); key into embedding_cache
  url            text NOT NULL
);
CREATE INDEX chunk_tsv_idx ON chunk USING gin (tsv);
CREATE INDEX chunk_emb_idx ON chunk USING hnsw (emb vector_cosine_ops);
CREATE INDEX chunk_type_date_idx ON chunk (doc_type, meeting_date);
CREATE INDEX chunk_divisions_idx ON chunk USING gin (divisions);
CREATE INDEX chunk_item_idx ON chunk (item_id);
CREATE INDEX chunk_embed_hash_idx ON chunk (embed_hash);

-- Embeddings by content hash, so re-chunking or re-running never pays twice for the same text.
CREATE TABLE embedding_cache (
  embed_hash  text PRIMARY KEY,
  model       text NOT NULL,
  dim         int  NOT NULL,
  emb         vector(1536) NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
