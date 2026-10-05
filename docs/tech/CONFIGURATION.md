# Runtime Configuration Guide

Sign2Text centralizes its runtime URL and port resolution in `backend/runtime_config.py`. This document summarizes the active configuration knobs that affect startup, browser routing, and third-party integrations.

## 1. Runtime Profiles

Runtime profile selection is handled by the launcher scripts rather than by a `start.py --profile` flag.

| Profile | How it is selected | Current behavior |
| :--- | :--- | :--- |
| `auto` | `setup.ps1` or `start.ps1 -Profile auto` | Chooses CUDA only when both PyTorch CUDA and ONNX Runtime CUDA are available; otherwise falls back to CPU |
| `cuda` | `setup.ps1 -Profile cuda` or `start.ps1 -Profile cuda` | Installs or requests the CUDA-capable runtime path |
| `cpu` | `setup.ps1 -Profile cpu` or `start.ps1 -Profile cpu` | Installs or requests the CPU-only runtime path |

`start.ps1` forwards the chosen value through `ASL_RUNTIME_PROFILE`.

## 2. Network Ports And Public URL Resolution

These values come from `backend/runtime_config.py`.

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `APP_PUBLIC_HOST` | `127.0.0.1` | Browser-visible hostname used to derive default public URLs |
| `APP_PUBLIC_SCHEME` | `http` | Browser-visible scheme used to derive default public URLs |
| `FRONTEND_PORT` | `5500` | Frontend server port |
| `ASL_API_PORT` | `8000` | ASL service port |
| `VOICE_API_PORT` | `8001` | Voice/Auth service port |
| `LETTER_API_PORT` | `8002` | Letter service port |
| `ADMIN_API_PORT` | `8003` | Admin service port |

### Explicit public URL overrides

| Variable | Default if unset | Purpose |
| :--- | :--- | :--- |
| `FRONTEND_PUBLIC_URL` | `<scheme>://<host>:5500` | Public frontend origin used for browser launch and same-origin CORS fallback |
| `ASL_API_BASE_URL` | `<scheme>://<host>:8000` | Browser-facing ASL base URL |
| `VOICE_API_BASE_URL` | `<scheme>://<host>:8001` | Browser-facing Voice base URL |
| `LETTER_API_BASE_URL` | `<scheme>://<host>:8002` | Browser-facing Letter base URL |
| `ADMIN_API_BASE_URL` | `<scheme>://<host>:8003` | Browser-facing Admin base URL |
| `VOICE_WS_URL` | Derived from `VOICE_API_BASE_URL + /ws/stt` | Browser-facing STT WebSocket URL |

### Bind host overrides

The service bind addresses are controlled separately from the public URL values:

- `ASL_BIND_HOST`
- `VOICE_BIND_HOST`
- `LETTER_BIND_HOST`
- `FRONTEND_BIND_HOST`
- `ADMIN_BIND_HOST` is read by `start.py` when launching the admin service

## 3. Generated Browser Runtime Config

At startup, `start.py` writes `frontend/runtime-config.js`.

That generated file is consumed by:

- `frontend/index.html`
- `frontend/admin.html`

It is not currently consumed by the React landing site under `landing/src/`. The landing site uses build-time `landing/src/config.js` instead. In the current source, dev mode uses direct localhost URLs and production builds target `/index.html` and `/admin-api`.

## 4. Proxy And Tunnel URL Overrides

When `start.py` runs with `--proxy` or `--tunnel`, it injects these values before loading runtime config:

| Variable | Value in proxy/tunnel mode |
| :--- | :--- |
| `ASL_API_BASE_URL` | `/asl-api` |
| `VOICE_API_BASE_URL` | `/voice-api` |
| `VOICE_WS_URL` | `/voice-api/ws/stt` |
| `LETTER_API_BASE_URL` | `/letter-api` |
| `ADMIN_API_BASE_URL` | `/admin-api` |

This keeps the main app and admin page on a single origin when they are served through `tunnel_proxy.py`. The landing site still relies on build-time `landing/src/config.js`, but its current production build also targets `/index.html` and `/admin-api`.

## 5. ASL And Ollama Settings

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `ASL_CHECKPOINT_PATH` | `backend/checkpoint/popsign_aslcitizen_semlex6_gcn_big/best_model.pt` | Path to the ASL checkpoint file |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Gloss-to-English normalization endpoint |
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct` | Model name passed to the Ollama-compatible endpoint |
| `OLLAMA_TIMEOUT_SEC` | `30` | Timeout for Ollama normalization requests |
| `OLLAMA_AUTH_TOKEN` | unset | Optional backend-only bearer token for a protected Ollama-compatible endpoint |
| `OLLAMA_MODE` | `local` | Used by helper scripts to decide whether to manage a local Ollama process |
| `SERVICE_TIMEOUT_SEC` | `30` | Startup readiness timeout used by `start.py` |

Important notes:

- `OLLAMA_AUTH_TOKEN` is backend-only and is not written into `frontend/runtime-config.js`.
- `start.ps1 -NoOllama` sets `ASL_DISABLE_TRANSLATION=1`, which disables the normalization step without removing ASL inference.

## 6. Authentication And Third-Party Configuration

Authentication is handled by Supabase. The browser signs in through the Supabase JS SDK (email+password or Google OAuth via the Supabase Google provider) and passes the issued JWT to every backend request. All three user-facing services (ASL, Voice, Letter) validate the Bearer token via `backend/auth_supabase.py`, which calls Supabase's `GET /auth/v1/user` endpoint (results cached ~60 s per token).

Additionally, the Voice path depends on Google STT/TTS credentials and Azure Translator credentials.

### Auth variables (required)

- `SUPABASE_URL` â€” e.g. `https://<project-ref>.supabase.co`. Written into `frontend/runtime-config.js` so the browser SDK can pick it up.
- `SUPABASE_ANON_KEY` â€” the project's publishable anon key. Also written into `runtime-config.js`.
- `SUPABASE_JWKS_URL` *(optional)* â€” override for the JWKS URL if the default `{SUPABASE_URL}/auth/v1/jwks` doesn't apply. For most projects this can be left unset.
- `FRONTEND_ORIGIN` â€” used for CORS (unchanged).

