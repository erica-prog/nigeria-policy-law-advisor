-- Corpus tiers and the unified chunk metadata contract (ADR-001 decision 1).
--
-- A matter is the unit of isolation the platform already has, so a firm
-- knowledge base and an authority library are modelled as matters of a
-- different `tier`: no owner, and visible to everyone the tenancy work later
-- allows. Every chunk carries its matter's tier (denormalised for query
-- speed), a trust_kind the client renders without interpreting answer text,
-- the locator scheme its citation is written in, where the text came from
-- (source_id, licence, effective_date) and a structured jurisdiction.
--
-- Everything after the BACKFILL marker is idempotent and is re-run by the
-- tests against legacy-shaped rows, so it can be re-applied by hand after a
-- restore from backup.

ALTER TABLE matters
    ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'matter'
        CHECK (tier IN ('matter', 'firm', 'authority')),
    ADD COLUMN IF NOT EXISTS tenant_id TEXT;

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'matter'
        CHECK (tier IN ('matter', 'firm', 'authority')),
    ADD COLUMN IF NOT EXISTS trust_kind TEXT NOT NULL DEFAULT 'evidence'
        CHECK (trust_kind IN ('evidence', 'firm', 'authority', 'web')),
    ADD COLUMN IF NOT EXISTS locator_scheme TEXT,
    ADD COLUMN IF NOT EXISTS source_id TEXT,
    ADD COLUMN IF NOT EXISTS effective_date DATE,
    ADD COLUMN IF NOT EXISTS licence TEXT,
    ADD COLUMN IF NOT EXISTS country TEXT,
    ADD COLUMN IF NOT EXISTS jurisdiction_level TEXT,
    ADD COLUMN IF NOT EXISTS region TEXT,
    ADD COLUMN IF NOT EXISTS tenant_id TEXT;

CREATE INDEX IF NOT EXISTS chunks_by_tier ON chunks (tier, matter_id);

-- BACKFILL
-- The shared demo matter has always been a reference library: authority, not evidence.
UPDATE matters SET tier = 'authority' WHERE matter_id = 'phase1-demo' AND tier = 'matter';
-- The prototype's flat jurisdiction values become taxonomy ids
-- (src/policy_advisor/jurisdictions/data/taxonomy.json); the aliases stay accepted.
UPDATE chunks SET jurisdiction = 'ng-federal' WHERE jurisdiction = 'federal';
UPDATE chunks SET jurisdiction = 'ng-lagos' WHERE jurisdiction = 'lagos';
UPDATE chunks SET country = 'NG', jurisdiction_level = 'federal', region = NULL
    WHERE jurisdiction = 'ng-federal' AND country IS NULL;
UPDATE chunks SET country = 'NG', jurisdiction_level = 'state', region = 'Lagos'
    WHERE jurisdiction = 'ng-lagos' AND country IS NULL;
UPDATE chunks SET trust_kind = 'authority'
    WHERE doc_type IN ('statute', 'judgment') AND trust_kind = 'evidence';
UPDATE chunks c SET tier = m.tier FROM matters m
    WHERE m.matter_id = c.matter_id AND c.tier <> m.tier;
UPDATE chunks SET locator_scheme = CASE
        WHEN locator ~ '^Order [0-9]+ Rule [0-9]+' THEN 'ng_rules'
        WHEN locator ~ '^Paragraph [0-9]+' THEN 'judgment_para'
        WHEN locator ~ '^Section [0-9]+' THEN 'section'
    END
    WHERE locator_scheme IS NULL;
