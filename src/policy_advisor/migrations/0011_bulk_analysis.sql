-- Bulk analysis (ADR-001 decision 8 and action 14; docs/30 slice P3-C; docs/33).
--
-- A bulk job extracts a typed record from every document of a matter with a
-- named schema, under a token ceiling the lawyer set when submitting; large
-- jobs go through the Batch API. Extractions are rows with evidence spans
-- that point back into the chunks the record was read from; findings are
-- the cross-document checks over them. All tenant-scoped under the forced
-- policy; the worker's '*' view claims from every firm's queue.
CREATE TABLE bulk_jobs (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT app_tenant() REFERENCES tenants (tenant_id),
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    schema_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    documents TEXT[] NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'waiting_batch', 'paused_ceiling', 'done', 'failed', 'cancelled')),
    backend TEXT NOT NULL DEFAULT 'sync' CHECK (backend IN ('sync', 'batch')),
    batch_id TEXT,
    triage_model TEXT NOT NULL,
    escalation_model TEXT NOT NULL,
    ceiling_tokens INTEGER NOT NULL,
    spent_tokens INTEGER NOT NULL DEFAULT 0,
    done_documents INTEGER NOT NULL DEFAULT 0,
    flagged_documents INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    submitted_by TEXT,
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX bulk_jobs_queue ON bulk_jobs (status, created_at);
CREATE INDEX bulk_jobs_by_matter ON bulk_jobs (matter_id, created_at DESC);

CREATE TABLE extractions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT app_tenant() REFERENCES tenants (tenant_id),
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    source_document TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    job_id TEXT REFERENCES bulk_jobs (job_id) ON DELETE SET NULL,
    record JSONB NOT NULL,
    evidence_spans JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence NUMERIC(4, 3) NOT NULL,
    model TEXT NOT NULL,
    flagged BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (matter_id, source_document, schema_id, schema_version)
);
CREATE INDEX extractions_by_matter ON extractions (matter_id, schema_id);
CREATE INDEX extractions_record ON extractions USING GIN (record);

CREATE TABLE findings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT app_tenant() REFERENCES tenants (tenant_id),
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    job_id TEXT REFERENCES bulk_jobs (job_id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'review' CHECK (severity IN ('info', 'review', 'high')),
    summary TEXT NOT NULL,
    documents TEXT[] NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'reviewed', 'dismissed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX findings_by_matter ON findings (matter_id, status, created_at DESC);

DO $$
DECLARE
    t TEXT;
    api_role TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['bulk_jobs', 'extractions', 'findings'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = app_tenant() OR app_tenant() = ''*'') '
            'WITH CHECK (tenant_id = app_tenant() OR app_tenant() = ''*'')', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO policy_advisor_app', t);
        FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
                EXECUTE format('REVOKE ALL ON %I FROM %I', t, api_role);
            END IF;
        END LOOP;
    END LOOP;
END
$$;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO policy_advisor_app;
