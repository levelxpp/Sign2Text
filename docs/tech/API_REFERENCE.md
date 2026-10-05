# Sign2Text API Reference

This document summarizes the current browser-facing routes, backend endpoints, and proxy paths verified from the repository's live implementation.

> [!NOTE]
> All ports listed here are the defaults used by `start.py` and `backend/runtime_config.py`. They can be overridden by environment variables.

## 1. Frontend Routing And Static Entry Points

The browser surfaces are served by `backend/frontend_server.py`.

| Path | Current behavior |
| :--- | :--- |
| `/` | Serves `landing/dist/index.html` when the landing build exists; otherwise falls back to `frontend/index.html` |
| `/about` | Landing SPA route when the landing build exists |
| `/contribute` | Landing SPA route when the landing build exists |
| `/index.html` | Main browser app from `frontend/index.html` |
| `/admin.html` | Admin dashboard from `frontend/admin.html` |
| `/privacy.html` | Privacy page from `frontend/privacy.html` |
| `/terms.html` | Terms page from `frontend/terms.html` |

Additional routing notes:

- Built landing assets under `/assets/...` are served from `landing/dist/...`.
- Frontend app assets such as `app.js`, `style.css`, and `runtime-config.js` are served from `frontend/`.
- Unknown landing SPA paths resolve back to `landing/dist/index.html` when the landing build exists.

## 2. ASL Service (`backend/api_server.py`, default port `8000`)

The ASL service handles browser-uploaded camera frames, streaming events, annotated video output, and ASL runtime diagnostics.

### HTTP endpoints

- `GET /`
- `POST /start`
- `POST /stop`
- `GET /status`
- `GET /runtime`
- `GET /stream`
- `GET /video_feed`
- `POST /upload_frame`
- `POST /manual_sign`
- `POST /threshold`
- `POST /clear`
- `POST /reset`

### WebSocket endpoints

- `WS /ws/frames`

### Notes

- The current browser app starts ASL with `camera_source: "remote"` and uploads frames from the browser camera to `POST /upload_frame`.
- `GET /stream` is the main SSE channel consumed by the app.
- `GET /video_feed` returns the backend's annotated MJPEG stream.
- `GET /runtime` exposes runtime and hardware diagnostics.
- There is currently no ASL `GET /health` route.

### Authentication

All endpoints except `GET /` require a valid Supabase JWT. Send it via `Authorization: Bearer <token>`, or — for browser `EventSource` and `WebSocket` connections that cannot set custom headers — as a `?token=<token>` query parameter. Unauthenticated HTTP requests return `401 {"detail": "Missing Bearer token."}`. Unauthenticated WebSocket connections to `/ws/frames` are rejected with close code `4401` (browsers see HTTP 403 during the upgrade handshake).

Additionally: `POST /offer` (WebRTC offer from the mobile client) is protected the same way.

## 3. Voice Service (`backend/stt_backend.py`, default port `8001`)

The Voice service hosts real-time speech-to-text, text translation, text-to-speech, and two thin auth helper endpoints. The actual sign-in happens in the browser against Supabase; the backend only validates the resulting JWT.

### HTTP endpoints

- `GET /auth/me` — returns `{sub, email, role}` from the current Bearer token
- `POST /auth/logout` — no-op (returns `{"ok": true}`); the client-side Supabase `signOut()` does the real work
- `GET /health`
- `POST /api/translate`
- `POST /api/tts`

### WebSocket endpoints

- `WS /ws/stt`

### Transcript history endpoints

- `POST /api/transcripts` — save a transcript entry (requires auth)
- `GET /api/transcripts?limit=N` — list user's transcripts, max 100 (requires auth)
- `DELETE /api/transcripts/{transcript_id}` — delete a single transcript (requires auth)
- `DELETE /api/transcripts` — delete all transcripts for the authenticated user (requires auth); returns `{"deleted": true, "count": N}`

All transcript endpoints require a valid Supabase JWT. Ownership is derived from the `sub` claim of the token — the client cannot specify or override ownership. When `SUPABASE_ENABLED=false` or the database is unavailable, transcript endpoints return `503`.

