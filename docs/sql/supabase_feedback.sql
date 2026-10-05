-- Supabase feedback table for public feedback submissions.
-- Run once in the Supabase SQL editor or via migration.
-- Task 4 of the Sign2Text Supabase integration plan.

CREATE TABLE IF NOT EXISTS feedback (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT        NOT NULL DEFAULT '',
    message     TEXT        NOT NULL DEFAULT '',
    source      TEXT        NOT NULL DEFAULT 'home',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_created_at
    ON feedback (created_at);

-- Row-Level Security: service-role key bypasses RLS,
-- but enable it so that anon/authenticated keys cannot read feedback.
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;

-- No policies needed yet — the backend uses the service-role key which
-- bypasses RLS.  Add policies here if you later expose this table to
-- the Supabase client SDK from the browser.
