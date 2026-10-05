# System Architecture

Sign2Text is a multi-surface application with a routing-aware frontend server and four backend API services. This document summarizes the current architecture and how the pieces connect at runtime.

## 1. Runtime Services

The shipped runtime is coordinated by `start.py`, which launches the frontend server plus four Python service processes.

| Runtime component | Default port | Current responsibility |
| :--- | :--- | :--- |
| Frontend server | `5500` | Serves the landing site, main app, admin page, and legal pages |
| ASL service | `8000` | ASL inference, SSE, MJPEG, frame upload, runtime diagnostics |
| Voice service | `8001` | STT WebSocket, translation, mounted TTS, `/auth/me`, `/auth/logout` |
| Letter service | `8002` | Fingerspelling recognition, SSE, frame upload, word completion |
| Admin service | `8003` | Contributor leads, public feedback, admin stats/export/management |

## 2. Frontend Surfaces And Routing

`backend/frontend_server.py` is the routing layer in front of the browser surfaces.

### Browser entry points

- `/` serves the React/Vite landing site when `landing/dist/index.html` exists.
- `/index.html` always serves the main browser app from `frontend/index.html`.
- `/admin.html` always serves the admin dashboard from `frontend/admin.html`.
- `/privacy.html` and `/terms.html` always serve the legal pages from `frontend/`.

### Landing-aware behavior

- When the landing build exists, landing SPA routes such as `/about` and `/contribute` resolve to `landing/dist/index.html`.
- When the landing build is missing, `/` falls back to the main app.
- App assets such as `app.js`, `style.css`, and `runtime-config.js` continue to come from `frontend/` even when the landing site is present.

## 3. Transport Patterns

Different parts of the product use different transport patterns based on the data they stream.

- ASL uses HTTP frame upload, SSE (`GET /stream`), MJPEG (`GET /video_feed`), and an auxiliary frame WebSocket.
- Voice uses cookie-backed HTTP routes plus `WS /ws/stt` for bidirectional audio streaming and transcript messages.
- Letter uses HTTP frame upload and SSE (`GET /stream`) for recognized-letter and word-completion events.
- Admin uses standard JSON/CSV HTTP endpoints.

## 4. Startup And Runtime Wiring

The normal startup path is:

1. `setup.ps1` creates `.venv`, detects `auto`/`cuda`/`cpu`, and installs either `requirements.runtime.txt` or `requirements.runtime-cpu.txt`.
2. `start.ps1` sets `ASL_RUNTIME_PROFILE`, optionally enables tunnel mode, optionally disables Ollama normalization with `-NoOllama`, and runs `start.py`.
3. `start.py` loads `backend/runtime_config.py`, validates the ASL checkpoint, writes `frontend/runtime-config.js`, and launches the frontend, ASL, Voice, Letter, and Admin services.

Proxy and tunnel notes:

- `python start.py --proxy` starts `tunnel_proxy.py` on port `8080`.
- `python start.py --tunnel` starts the same proxy layer and then launches a Cloudflare Quick Tunnel.
- In proxy or tunnel mode, `start.py` injects `/asl-api`, `/voice-api`, `/letter-api`, `/admin-api`, and `/voice-api/ws/stt` into generated `frontend/runtime-config.js`.

## 5. Browser Configuration Split

The repository currently has two different browser-side configuration paths:

- The main app (`frontend/index.html`) and admin page (`frontend/admin.html`) read generated `frontend/runtime-config.js`.
- The landing React site uses `landing/src/config.js` instead.

That split matters operationally:

- The main app and admin page automatically follow proxy-aware base URLs in `--proxy` and `--tunnel` mode.
- The landing site's current production build also targets `/index.html` and `/admin-api`, but it gets those values from a build step rather than from generated runtime config.
- If `landing/dist` is stale relative to `landing/src/config.js`, rebuild the landing app to pick up the current production targets.

## 6. Data And Persistence

The current persistence layer is lightweight and service-specific.

- Contributor leads and public feedback use SQLite at `data/admin.sqlite3` by default. When `SUPABASE_ENABLED=true` and `SUPABASE_USE_FOR_ADMIN=true`, admin persistence switches to Supabase with SQLite fallback on init failure.
- Browser session history for captions is always stored client-side in local storage.
- Authenticated users can also load, merge, and save transcript history through the Voice service transcript endpoints when the Supabase transcript repository is available; otherwise those endpoints return `503`.
- Voice authentication uses cookie-backed sessions in the backend.
- The ASL runtime loads its default checkpoint bundle from `backend/checkpoint/popsign_aslcitizen_semlex6_gcn_big/`.

## 7. ML-To-Runtime Lifecycle

Model training happens offline. This repository contains the runtime inference path only.

- Offline keypoint extraction and model training are maintained privately and are not published in this repository.
- Runtime ASL inference is served by `backend/api_server.py`, which loads the checkpoint bundle and its `label2idx.json` metadata through the logic in `backend/new_Inference.py`.

The shipped runtime therefore depends on the checkpoint assets already being present on disk, even though extraction and training happen outside the normal deployment path.

## 8. Security And Access Control

- All user-facing modes (ASL, Voice, Letter) require Supabase JWT authentication. The web frontend signs in via Supabase (email+password or Google OAuth through the Supabase provider) and the SDK returns a Bearer access token.
- Services validate the token on every request via `backend/auth_supabase.py`. `require_user()` reads `Authorization: Bearer <token>` (falling back to `?token=` query param for browser `EventSource` / `WebSocket` which cannot set custom headers). `get_user_from_ws()` uses the same logic for WebSocket routes. Tokens are validated by calling Supabase's `GET /auth/v1/user` endpoint; successful results are cached for ~60 s.
- Required env vars for auth: `SUPABASE_URL`, `SUPABASE_ANON_KEY`. The frontend reads these via `window.APP_CONFIG` emitted by `runtime_config.py`.
- Only two auth routes remain on the Voice service: `GET /auth/me` (returns the current user from the Bearer token) and `POST /auth/logout` (no-op â€” client-side Supabase `signOut()` handles real sign-out).
- The frontend enforces an app-wide auth gate before starting any pipeline.
- Public routes (`/` and `/health` where present) remain accessible without authentication.
- Admin APIs are gated by `X-Admin-Token` (separate from user auth).
- Missing or insecure `ADMIN_ACCESS_TOKEN` disables protected admin routes with `503`.
- Trusted proxy IP extraction is available when `TRUSTED_PROXY=true`.
