-- The last state that lived beside the database moves into it (ADR-001
-- action 7): matter titles and timestamps, the per-matter conversation log,
-- lawyer accounts and their encrypted Claude keys. A fresh deployment with an
-- empty data/ directory now serves login, matters and chat from the
-- database alone; data/ keeps only uploaded originals and the demo PDFs.

ALTER TABLE matters
    ADD COLUMN IF NOT EXISTS title TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- One row per message, in order. The message is the client contract's
-- UserMessage or ChatReply as JSON; the API validates it on the way out.
CREATE TABLE conversations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    matter_id TEXT NOT NULL REFERENCES matters (matter_id) ON DELETE CASCADE,
    message JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX conversations_by_matter ON conversations (matter_id, id);

-- Accounts: the same bcrypt hashes credentials.yaml held, one row each.
CREATE TABLE users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Bring-your-own Claude keys: one Fernet token per user, encrypted under a
-- key derived from AUTH_COOKIE_KEY, exactly as user_keys.json stored them.
CREATE TABLE user_keys (
    username TEXT PRIMARY KEY REFERENCES users (username) ON DELETE CASCADE,
    token TEXT NOT NULL,
    last4 TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_keys ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON conversations, users, user_keys FROM %I', api_role);
        END IF;
    END LOOP;
END
$$;
