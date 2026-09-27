-- Initial schema: matters, their chunks (text, metadata and embedding in the
-- same row), and each lawyer's recent chats, in Supabase Postgres.
--
-- Replaces three stores that had to be kept in step by hand: Chroma for
-- vectors, data/index/matters/<id>/chunks.json for BM25, and meta.json for
-- ownership. A document added to or removed from one but not the others left
-- the keyword and vector indexes disagreeing about what a matter contained.
--
-- Runs unchanged on Supabase, a local Postgres, and the CI container.

-- pgvector. Supabase keeps extensions in its own `extensions` schema, so use
-- it where it exists; a plain Postgres has no such schema and takes the
-- default. Every connection sets search_path to "public, extensions", so the
-- vector type and its operators resolve the same way either way.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_extension WHERE extname = 'vector') THEN
        IF EXISTS (SELECT FROM pg_namespace WHERE nspname = 'extensions') THEN
            CREATE EXTENSION vector SCHEMA extensions;
        ELSE
            CREATE EXTENSION vector;
        END IF;
    END IF;
END
$$;

CREATE TABLE matters (
    matter_id TEXT PRIMARY KEY,
    owner TEXT,  -- NULL for the shared demo matter
    -- Bumped in the same transaction as every chunk write or delete, so a
    -- cached index can tell it is stale by comparing one integer, from any
    -- retriever instance and any app process.
    content_version BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chunks (
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    source_document TEXT NOT NULL,
    doc_type TEXT,
    jurisdiction TEXT,
    locator TEXT,
    page INTEGER,
    heading TEXT,
    text TEXT NOT NULL,
    language TEXT,
    translated_text TEXT,
    translated_language TEXT,
    translation_flagged BOOLEAN NOT NULL DEFAULT false,
    translation_flag_reason TEXT,
    -- bge-small-en-v1.5. NOT NULL on purpose: a chunk that BM25 can find but
    -- vector search cannot is exactly the drift this schema exists to end.
    -- The dimension pins the model; index_meta records which one.
    embedding vector(384) NOT NULL,
    -- Insertion order. Postgres has no implicit row order, and BM25 breaks
    -- ties by document order, so without this the same corpus could rank
    -- differently from one load to the next.
    inserted_seq BIGINT GENERATED ALWAYS AS IDENTITY,
    -- Unique within a matter, not globally. The chunker happens to prefix ids
    -- with the matter name, but isolation should not rest on a naming habit:
    -- with a global key, two matters producing the same id would make one
    -- upload fail, and an upsert would overwrite another client's chunk.
    PRIMARY KEY (matter_id, chunk_id)
);

CREATE INDEX chunks_by_document ON chunks (matter_id, source_document);
CREATE INDEX chunks_in_order ON chunks (matter_id, inserted_seq);
-- No vector index. An exact scan over one matter's rows is sub-millisecond at
-- this size, and an HNSW index combined with a matter_id filter can silently
-- return fewer than the requested number of neighbours.

CREATE TABLE chat_exchanges (
    -- Ordering and pruning use this rather than created_at: two exchanges in
    -- the same instant would tie on a timestamp, never on the identity.
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL,
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('corpus', 'web', 'none')),
    faithful BOOLEAN NOT NULL,
    -- Citation locators, unsupported locators and web links - enough to
    -- redraw an answer's trust signals, deliberately not the passages quoted.
    citations JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX chat_by_user_and_matter ON chat_exchanges (username, matter_id, id);

-- Facts about the index as a whole. Today just the embedding model: vectors
-- from two different models of the same dimension compare without error and
-- rank by noise, so the mismatch has to be caught by name.
CREATE TABLE index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Never reachable through Supabase's auto-generated REST API. The app talks to
-- Postgres directly, so none of this affects it; it means client chats and
-- document text can't be read with the project's public anon key even if a
-- dashboard setting changes. RLS with no policies denies every API role.
ALTER TABLE matters ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_exchanges ENABLE ROW LEVEL SECURITY;
ALTER TABLE index_meta ENABLE ROW LEVEL SECURITY;

-- The roles exist only on Supabase, so revoke conditionally: a plain Postgres
-- would reject the statement outright.
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format(
                'REVOKE ALL ON matters, chunks, chat_exchanges, index_meta FROM %I', api_role
            );
        END IF;
    END LOOP;
END
$$;
