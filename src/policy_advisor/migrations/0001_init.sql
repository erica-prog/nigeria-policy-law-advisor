-- Initial schema: one SQLite file holding matters, their chunks (text,
-- metadata and embedding in the same row), and each lawyer's recent chats.
--
-- Replaces three stores that had to be kept in step by hand: Chroma for
-- vectors, data/index/matters/<id>/chunks.json for BM25, and meta.json for
-- ownership. A document added to or removed from one but not the others left
-- the keyword and vector indexes disagreeing about what a matter contained.

CREATE TABLE matters (
    matter_id TEXT PRIMARY KEY,
    owner TEXT,  -- NULL for the shared demo matter
    -- Bumped in the same transaction as every chunk write or delete, so a
    -- cached index can tell it is stale by comparing one integer, from any
    -- retriever instance and any process.
    content_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE chunks (
    chunk_id TEXT NOT NULL,
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
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
    translation_flagged INTEGER NOT NULL DEFAULT 0,
    translation_flag_reason TEXT,
    -- float32 little-endian. NOT NULL on purpose: a chunk that BM25 can find
    -- but vector search cannot is exactly the drift this schema exists to end.
    embedding BLOB NOT NULL,
    -- Unique within a matter, not globally. The chunker happens to prefix ids
    -- with the matter name, but isolation should not rest on a naming habit:
    -- with a global key, two matters producing the same id would make one
    -- upload fail, and an upsert would overwrite another client's chunk.
    PRIMARY KEY (matter_id, chunk_id)
);

CREATE INDEX chunks_by_document ON chunks (matter_id, source_document);

CREATE TABLE chat_exchanges (
    -- Ordering and pruning use this rather than created_at: two exchanges in
    -- the same millisecond would tie on a timestamp, never on the rowid.
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('corpus', 'web', 'none')),
    faithful INTEGER NOT NULL,
    -- JSON: citation locators, unsupported locators and web links - enough to
    -- redraw an answer's trust signals, deliberately not the passages quoted.
    citations TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX chat_by_user_and_matter ON chat_exchanges (username, matter_id, id);

-- Facts about the index as a whole. Today just the embedding model: vectors
-- from two different models are not comparable, and nothing about a BLOB
-- would reveal the mix.
CREATE TABLE index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
