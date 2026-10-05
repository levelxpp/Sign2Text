# Supabase Setup Guide

This guide walks operators through setting up Supabase as the optional cloud persistence backend for Sign2Text.

---

## Overview

Sign2Text can optionally use Supabase (managed Postgres via PostgREST) for:

- **User profiles** — synced from the existing Google OAuth callback
- **Transcript history** — authenticated transcript CRUD through the existing Voice service routes
- **Contributor leads** — public intake from the landing Contribute page
- **Feedback** — public submissions from the landing Home page

Supabase is **fully optional**. When disabled or unconfigured, the stack falls back to SQLite at `data/admin.sqlite3` for admin data, profile sync is silently skipped, and transcript routes return `503`.

No frontend Supabase client SDK or browser-side Supabase auth is used. All Supabase access is backend-only via the service-role key.

---

## Prerequisites

1. A Supabase project (free tier is sufficient)
2. From the Supabase dashboard under **Settings > API**, note:
   - **Project URL** (e.g. `https://xyz.supabase.co`)
   - **Service-role key** (the `service_role` key, not the `anon` key)
3. Python environment with the project dependencies installed

---

## 1. Apply SQL DDL

Run the following SQL files in the **Supabase SQL Editor** (Dashboard > SQL Editor), in this order:

| Order | File | Creates |
| :---: | :--- | :--- |
| 1 | `docs/sql/supabase_profiles.sql` | `profiles` table with `google_sub` unique index, RLS enabled |
| 2 | `docs/sql/supabase_transcripts.sql` | `transcripts` table with `(google_sub, client_history_id)` uniqueness and user-history indexes |
| 3 | `docs/sql/supabase_contributor_leads.sql` | `contributor_leads` table with unique `email` index, status and created_at indexes, RLS enabled |
| 4 | `docs/sql/supabase_feedback.sql` | `feedback` table with `created_at` index, RLS enabled |

Order matters: create profiles first, then transcripts for authenticated history sync, then the two admin tables.

### Verify tables exist

After applying the DDL, confirm in the Supabase dashboard under **Table Editor** that all four tables appear:

- `profiles` — columns: `id`, `google_sub`, `email`, `email_verified`, `display_name`, `avatar_url`, `last_login_at`, `updated_at`, `created_at`
- `transcripts` — columns: `id`, `google_sub`, `client_history_id`, `mode`, `started_at`, `text`, `created_at`, `updated_at`
- `contributor_leads` — columns: `id`, `email`, `source`, `status`, `notes`, `created_at`, `updated_at`
- `feedback` — columns: `id`, `name`, `message`, `source`, `created_at`

---

## 2. Configure Environment

Add these variables to your `.env` file (project root):

```env
SUPABASE_ENABLED=true
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key-here

# Optional — defaults shown:
# SUPABASE_SCHEMA=public
# SUPABASE_TIMEOUT_SEC=10.0

# Enable Supabase for admin persistence (contributor leads + feedback):
SUPABASE_USE_FOR_ADMIN=true
```

See [Runtime Configuration](../tech/CONFIGURATION.md) for full details on each variable.

`SUPABASE_USE_FOR_ADMIN` affects only contributor leads and feedback. Transcript storage uses `SUPABASE_ENABLED=true` plus the required credentials and schema, even when `SUPABASE_USE_FOR_ADMIN=false`.

---

## 3. Verify Profile Sync

1. Start the stack (`.\start.ps1` or `python start.py`)
2. Open Voice mode and complete a Google OAuth login
3. In the Supabase dashboard, check the `profiles` table — a row should appear with your Google `sub`, email, and display name
4. Log in again — the same row should be updated (not duplicated)

Profile sync is non-blocking. If Supabase is down or misconfigured, the OAuth login still succeeds and the failure is logged as a warning.

---

## 4. Verify Transcript Storage

1. Ensure `SUPABASE_ENABLED=true` in `.env`
2. Confirm `docs/sql/supabase_transcripts.sql` has been applied
3. Open the main app, sign in, and create or archive a non-empty session so the frontend saves history
4. Confirm the app can load or save transcript history without `503` responses from `/api/transcripts`
5. In the Supabase dashboard, check the `transcripts` table for rows keyed by `google_sub` and `client_history_id`

Current app behavior remains local-first: the browser keeps session history locally and merges or syncs backend transcript rows when transcript storage is available.

---

## 5. Verify Admin Persistence

1. Ensure `SUPABASE_USE_FOR_ADMIN=true` in `.env`
2. Restart the admin service
3. Check the admin service startup log for `Admin persistence: Supabase`
4. Submit a test contributor lead via the landing Contribute form
5. Submit test feedback via the landing Home feedback form
6. Check both tables in the Supabase dashboard

If the admin service log shows a fallback warning instead, check that `SUPABASE_ENABLED` is also `true` and that the URL/key are correct.

---

## 6. RLS Notes

- All four tables have **Row-Level Security (RLS) enabled**
- **No policies** are defined — the backend service-role key bypasses RLS entirely
- The `anon` key **cannot** read or write any of these tables
- If you later add browser-facing Supabase client access, you must add appropriate RLS policies
- No frontend Supabase auth is being used in this integration

---

## 7. What Stays Backend-Only

These components do not use Supabase and remain unchanged:

- Cookie-backed session state (owned by `stt_backend.py`)
- In-memory rate limiting and honeypot detection
- Admin token validation (`ADMIN_ACCESS_TOKEN`)
- ASL, Voice, and Letter inference pipelines
- `frontend/runtime-config.js` — never contains Supabase credentials

---

## 8. Migrating Existing SQLite Data

If you have existing contributor leads and feedback in `data/admin.sqlite3` that you want in Supabase, use the one-time migration utility.

### When to use

- You have been running Sign2Text with SQLite admin persistence
- You want to move that historical data into Supabase
- You have already applied the SQL DDL (Step 1) and configured the environment (Step 2)

### Command

```bash
python tools/migrate_admin_sqlite_to_supabase.py
```

### Options

| Flag | Purpose |
| :--- | :--- |
| `--db-path PATH` | Path to SQLite database (default: `data/admin.sqlite3`) |
| `--dry-run` | Read SQLite data and report what would be migrated, without writing to Supabase |

### Dry run (recommended first)

```bash
python tools/migrate_admin_sqlite_to_supabase.py --dry-run
```

### Expected output

```
Contributor leads in SQLite: 45
Feedback rows in SQLite:     12

=== Migration Summary ===
Contributor Leads: 45 read, 45 migrated, 0 failed
Feedback:          12 read, 12 migrated, 0 skipped (already in Supabase), 0 failed

SQLite database NOT modified.
```

### Safety notes

- The migration utility **never modifies or deletes SQLite data** — it is read-only
- Contributor leads use **upsert on email** — running the migration multiple times is safe and idempotent; each run inserts or updates
- Feedback uses a **deduplication check** before inserting — identical records (same name, message, source, and timestamp) are skipped
- The utility **requires explicit CLI invocation** — it never runs at startup
- If `SUPABASE_ENABLED` is not `true`, the utility exits with a clear error
- If the SQLite file does not exist, the utility exits with a clear error

### Rollback

The migration does not remove data from SQLite. To revert to SQLite-only persistence:

1. Set `SUPABASE_USE_FOR_ADMIN=false` (or remove it) in `.env`
2. Restart the admin service
3. The admin service will resume using `data/admin.sqlite3`

Supabase data remains intact and can be cleared manually via the dashboard if desired.