### Other common variables

- `AZURE_TRANSLATOR_KEY`
- `AZURE_TRANSLATOR_KEY_FILE`
- `AZURE_TRANSLATOR_REGION`
- `AZURE_TRANSLATOR_ENDPOINT`
- `KEYPOINTS_STREAM_ENABLED` â€” set to `true` to enable the skeleton-keypoints SSE stream consumed by the web overlay and mobile client. Default: `false`.

### Credential path variables recognized by the launcher/backend

- `GOOGLE_STT_CREDENTIALS`
- `STT_KEY_PATH`
- `GOOGLE_TTS_CREDENTIALS`
- `TTS_KEY_PATH`
- `GOOGLE_APPLICATION_CREDENTIALS`

The secret/config loaders also support `*_FILE` variants for selected secrets.

## 7. Admin And Proxy Security Settings

| Variable | Purpose |
| :--- | :--- |
| `ADMIN_ACCESS_TOKEN` | Required to unlock protected admin routes; weak or placeholder values disable admin access |
| `TRUSTED_PROXY` | Must be set to `true` to trust `X-Forwarded-For` for IP extraction |

Admin behavior tied to these settings:

- Protected admin routes return `503` when `ADMIN_ACCESS_TOKEN` is missing or rejected as insecure.
- Protected admin routes return `401` when the supplied `X-Admin-Token` does not match.
- Public contributor and feedback intake rate limits use the extracted client IP.

## 8. Configuration Sources

Configuration is loaded in this order, with later values overriding earlier ones:

1. Defaults defined in `backend/runtime_config.py`
2. Root `.env`
3. `backend/.env`
4. Process environment variables

## 9. Supabase Integration (Optional)

Sign2Text can optionally use Supabase as a cloud persistence backend. All Supabase configuration is backend-only and never exposed to the browser.

### Environment variables

| Variable | Type | Default | Purpose |
| :--- | :--- | :--- | :--- |
| `SUPABASE_ENABLED` | bool | `false` | Master switch â€” must be `true` for any Supabase I/O |
| `SUPABASE_URL` | string | (empty) | Supabase project URL (e.g. `https://xyz.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | string | (empty) | Service-role key for backend PostgREST access |
| `SUPABASE_SCHEMA` | string | `public` | PostgREST schema header |
| `SUPABASE_TIMEOUT_SEC` | float | `10.0` | httpx request timeout for PostgREST calls |
| `SUPABASE_USE_FOR_ADMIN` | bool | `false` | When `true` (and Supabase is enabled), contributor leads and feedback use Supabase instead of SQLite |

### Required combination

When `SUPABASE_ENABLED=true`, both `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` must be set and non-empty. If either is missing, `get_supabase_client()` raises a `RuntimeError` at init time.

Transcript storage is enabled by `SUPABASE_ENABLED=true` plus the required Supabase settings and SQL schema. It does not depend on `SUPABASE_USE_FOR_ADMIN`. If transcript storage is unavailable, the Voice service keeps running but `/api/transcripts` returns `503`.

### Secret exposure guarantee

None of these variables appear in `frontend_app_config` or `frontend/runtime-config.js`. The `SUPABASE_SERVICE_ROLE_KEY` is treated identically to `OLLAMA_AUTH_TOKEN` â€” parsed in the backend config, excluded from the frontend payload, and masked (last 4 chars only) in `SupabaseClient.__repr__`.

### SQLite fallback behavior

When `SUPABASE_USE_FOR_ADMIN=true` but Supabase initialization fails (disabled, misconfigured, or unreachable at startup), `admin_server.py` logs a warning and falls back to SQLite automatically. The fallback decision is made once at module init time â€” there is no per-request fallback.

### What syncs to Supabase

| Data | Trigger | Supabase table |
| :--- | :--- | :--- |
| User profiles | Supabase sign-in (populated by Supabase itself) | `auth.users` / `profiles` |
| Transcript history | Voice transcript CRUD via `/api/transcripts` | `transcripts` |
| Contributor leads | Public intake via `POST /api/public/contributor-leads` | `contributor_leads` |
| Feedback | Public submission via `POST /api/public/feedback` | `feedback` |

### What stays backend-only

Session state, rate-limit tracking, honeypot detection, admin token validation, and all inference state (ASL, Voice, Letter) remain entirely within the backend and do not use Supabase.

For setup instructions, see the [Supabase Setup Guide](../ops/SUPABASE_SETUP.md).
