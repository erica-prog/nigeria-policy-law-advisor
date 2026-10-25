-- Retained originals (ADR-001 action 4). One row per document per matter,
-- pointing at the blob store key that holds the uploaded bytes. The chunks
-- table keeps carrying source_document by name; this table is where the
-- original file, its hash and size are recorded so it can be re-processed or
-- shown to the lawyer later.
CREATE TABLE documents (
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes BIGINT NOT NULL,
    content_type TEXT,
    uploaded_by TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (matter_id, name)
);

-- Same posture as every other table: never reachable through a provider's
-- auto-generated REST API (see 0001_init.sql).
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON documents FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
