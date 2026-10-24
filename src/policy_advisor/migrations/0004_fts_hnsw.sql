-- Keyword search in the database and an approximate index for the authority
-- tier (ADR-001 action 7).
--
-- The in-memory BM25 index is rebuilt from every row of a matter; fine for a
-- lawyer's case, not for a library of hundreds of thousands of passages. A
-- generated tsvector over the text (and its translation, so a query in
-- either language matches) lets Postgres rank keyword hits itself.
--
-- Vector search over one matter stays an exact scan (schema comment in
-- 0001). The authority tier is the exception: shared, large, and read far
-- more than written, so it gets an HNSW index. Partial on purpose: an
-- approximate index combined with a selective filter can return fewer than k
-- rows, and a lawyer's own matter must never pay that price.
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(text, '') || ' ' || coalesce(translated_text, ''))
        ) STORED;

CREATE INDEX IF NOT EXISTS chunks_search_vector ON chunks USING gin (search_vector);
CREATE INDEX IF NOT EXISTS chunks_by_locator ON chunks (matter_id, locator);
CREATE INDEX IF NOT EXISTS chunks_authority_embedding
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WHERE tier = 'authority';
