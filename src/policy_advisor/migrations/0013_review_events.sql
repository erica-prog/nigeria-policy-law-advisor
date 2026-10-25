-- Review events (docs/30 slice P3-F, D7; docs/36): every transition of an
-- artefact or a finding, who made it with which role, and a note. The
-- states themselves live on the rows (artefacts.status, findings.status);
-- this table is the immutable trail behind them. Tenant-scoped.
CREATE TABLE review_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT app_tenant() REFERENCES tenants (tenant_id),
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('artefact', 'finding')),
    subject_id TEXT NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    username TEXT,
    role TEXT,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX review_events_by_subject ON review_events (subject_kind, subject_id, created_at);
CREATE INDEX review_events_by_matter ON review_events (matter_id, created_at DESC);

ALTER TABLE review_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON review_events
    USING (tenant_id = app_tenant() OR app_tenant() = '*')
    WITH CHECK (tenant_id = app_tenant() OR app_tenant() = '*');
GRANT SELECT, INSERT ON review_events TO policy_advisor_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO policy_advisor_app;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON review_events FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
