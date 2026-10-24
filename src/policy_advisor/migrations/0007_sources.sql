-- The source registry (ADR-001 decision 5; docs/23 slice P2-A).
--
-- One row per external source the advisor may draw on: a hosted or local
-- MCP connector, the web-search allow-list, or a static library corpus.
-- The row records who publishes it, which connector reaches it, the
-- licence terms that constrain what may be stored, how freshness is
-- checked, and whether a lawyer has signed it off (`allowed`). Nothing
-- outside this table decides whether a source may be used: web search
-- reads its domains from the allowed web rows, and the agent loop (P2-E)
-- refuses any tool whose source is not allowed. A chunk's `source_id`
-- (migration 0003) points here.
--
-- Everything after the SEED marker is idempotent: it carries the one
-- allow-list that was hard-coded before this table existed, so a deployment
-- that has not run `scripts/manage_sources.py seed` keeps searching the two
-- official Nigerian domains it searched yesterday.
CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'authority'
        CHECK (tier IN ('matter', 'firm', 'authority')),
    trust_kind TEXT NOT NULL DEFAULT 'authority'
        CHECK (trust_kind IN ('evidence', 'firm', 'authority', 'web')),
    connector TEXT NOT NULL,
    tool_prefix TEXT,
    licence JSONB NOT NULL DEFAULT '{}'::jsonb,
    freshness JSONB NOT NULL DEFAULT '{}'::jsonb,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    allowed BOOLEAN NOT NULL DEFAULT false,
    allowed_by TEXT,
    allowed_at TIMESTAMPTZ,
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX sources_by_connector ON sources (connector, allowed);
CREATE INDEX sources_by_country ON sources (country, allowed);

ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON sources FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;

-- SEED (idempotent)
INSERT INTO sources (
    source_id, name, country, tier, trust_kind, connector, licence, freshness, config,
    allowed, allowed_by, allowed_at, notes
) VALUES (
    'web-official-ng',
    'Official Nigerian government websites (web search)',
    'NG', 'authority', 'web', 'web_search',
    '{"terms_url": null, "may_store_text": false, "may_store_snippets": true, "attribution": "cite the page URL"}'::jsonb,
    '{"how_checked": "live search on every call", "last_checked": null}'::jsonb,
    '{"domains": ["nass.gov.ng", "ndpc.gov.ng"], "max_uses": 3}'::jsonb,
    true, 'phase-1-default', now(),
    'The allow-list web_search.py carried as a constant before the registry existed. In use since Phase 1; a lawyer has not yet reviewed it.'
)
ON CONFLICT (source_id) DO NOTHING;
