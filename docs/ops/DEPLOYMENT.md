# Sign2Text Deployment Guide

This guide describes the repository-supported deployment workflows that are currently implemented in the codebase and launcher scripts.

## Startup Prerequisites

Before launching the stack, verify these inputs:

### 1. ASL checkpoint

The ASL runtime will not start unless the checkpoint file exists.

- Default path: `backend/checkpoint/popsign_aslcitizen_semlex6_gcn_big/best_model.pt`
- Override variable: `ASL_CHECKPOINT_PATH`

### 2. Admin security

Protected admin routes depend on a real admin access token.

- Environment variable: `ADMIN_ACCESS_TOKEN`
- Current rule: at least 16 characters and not a placeholder such as `change_me`, `admin`, `password`, or `your_admin_token_here`
- Current failure mode: protected admin routes return `503` when the configured token is missing or rejected as insecure

### 3. Provider configuration

Configure only the providers you plan to use:

- Google OAuth, STT, and TTS for Voice mode
- Azure Translator for text translation in Voice mode
- Ollama or another Ollama-compatible endpoint for ASL gloss-to-English normalization

### 4. Supabase (optional)

Supabase is fully optional. The stack runs without it, using SQLite for admin persistence, skipping profile sync, and leaving backend transcript sync unavailable.

To enable Supabase:

- Set `SUPABASE_ENABLED=true`, `SUPABASE_URL`, and `SUPABASE_SERVICE_ROLE_KEY` in `.env`
- Apply the SQL DDL files in the Supabase dashboard, including `docs/sql/supabase_transcripts.sql` (see [Supabase Setup Guide](SUPABASE_SETUP.md) for apply order)
- Transcript history requires only `SUPABASE_ENABLED=true` plus the Supabase schema and credentials
- For admin persistence (contributor leads + feedback): also set `SUPABASE_USE_FOR_ADMIN=true`
- For existing SQLite data: optionally run the one-time migration utility (see [Supabase Setup Guide â€” Migration](SUPABASE_SETUP.md#8-migrating-existing-sqlite-data))

## Deployment Workflows

### 1. Local Windows deployment

The standard Windows workflow uses the repository's PowerShell scripts.

1. Prepare the environment:

   ```powershell
   .\setup.ps1
   ```

2. Launch the stack:

   ```powershell
   .\start.ps1
   ```

Optional launch variants:

```powershell
.\start.ps1 -Profile cpu
.\start.ps1 -Profile cuda
.\start.ps1 -Tunnel
.\start.ps1 -NoOllama
```

Current browser-open behavior:

- When `landing/dist/index.html` exists, `start.py` opens the frontend launch URL at `/`.
- When the landing build is missing, `start.py` opens `/index.html`.

### 2. Headless Linux / GPU host deployment

For Azure, RunPod, or similar Linux hosts:

1. Prepare the environment:

   ```bash
   bash setup_runpod.sh
   ```

2. Launch the stack:

   ```bash
   bash start_server.sh --proxy
   ```

`start_server.sh` runs `python start.py --headless` and forwards any extra flags such as `--proxy`.

## Service Flags And Coordination

`start.py` currently supports these flags:

| Flag | Description |
| :--- | :--- |
| `--headless` | Non-interactive mode with no browser auto-open and no interactive port-conflict prompts |
| `--proxy` | Starts `tunnel_proxy.py` on `0.0.0.0:8080` and routes the app through a single public port |
| `--tunnel` | Starts the local proxy layer and then launches a Cloudflare Quick Tunnel |

Profile selection is not a `start.py` flag. Use `start.ps1 -Profile ...` or set `ASL_RUNTIME_PROFILE` in the environment.

## Post-Deployment Checklist

### 1. Frontend surfaces

Verify that the frontend server is serving the expected pages:

- Landing page: `http://localhost:5500/` when the landing build exists
- Main app: `http://localhost:5500/index.html`
- Admin dashboard: `http://localhost:5500/admin.html`
- Legal pages: `http://localhost:5500/privacy.html` and `http://localhost:5500/terms.html`

### 2. Backend responsiveness

Verify that the backend services are responding on their implemented routes:

- ASL service: `GET http://localhost:8000/status` or `GET http://localhost:8000/runtime`
- Voice/Auth service: `GET http://localhost:8001/health`
- Letter service: `GET http://localhost:8002/health` or `GET http://localhost:8002/status`
- Admin service: `GET http://localhost:8003/health`

### 3. Proxy-specific checks

If you launched with `--proxy` or `--tunnel`, also verify:

- `http://localhost:8080/index.html`
- `http://localhost:8080/admin.html`
- `http://localhost:8080/asl-api/status`
- `http://localhost:8080/voice-api/health`
- `http://localhost:8080/letter-api/health`
- `http://localhost:8080/admin-api/health`

### 4. Supabase verification

If Supabase is enabled, verify:

- **Profile sync**: complete a Google OAuth login via Voice mode, then check the `profiles` table in the Supabase dashboard for a new or updated row.
- **Transcript storage**: sign in through the main app, save or archive a non-empty session, and confirm `/api/transcripts` succeeds without `503`. Then verify rows appear in the `transcripts` table.
- **Admin persistence**: check the admin service startup log for `Admin persistence: Supabase`. Submit a test contributor lead and feedback entry, then verify both appear in the Supabase dashboard.
- **Fallback**: if the admin service log shows a fallback warning, check that `SUPABASE_ENABLED`, `SUPABASE_URL`, and `SUPABASE_SERVICE_ROLE_KEY` are all correctly set. The existence of `data/admin.sqlite3` is expected and not an error â€” it remains as the fallback database.

## Deployment Caveats

- The main app and admin page use generated `frontend/runtime-config.js` and correctly follow `/asl-api`, `/voice-api`, `/letter-api`, and `/admin-api` in proxy or tunnel mode.
- The React landing site still relies on build-time `landing/src/config.js` rather than generated runtime config.
- In the current source, production builds target `/index.html` and `/admin-api`. If the deployed `landing/dist` is stale, rebuild the landing app from `landing/` with `npm ci` and `npm run build` before redeploying.
