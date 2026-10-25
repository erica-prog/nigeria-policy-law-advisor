-- Artefacts (ADR-001 decision 7 and action 15; docs/30 slice P3-E; docs/35).
--
-- A template is a firm's DOCX with Jinja tags and a style block; the shared
-- tenant holds the defaults every firm may use. An artefact is one generated
-- memo or deck: its outline, its provenance manifest (every paragraph to the
-- passages that support it), the blob keys of the rendered files, and a
-- review state that starts at draft. Nothing here sends or files anything.
CREATE TABLE templates (
    template_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT app_tenant() REFERENCES tenants (tenant_id),
    kind TEXT NOT NULL CHECK (kind IN ('memo', 'slides')),
    name TEXT NOT NULL,
    docx_key TEXT,
    style JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO templates (template_id, tenant_id, kind, name, docx_key, style, is_default) VALUES
    ('memo-default', 'shared', 'memo', 'Memorandum (default)', 'packaged:memo.docx',
     '{"font": "Calibri", "font_size": 11, "heading_colour": "1F3864", "footer": "Prepared with the Law & Policy Advisor; review before use."}'::jsonb, true),
    ('slides-default', 'shared', 'slides', 'Presentation (default)', NULL,
     '{"font": "Calibri", "title_colour": "1F3864", "footer": "Review before use."}'::jsonb, true)
ON CONFLICT (template_id) DO NOTHING;

CREATE TABLE artefacts (
    artefact_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT app_tenant() REFERENCES tenants (tenant_id),
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('memo', 'slides', 'both')),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed', 'approved', 'rejected')),
    instruction TEXT NOT NULL,
    outline JSONB NOT NULL,
    manifest JSONB NOT NULL,
    docx_key TEXT,
    pptx_key TEXT,
    template_id TEXT REFERENCES templates (template_id),
    run_id TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX artefacts_by_matter ON artefacts (matter_id, created_at DESC);

ALTER TABLE templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE templates FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON templates
    USING (tenant_id = app_tenant() OR tenant_id = 'shared' OR app_tenant() = '*')
    WITH CHECK (tenant_id = app_tenant() OR tenant_id = 'shared' OR app_tenant() = '*');
ALTER TABLE artefacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE artefacts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON artefacts
    USING (tenant_id = app_tenant() OR app_tenant() = '*')
    WITH CHECK (tenant_id = app_tenant() OR app_tenant() = '*');
GRANT SELECT, INSERT, UPDATE, DELETE ON templates, artefacts TO policy_advisor_app;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON templates, artefacts FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
