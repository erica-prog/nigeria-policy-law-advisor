-- Audit of research-agent runs (ADR-001 decision 6; docs/23 slice P2-E).
--
-- Every agent run is a row, and every tool call the run made or was refused
-- is a row under it, including the calls the policy denied and the one that
-- exhausted the budget. A lawyer's key paid for the run, so the row carries
-- who, what matter, which model, token counts and how it ended. Arguments
-- are stored truncated; results are not stored (their size is), because the
-- answer and its citations are what the conversation keeps.
CREATE TABLE agent_runs (
    run_id TEXT PRIMARY KEY,
    matter_id TEXT,
    username TEXT,
    question TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'research',
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'done', 'budget_exhausted', 'failed')),
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX agent_runs_by_matter ON agent_runs (matter_id, started_at DESC);
CREATE INDEX agent_runs_by_user ON agent_runs (username, started_at DESC);

CREATE TABLE agent_tool_calls (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    connector TEXT NOT NULL,
    tool TEXT NOT NULL,
    source_id TEXT,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('ok', 'denied', 'budget', 'error')),
    result_chars INTEGER,
    duration_ms INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agent_tool_calls_by_run ON agent_tool_calls (run_id, seq);

ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_tool_calls ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON agent_runs, agent_tool_calls FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
