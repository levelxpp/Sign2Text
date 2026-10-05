-- Supabase contributor_leads table for public contributor intake.
-- Run once in the Supabase SQL editor or via migration.
-- Task 4 of the Sign2Text Supabase integration plan.

CREATE TABLE IF NOT EXISTS contributor_leads (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       TEXT        NOT NULL,
    source      TEXT        NOT NULL DEFAULT 'landing_contribute',
    status      TEXT        NOT NULL DEFAULT 'new',
    notes       TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unique on email — enforces duplicate-email idempotency at the DB level.
-- Backend normalizes email to lowercase before insert, so this constraint
-- operates on the already-normalized value.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contributor_leads_email
    ON contributor_leads (email);

CREATE INDEX IF NOT EXISTS idx_contributor_leads_status
    ON contributor_leads (status);

CREATE INDEX IF NOT EXISTS idx_contributor_leads_created_at
    ON contributor_leads (created_at);

-- Row-Level Security: service-role key bypasses RLS,
-- but enable it so that anon/authenticated keys cannot read leads.
ALTER TABLE contributor_leads ENABLE ROW LEVEL SECURITY;

-- No policies needed yet — the backend uses the service-role key which
-- bypasses RLS.  Add policies here if you later expose this table to
-- the Supabase client SDK from the browser.
