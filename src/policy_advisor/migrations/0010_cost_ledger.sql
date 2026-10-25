-- The cost ledger (ADR-001 action 17; docs/30 slice P3-B, D8; docs/32).
--
-- One row per paid or metered call: a model call (tokens), a connector tool
-- call (duration), a web search (tokens). Attributed to the firm, the user,
-- the matter and the agent run or document job it belongs to, so a firm can
-- see what each person and each matter spent. The estimated cost is filled
-- from the price table in settings when one is configured; token counts are
-- the record either way. Tenant-scoped under the same forced policy as the
-- other tables; there are no shared ledger rows.
CREATE TABLE cost_ledger (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT app_tenant() REFERENCES tenants (tenant_id),
    username TEXT,
    matter_id TEXT,
    run_id TEXT,
    job_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('model', 'tool', 'web_search')),
    model TEXT,
    connector TEXT,
    tool TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_usd NUMERIC(12, 6),
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX cost_ledger_by_user ON cost_ledger (tenant_id, username, created_at DESC);
CREATE INDEX cost_ledger_by_matter ON cost_ledger (tenant_id, matter_id, created_at DESC);

ALTER TABLE cost_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cost_ledger
    USING (tenant_id = app_tenant() OR app_tenant() = '*')
    WITH CHECK (tenant_id = app_tenant() OR app_tenant() = '*');
GRANT SELECT, INSERT ON cost_ledger TO policy_advisor_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO policy_advisor_app;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON cost_ledger FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
