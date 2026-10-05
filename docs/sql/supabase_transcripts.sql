-- Supabase transcripts table for persisting user transcript history.
-- Run once in the Supabase SQL editor or via migration.
-- Task 2 of the transcript-history integration (schema + repository foundation).

CREATE TABLE IF NOT EXISTS transcripts (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    google_sub          TEXT        NOT NULL,
    client_history_id   TEXT        NOT NULL,
    mode                TEXT        NOT NULL,
    started_at          TIMESTAMPTZ,
    text                TEXT        NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Upsert conflict key: one transcript per user per client-side history entry.
CREATE UNIQUE INDEX IF NOT EXISTS idx_transcripts_sub_history
    ON transcripts (google_sub, client_history_id);

-- Supports user-scoped reverse-chronological listing.
CREATE INDEX IF NOT EXISTS idx_transcripts_user_created
    ON transcripts (google_sub, created_at DESC);

-- Row-Level Security: service-role key bypasses RLS,
-- but enable it so that anon/authenticated keys cannot read transcripts.
ALTER TABLE transcripts ENABLE ROW LEVEL SECURITY;

-- No policies needed yet — the backend uses the service-role key which
-- bypasses RLS.  Add policies here if you later expose transcripts to
-- the Supabase client SDK from the browser.
