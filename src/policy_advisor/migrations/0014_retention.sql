-- Residency, retention and holds (docs/30 slice P3-G, D9; docs/37).
--
-- A firm records where its data must live and how long a matter's rows and
-- originals may stay after the last activity. A matter on hold is never
-- deleted by the retention job. Every deletion, skip and dry-run decision
-- is a retention_events row, tenant-scoped like everything else.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS retention_days_firm INTEGER;

ALTER TABLE matters
    ADD COLUMN IF NOT EXISTS hold BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS hold_reason TEXT,
    ADD COLUMN IF NOT EXISTS hold_set_by TEXT,
    ADD COLUMN IF NOT EXISTS hold_set_at TIMESTAMPTZ;

CREATE TABLE retention_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT app_tenant() REFERENCES tenants (tenant_id),
    matter_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('deleted', 'would_delete', 'skipped_hold')),
    reason TEXT NOT NULL,
    counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX retention_events_by_tenant ON retention_events (tenant_id, created_at DESC);

ALTER TABLE retention_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE retention_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON retention_events
    USING (tenant_id = app_tenant() OR app_tenant() = '*')
    WITH CHECK (tenant_id = app_tenant() OR app_tenant() = '*');
GRANT SELECT, INSERT ON retention_events TO policy_advisor_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO policy_advisor_app;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON retention_events FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