These transcript routes are implemented in the Voice service and use the Supabase transcript repository when transcript storage is available.

### Auth behavior

- The browser app requires sign-in (email+password or Google via Supabase) before starting any pipeline.
- `POST /api/translate`, `POST /api/tts`, and `WS /ws/stt` all require the Bearer token. WebSocket clients pass the token via `?token=` since browsers cannot set `Authorization` headers on WS connections.

## 4. Letter Service (`backend/letter_server.py`, default port `8002`)

The Letter service powers fingerspelling recognition from browser-uploaded frames.

### HTTP endpoints

- `GET /`
- `GET /health`
- `POST /start`
- `POST /stop`
- `GET /status`
- `POST /upload_frame`
- `GET /stream`

### SSE event patterns

- `letter_detected`
- `letter_accepted`
- `word_completed`
- lifecycle and error events

### Notes

- The frontend opens the browser camera locally and uploads JPEG frames to `POST /upload_frame`.
- Word assembly is backend-driven, and the frontend also applies duplicate/repeat-control logic while keeping its displayed word in sync with accepted backend output.
- When the user selects a non-English target language in Letter mode, the frontend translates each committed word via the Voice service's `POST /api/translate` and speaks it via `POST /api/tts`. The Letter service itself does not perform translation or TTS.

### Authentication

All endpoints except `GET /` and `GET /health` require a valid Supabase JWT (same Bearer / `?token=` scheme as the ASL service). Unauthenticated requests return `401 {"detail": "Missing Bearer token."}`.

## 5. Admin Service (`backend/admin_server.py`, default port `8003`)

The Admin service handles public contributor intake, public feedback submission, and token-protected admin management routes. Data is stored in `data/admin.sqlite3` by default, with optional Supabase-backed admin persistence when enabled.

### Public endpoints

- `GET /`
- `GET /health`
- `POST /api/public/contributor-leads`
- `POST /api/public/feedback`

### Protected endpoints

These endpoints require the `X-Admin-Token` header:

- `GET /api/admin/stats`
- `GET /api/admin/contributor-leads`
- `GET /api/admin/contributor-leads/export`
- `PATCH /api/admin/contributor-leads/{id}`
- `DELETE /api/admin/contributor-leads/{id}`
- `DELETE /api/admin/contributor-leads`
- `GET /api/admin/feedback`
- `GET /api/admin/feedback/export`

### Public intake behavior

- Contributor leads use duplicate-email idempotency, honeypot protection, and IP-based rate limiting.
- Feedback uses the same honeypot and IP-based rate limiting.
- Empty feedback messages are accepted as safe no-ops and do not create database rows.

### Admin protection behavior

- Protected routes return `503` when `ADMIN_ACCESS_TOKEN` is missing or rejected as insecure.
- Protected routes return `401` when the presented `X-Admin-Token` is missing or does not match.

## 6. Proxy And Tunnel Route Prefixes

When `start.py` runs with `--proxy` or `--tunnel`, `tunnel_proxy.py` exposes the stack behind a single entry point and `start.py` writes proxy-aware paths into `frontend/runtime-config.js`.

| Proxy path | Upstream |
| :--- | :--- |
| `/` | Frontend server on port `5500` |
| `/asl-api/*` | ASL service on port `8000` |
| `/voice-api/*` | Voice service on port `8001` |
| `/letter-api/*` | Letter service on port `8002` |
| `/admin-api/*` | Admin service on port `8003` |
| `/voice-api/ws/stt` | Voice WebSocket |
| `/asl-api/ws/frames` | ASL frame WebSocket |

Important caveat:

- The main app and admin page read those proxy paths from generated `frontend/runtime-config.js`.
- The React landing site does not consume `runtime-config.js`; it still resolves its app/admin targets through build-time `landing/src/config.js`.
- In the current source, production builds target `/index.html` and `/admin-api`. If `landing/dist` is stale, rebuild the landing app to pick up those current production values.
