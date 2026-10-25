-- Tenancy (ADR-001 action 16; docs/30 slice P3-A, D1 and D2).
--
-- Until now every row belonged to whoever filtered by matter_id, and the
-- application connected as the database owner, so a forgotten filter read
-- everything. From here:
--
--   * `tenants` and `tenant_users` (with a role) exist; every user is in one.
--   * Every tenant-scoped table has a NOT NULL `tenant_id`, backfilled: the
--     shared libraries belong to the `shared` tenant, everything else to
--     `default`.
--   * The application works as the role `policy_advisor_app`, which cannot
--     bypass row-level security, and sets `app.tenant_id` per transaction.
--     Policies on every tenant-scoped table let a request see its own
--     tenant's rows plus the shared libraries, and write only its own (or
--     shared rows: the API refuses uploads to libraries at the application
--     level; the build script writes them under the shared tenant).
--   * With no tenant set, `app_tenant()` is NULL and only shared rows are
--     visible: the failure mode is an empty result, never another firm's.
--   * The document worker, a trusted process that must see every firm's
--     queue, sets `app.tenant_id = '*'`; the API never does (tenancy.py).
--
-- Everything is idempotent so it can be re-applied after a restore.

CREATE OR REPLACE FUNCTION app_tenant() RETURNS TEXT
LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '') $$;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    residency TEXT CHECK (residency IN ('ng', 'us')),
    retention_days_matter INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO tenants (tenant_id, name) VALUES
    ('shared', 'Shared reference libraries'),
    ('default', 'Default tenant')
ON CONFLICT (tenant_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS tenant_users (
    tenant_id TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    username TEXT NOT NULL REFERENCES users (username) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('admin', 'lawyer', 'reviewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, username)
);
CREATE INDEX IF NOT EXISTS tenant_users_by_user ON tenant_users (username);
INSERT INTO tenant_users (tenant_id, username, role)
SELECT 'default', username, 'lawyer' FROM users
ON CONFLICT DO NOTHING;

-- Matters may be shared with named users of the same tenant (D2).
CREATE TABLE IF NOT EXISTS matter_shares (
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    username TEXT NOT NULL REFERENCES users (username) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL REFERENCES tenants (tenant_id),
    can_write BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (matter_id, username)
);
CREATE INDEX IF NOT EXISTS matter_shares_by_user ON matter_shares (username);

-- tenant_id on every tenant-scoped table: add, backfill, require, default.
ALTER TABLE matters ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE matters SET tenant_id = CASE
    WHEN matter_id IN ('phase1-demo', 'us-library') OR owner IS NULL THEN 'shared'
    ELSE 'default' END
WHERE tenant_id IS NULL;
ALTER TABLE matters ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE matters ALTER COLUMN tenant_id SET DEFAULT app_tenant();
CREATE INDEX IF NOT EXISTS matters_by_tenant ON matters (tenant_id);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE chunks c SET tenant_id = m.tenant_id FROM matters m
WHERE c.matter_id = m.matter_id AND c.tenant_id IS NULL;
ALTER TABLE chunks ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE chunks ALTER COLUMN tenant_id SET DEFAULT app_tenant();

ALTER TABLE documents ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE documents d SET tenant_id = m.tenant_id FROM matters m
WHERE d.matter_id = m.matter_id AND d.tenant_id IS NULL;
ALTER TABLE documents ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE documents ALTER COLUMN tenant_id SET DEFAULT app_tenant();

ALTER TABLE document_jobs ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE document_jobs j SET tenant_id = m.tenant_id FROM matters m
WHERE j.matter_id = m.matter_id AND j.tenant_id IS NULL;
ALTER TABLE document_jobs ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE document_jobs ALTER COLUMN tenant_id SET DEFAULT app_tenant();

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE conversations c SET tenant_id = m.tenant_id FROM matters m
WHERE c.matter_id = m.matter_id AND c.tenant_id IS NULL;
ALTER TABLE conversations ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE conversations ALTER COLUMN tenant_id SET DEFAULT app_tenant();

ALTER TABLE chat_exchanges ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE chat_exchanges c SET tenant_id = m.tenant_id FROM matters m
WHERE c.matter_id = m.matter_id AND c.tenant_id IS NULL;
UPDATE chat_exchanges SET tenant_id = 'default' WHERE tenant_id IS NULL;
ALTER TABLE chat_exchanges ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE chat_exchanges ALTER COLUMN tenant_id SET DEFAULT app_tenant();

ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE agent_runs r SET tenant_id = m.tenant_id FROM matters m
WHERE r.matter_id = m.matter_id AND r.tenant_id IS NULL;
UPDATE agent_runs SET tenant_id = 'default' WHERE tenant_id IS NULL;
ALTER TABLE agent_runs ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE agent_runs ALTER COLUMN tenant_id SET DEFAULT app_tenant();

-- The application role: no login of its own (the connecting role switches to
-- it), no bypass of row-level security. Membership is granted to whoever
-- runs the migrations, which is the role the application connects as.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'policy_advisor_app') THEN
        CREATE ROLE policy_advisor_app NOLOGIN NOINHERIT NOBYPASSRLS;
    END IF;
    EXECUTE format('GRANT policy_advisor_app TO %I', current_user);
    IF EXISTS (SELECT FROM pg_namespace WHERE nspname = 'extensions') THEN
        GRANT USAGE ON SCHEMA extensions TO policy_advisor_app;
    END IF;
END
$$;
GRANT USAGE ON SCHEMA public TO policy_advisor_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO policy_advisor_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO policy_advisor_app;
GRANT EXECUTE ON FUNCTION app_tenant() TO policy_advisor_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO policy_advisor_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO policy_advisor_app;

-- Policies. FORCE makes them apply to the owner too, should the application
-- ever run as it. A provider's API roles keep the REVOKEs from earlier migrations.
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'matters', 'chunks', 'documents', 'document_jobs', 'conversations',
        'chat_exchanges', 'agent_runs', 'matter_shares'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = app_tenant() OR tenant_id = ''shared'' OR app_tenant() = ''*'') '
            'WITH CHECK (tenant_id = app_tenant() OR tenant_id = ''shared'' OR app_tenant() = ''*'')', t);
    END LOOP;
END
$$;

-- Tool calls follow their run (the subquery is itself policy-filtered).
ALTER TABLE agent_tool_calls FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON agent_tool_calls;
CREATE POLICY tenant_isolation ON agent_tool_calls
    USING (EXISTS (SELECT 1 FROM agent_runs r WHERE r.run_id = agent_tool_calls.run_id))
    WITH CHECK (EXISTS (SELECT 1 FROM agent_runs r WHERE r.run_id = agent_tool_calls.run_id));

-- Global tables (users, user_keys, sources, index_meta, tenants,
-- tenant_users, schema_migrations) are not tenant-scoped: users are keyed by
-- username and sources are the platform's. Earlier migrations enabled
-- row-level security on them with no policy, which the owner bypassed and the
-- application role cannot, so each gets an explicit allow-all policy for the
-- application role; a provider's API roles stay revoked.
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_users ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'users', 'user_keys', 'sources', 'index_meta', 'tenants', 'tenant_users', 'schema_migrations'
    ] LOOP
        EXECUTE format('DROP POLICY IF EXISTS app_all ON %I', t);
        EXECUTE format('CREATE POLICY app_all ON %I FOR ALL TO policy_advisor_app USING (true) WITH CHECK (true)', t);
    END LOOP;
END
$$;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON tenants, tenant_users, matter_shares FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
