-- Supabase profiles table for Google-authenticated users.
-- Run once in the Supabase SQL editor or via migration.
-- Task 3 of the Sign2Text Supabase integration plan.

CREATE TABLE IF NOT EXISTS profiles (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    google_sub      TEXT        NOT NULL UNIQUE,
    email           TEXT        NOT NULL,
    email_verified  BOOLEAN     NOT NULL DEFAULT FALSE,
    display_name    TEXT        NOT NULL DEFAULT '',
    avatar_url      TEXT        NOT NULL DEFAULT '',
    last_login_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for email lookups (not unique — multiple Google accounts can share an email).
CREATE INDEX IF NOT EXISTS idx_profiles_email ON profiles (email);

-- Row-Level Security: service-role key bypasses RLS,
-- but enable it so that anon/authenticated keys cannot read profiles.
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

-- No policies needed yet — the backend uses the service-role key which
-- bypasses RLS.  Add policies here if you later expose profiles to the
-- Supabase client SDK from the browser.
