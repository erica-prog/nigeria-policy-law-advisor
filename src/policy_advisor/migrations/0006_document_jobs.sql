-- Document-processing jobs in the database (ADR-001 action 7).
--
-- The in-process job table forgot everything on restart: an upload accepted
-- a second before a deploy was never processed and never reported. Jobs are
-- rows now. The API enqueues; a worker (inline in the API process by default,
-- or `python -m policy_advisor.worker` alongside it) claims the oldest queued
-- job with SKIP LOCKED, so several workers never process the same upload.
-- The uploaded bytes live in the blob store from the moment of upload, so a
-- worker in another process can read them.
CREATE TABLE document_jobs (
    job_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'ready', 'failed', 'cancelled')),
    storage_key TEXT,
    jurisdiction TEXT,
    uploaded_by TEXT,
    chunk_count INTEGER,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX document_jobs_queue ON document_jobs (status, created_at);
CREATE INDEX document_jobs_by_matter ON document_jobs (matter_id, name, created_at DESC);

ALTER TABLE document_jobs ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON document_jobs FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
