import asyncio
import base64
import hashlib
import hmac
import json
import os
import queue
import threading
import logging
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple, Iterator, List, Dict, Any

import httpx
from fastapi import FastAPI, WebSocket, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from google.cloud import speech_v1 as speech
from google.oauth2 import service_account
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

try:
    from backend.browser_origin import (
        build_cors_settings,
        callback_url_for_base,
        configured_browser_origin,
        is_localish_origin,
    )
    from backend.auth_supabase import require_user, get_user_from_ws
except Exception:
    from browser_origin import (
        build_cors_settings,
        callback_url_for_base,
        configured_browser_origin,
        is_localish_origin,
    )
    from auth_supabase import require_user, get_user_from_ws

try:
    from backend.secret_config import (
        apply_project_dotenv,
        google_credentials_candidates,
        read_bool_env,
        read_secret_value,
        resolve_project_path,
    )
except Exception:
    from secret_config import (
        apply_project_dotenv,
        google_credentials_candidates,
        read_bool_env,
        read_secret_value,
        resolve_project_path,
    )

try:
    from backend.runtime_config import load_runtime_config
except Exception:
    from runtime_config import load_runtime_config

try:
    from backend.supabase_client import get_supabase_client
    from backend.supabase_repo import ProfileRepo, TranscriptRepo
except Exception:
    from supabase_client import get_supabase_client
    from supabase_repo import ProfileRepo, TranscriptRepo

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stt_backend")

LOCALISH_HOSTS = {
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "::1",
    "::",
}

apply_project_dotenv()
RUNTIME_CONFIG = load_runtime_config()


# â”€â”€ Supabase profile sync (best-effort, non-blocking) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _init_profile_repo():
    """Build ProfileRepo if Supabase is enabled.  Never raises."""
    try:
        client = get_supabase_client(RUNTIME_CONFIG)
        if client is None:
            return None
        return ProfileRepo(client)
    except Exception:
        logger.warning("Supabase profile sync disabled: config error", exc_info=True)
        return None


_profile_repo = _init_profile_repo()


# â”€â”€ Supabase transcript repo (best-effort, non-blocking) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _init_transcript_repo():
    """Build TranscriptRepo if Supabase is enabled.  Never raises."""
    try:
        client = get_supabase_client(RUNTIME_CONFIG)
        if client is None:
            return None
        return TranscriptRepo(client)
    except Exception:
        logger.warning("Supabase transcript repo disabled: config error", exc_info=True)
        return None


_transcript_repo = _init_transcript_repo()


def _sync_profile_background(user: Dict[str, Any]) -> None:
    """Fire-and-forget profile upsert in a background thread."""
    def _do_sync():
        try:
            _profile_repo.upsert_from_google_user(
                google_sub=user["sub"],
                email=user["email"],
                name=user["name"],
                picture=user["picture"],
                email_verified=user["email_verified"],
            )
        except Exception:
            logger.warning("Supabase profile sync failed (non-fatal)", exc_info=True)

    threading.Thread(target=_do_sync, daemon=True).start()


FRONTEND_PORT = RUNTIME_CONFIG.frontend_port
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "").strip().rstrip("/")
CONFIGURED_BROWSER_ORIGIN = configured_browser_origin(
    frontend_origin=FRONTEND_ORIGIN,
    frontend_public_url=RUNTIME_CONFIG.frontend_public_url,
    frontend_public_url_explicit=RUNTIME_CONFIG.frontend_public_url_explicit,
    public_host=RUNTIME_CONFIG.public_host,
    public_scheme=RUNTIME_CONFIG.public_scheme,
    public_host_explicit=RUNTIME_CONFIG.public_host_explicit,
    same_origin_base_url=RUNTIME_CONFIG.voice_api_base_url,
)
CONFIGURED_CALLBACK_URI = callback_url_for_base(
    RUNTIME_CONFIG.voice_api_base_url,
    origin=CONFIGURED_BROWSER_ORIGIN,
)

app = FastAPI()

# -------------------------
# CORS
# -------------------------
cors_env = os.environ.get("CORS_ORIGINS", "").strip()
allow_origin_regex: Optional[str] = None


def _is_localish_origin(origin: str) -> bool:
    try:
        host = (urllib.parse.urlparse(origin).hostname or "").strip().lower()
    except Exception:
        return False
    return host in LOCALISH_HOSTS


def _local_frontend_origin_regex() -> str:
    return rf"^https?://[^/]+:{FRONTEND_PORT}$"


def _local_frontend_ipv6_origins() -> List[str]:
    return [f"http://[::]:{FRONTEND_PORT}", f"http://[::1]:{FRONTEND_PORT}"]

if cors_env == "*":
    allow_origins: List[str] = []
    allow_origin_regex = r"^https?://[^/]+$"
    allow_credentials = True
    logger.warning(
        "CORS_ORIGINS='*' is a development-only fallback. "
        "Set an explicit deployment origin for public hosting.",
    )
else:
    allow_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
    allow_credentials = True
    all_localish = bool(allow_origins) and all(_is_localish_origin(o) for o in allow_origins)
    if all_localish:
        allow_origin_regex = _local_frontend_origin_regex()

    has_localhost = any("localhost" in o or "127.0.0.1" in o for o in allow_origins)
    if has_localhost:
        for extra in _local_frontend_ipv6_origins():
            if extra not in allow_origins:
                allow_origins.append(extra)


def _build_cors_settings(cors_env: Optional[str] = None) -> Dict[str, Any]:
    raw = cors_env if cors_env is not None else os.environ.get("CORS_ORIGINS", "")
    return build_cors_settings(
        explicit_origins=raw,
        frontend_port=FRONTEND_PORT,
        allow_credentials=True,
        fallback_origin=CONFIGURED_BROWSER_ORIGIN,
        log_label="Voice CORS",
    )


_cors_settings = _build_cors_settings()
allow_origins = _cors_settings["allow_origins"]
allow_origin_regex = _cors_settings["allow_origin_regex"]
allow_credentials = _cors_settings["allow_credentials"]

app.add_middleware(
    CORSMiddleware,
    **_cors_settings,
)

# -------------------------
# Google OAuth (Authorization Code)
# -------------------------
GOOGLE_CLIENT_ID = read_secret_value("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = read_secret_value("GOOGLE_CLIENT_SECRET")

# NEW: allow multiple redirect URIs (comma-separated)
_REDIRECTS_RAW = (os.environ.get("GOOGLE_REDIRECT_URIS", "") or "").strip()
_SINGLE_REDIRECT = (os.environ.get("GOOGLE_REDIRECT_URI", "") or "").strip()

GOOGLE_REDIRECT_URIS: List[str] = []
if _REDIRECTS_RAW:
    GOOGLE_REDIRECT_URIS = [x.strip() for x in _REDIRECTS_RAW.split(",") if x.strip()]
elif _SINGLE_REDIRECT:
    GOOGLE_REDIRECT_URIS = [_SINGLE_REDIRECT]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _auth_is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URIS)


_require_user_http = require_user       # from auth_supabase â€” shared helper
_get_user_ws = get_user_from_ws         # from auth_supabase â€” shared helper


def _format_host_for_url(host: str) -> str:
    host = (host or "").strip()
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _request_origin(request: Optional[Request]) -> str:
    scheme = "http"
    host = ""

    if request is not None:
        forwarded_proto = str(request.headers.get("x-forwarded-proto", "")).strip()
        if forwarded_proto:
            scheme = forwarded_proto.split(",")[0].strip() or scheme
        else:
            try:
                scheme = request.url.scheme or scheme
            except Exception:
                pass
        host = str(request.headers.get("x-forwarded-host", "")).strip()
        if host:
            host = host.split(",")[0].strip()
        if not host:
            host = str(request.headers.get("host", "")).strip()
        if not host:
            try:
                host = request.url.netloc or host
            except Exception:
                pass
        if not host:
            try:
                server = request.scope.get("server") or ()
                if server and server[0]:
                    server_host = _format_host_for_url(str(server[0]))
                    server_port = int(server[1]) if len(server) > 1 and server[1] else None
                    host = f"{server_host}:{server_port}" if server_port else server_host
            except Exception:
                pass

    if not host:
        host = "127.0.0.1"

    return f"{scheme}://{host}"


def _request_frontend_origin(request: Optional[Request]) -> str:
    request_origin = _request_origin(request)
    parsed = urllib.parse.urlparse(request_origin)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    return f"{scheme}://{_format_host_for_url(host)}:{FRONTEND_PORT}"


def _default_frontend_origin(request: Optional[Request] = None) -> str:
    if CONFIGURED_BROWSER_ORIGIN and not (
        request is not None and is_localish_origin(CONFIGURED_BROWSER_ORIGIN)
    ):
        return CONFIGURED_BROWSER_ORIGIN
    if request is not None:
        return _request_frontend_origin(request)
    if CONFIGURED_BROWSER_ORIGIN:
        return CONFIGURED_BROWSER_ORIGIN
    return _request_frontend_origin(request)


def _allowed_frontend_origins(request: Optional[Request] = None) -> List[str]:
    origins: List[str] = []
    if CONFIGURED_BROWSER_ORIGIN and not (
        request is not None and is_localish_origin(CONFIGURED_BROWSER_ORIGIN)
    ):
        origins.append(CONFIGURED_BROWSER_ORIGIN)
    if request is not None and (
        not CONFIGURED_BROWSER_ORIGIN or is_localish_origin(CONFIGURED_BROWSER_ORIGIN)
    ):
        derived = _request_frontend_origin(request)
        if derived not in origins:
            origins.append(derived)
    if origins:
        return origins
    if CONFIGURED_BROWSER_ORIGIN:
        return [CONFIGURED_BROWSER_ORIGIN]
    return [_request_frontend_origin(request)]


def _safe_next_url(next_url: Optional[str], request: Optional[Request] = None) -> str:
    default_origin = _default_frontend_origin(request)
    allowed_origins = {
        f"{parsed.scheme}://{parsed.netloc}"
        for parsed in (urllib.parse.urlparse(o) for o in _allowed_frontend_origins(request))
        if parsed.scheme and parsed.netloc
    }
    if not next_url:
        return default_origin
    try:
        s = str(next_url).strip()
        if not s:
            return default_origin

        # allow relative paths -> join to the configured or request-derived frontend origin
        u = urllib.parse.urlparse(s)
        if not u.scheme and not u.netloc:
            return urllib.parse.urljoin(default_origin.rstrip("/") + "/", s.lstrip("/"))

        # absolute: allow only if same origin as the configured/request-derived frontend
        if f"{u.scheme}://{u.netloc}" in allowed_origins:
            return s
        return default_origin
    except Exception:
        return default_origin


def _pick_redirect_uri_for_request(request: Request) -> str:
    if not GOOGLE_REDIRECT_URIS:
        return ""

    request_origin = _request_origin(request)
    if str(RUNTIME_CONFIG.voice_api_base_url or "").strip().startswith("/"):
        request_callback = callback_url_for_base(
            RUNTIME_CONFIG.voice_api_base_url,
            origin=request_origin,
        )
    else:
        request_callback = callback_url_for_base(request_origin)

    preferred_candidates: List[str] = []
    if CONFIGURED_CALLBACK_URI and not is_localish_origin(CONFIGURED_CALLBACK_URI):
        preferred_candidates.append(CONFIGURED_CALLBACK_URI)
    if request_callback:
        preferred_candidates.append(request_callback)
    if CONFIGURED_CALLBACK_URI and CONFIGURED_CALLBACK_URI not in preferred_candidates:
        preferred_candidates.append(CONFIGURED_CALLBACK_URI)

    for candidate in preferred_candidates:
        if candidate and candidate in GOOGLE_REDIRECT_URIS:
            return candidate

    preferred_callback = CONFIGURED_CALLBACK_URI or request_callback
    if preferred_callback:
        parsed_callback = urllib.parse.urlparse(preferred_callback)
        for ru in GOOGLE_REDIRECT_URIS:
            try:
                pu = urllib.parse.urlparse(ru)
                if (
                    pu.netloc == parsed_callback.netloc
                    and pu.path == parsed_callback.path
                ):
                    return ru
            except Exception:
                continue

    try:
        req_netloc = request.url.netloc
    except Exception:
        req_netloc = urllib.parse.urlparse(request_origin).netloc

    for ru in GOOGLE_REDIRECT_URIS:
        try:
            pu = urllib.parse.urlparse(ru)
            if pu.netloc == req_netloc:
                return ru
        except Exception:
            continue

    # fallback to first configured
    return GOOGLE_REDIRECT_URIS[0]


# -------------------------
# OAuth STATE TOKEN (signed, cookieless)
# This fixes "Invalid state (session cookie not present?)"
# -------------------------
def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _state_sig(payload_b64: str) -> str:
    mac = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(mac)


def _make_state_token(next_url: str, redirect_uri: str) -> Tuple[str, str]:
    nonce = secrets.token_urlsafe(24)
    payload = {
        "ts": int(time.time()),
        "nonce": nonce,
        "next": next_url,
        "ru": redirect_uri,
        "jti": secrets.token_urlsafe(8),
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _state_sig(payload_b64)
    return f"{payload_b64}.{sig}", nonce


def _parse_state_token(state: str, max_age_sec: int = 15 * 60) -> Optional[Dict[str, Any]]:
    try:
        if not state or "." not in state:
            return None
        payload_b64, sig = state.split(".", 1)
        if not hmac.compare_digest(_state_sig(payload_b64), sig):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        ts = int(payload.get("ts") or 0)
        if ts <= 0:
            return None
        if abs(int(time.time()) - ts) > max_age_sec:
            return None
        return payload
    except Exception:
        return None


@app.get("/auth/me")
async def auth_me(request: Request):
    """Return the current user from the Supabase JWT Bearer token."""
    user = _require_user_http(request)
    return {
        "sub": user.get("sub"),
        "email": user.get("email"),
        "role": user.get("role"),
    }


@app.post("/auth/logout")
async def auth_logout(request: Request):
    """No-op â€” Supabase sign-out is handled client-side."""
    return {"ok": True}


# -------------------------
# Google STT (lazy)
# -------------------------
_speech_client_singleton: Optional[speech.SpeechClient] = None
_speech_creds_source: Optional[str] = None


def _is_service_account_json(path: Path) -> bool:
    try:
        if not path.exists() or not path.is_file():
            return False
        if path.suffix.lower() != ".json":
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data.get("type") == "service_account") and bool(data.get("client_email"))
    except Exception:
        return False


def _build_speech_client() -> Tuple[speech.SpeechClient, str]:
    global _speech_client_singleton, _speech_creds_source

    if _speech_client_singleton is not None:
        return _speech_client_singleton, (_speech_creds_source or "unknown")

    project_root = Path(__file__).resolve().parents[1]
    candidates = google_credentials_candidates(
        "stt",
        project_root=project_root,
        environ=os.environ,
    )

    errors: List[str] = []

    for p in candidates:
        try:
            if p.exists():
                if not _is_service_account_json(p):
                    errors.append(f"{p} (not a service_account json)")
                    continue
                creds = service_account.Credentials.from_service_account_file(str(p))
                _speech_client_singleton = speech.SpeechClient(credentials=creds)
                _speech_creds_source = str(p)
                logger.info("STT using credentials: %s", _speech_creds_source)
                return _speech_client_singleton, _speech_creds_source
        except Exception as e:
            errors.append(f"{p} ({type(e).__name__}: {e})")

    try:
        _speech_client_singleton = speech.SpeechClient()
        _speech_creds_source = "ADC"
        logger.info("STT using ADC credentials")
        return _speech_client_singleton, _speech_creds_source
    except Exception as e:
        searched = "\n".join([str(c) for c in candidates if c])
        err_text = "\n".join(errors[-6:])
        keys_root = resolve_project_path(
            os.environ.get("GOOGLE_CREDENTIALS_DIR", "keys"),
            project_root,
        )
        raise RuntimeError(
            "Google STT credentials not found / not usable.\n"
            "Set GOOGLE_STT_CREDENTIALS / STT_KEY_PATH / GOOGLE_APPLICATION_CREDENTIALS,\n"
            f"or place a service-account file under {keys_root} using "
            "google-stt-service-account.json or google-service-account.json.\n"
            "Legacy local fallback names stt-service-account.json / tts-service-account.json are still supported.\n"
            f"Searched:\n{searched}\n"
            f"Notes:\n{err_text}"
        ) from e


# -------------------------
# Azure Translator
# -------------------------
try:
    from backend.translate import AzureTranslator, normalize_lang, ALLOWED_LANGS
except Exception:
    from translate import AzureTranslator, normalize_lang, ALLOWED_LANGS


_translator_singleton: Optional["AzureTranslator"] = None


def _get_translator() -> "AzureTranslator":
    global _translator_singleton
    if _translator_singleton is not None:
        return _translator_singleton
    _translator_singleton = AzureTranslator()
    return _translator_singleton


class TranslateRequest(BaseModel):
    text: str
    from_lang: Optional[str] = None
    to_lang: str
    include_source: bool = True


class TranscriptSaveRequest(BaseModel):
    client_history_id: str
    mode: str
    started_at: Optional[str] = None
    text: str


# -------------------------
# Plug in TTS router
# -------------------------
try:
    from backend.tts_backend import tts_router, tts_status
except Exception:
    from tts_backend import tts_router, tts_status

app.include_router(tts_router)

# -------------------------
# Root routes
# -------------------------
@app.get("/")
def root():
    return {
        "ok": True,
        "service": "EE NEW STT backend",
        "corsAllowOrigins": allow_origins,
        "endpoints": {
            "health": "/health",
            "auth_login": "/auth/login",
            "auth_callback": "/auth/callback",
            "auth_me": "/auth/me",
            "auth_logout": "/auth/logout",
            "translate": "/api/translate",
            "tts": "/api/tts",
            "stt_ws": "/ws/stt",
            "docs": "/docs",
        },
    }


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/health")
def health():
    try:
        _, creds_source = _build_speech_client()
        stt_ok = True
    except Exception as e:
        creds_source = str(e)
        stt_ok = False

    azure_ok = bool(read_secret_value("AZURE_TRANSLATOR_KEY"))
    tts_info = tts_status()

    return {
        "ok": True,
        "authProvider": "supabase",
        "sttConfigured": stt_ok,
        "sttCreds": creds_source,
        "azureConfigured": azure_ok,
        "ttsConfigured": bool(tts_info.get("configured")),
        "ttsCreds": tts_info.get("creds"),
    }


@app.post("/api/translate")
async def api_translate(req: TranslateRequest, request: Request):
    _require_user_http(request)

    to_norm = normalize_lang(req.to_lang)
    from_norm = normalize_lang(req.from_lang) if req.from_lang else None

    if not to_norm:
        raise HTTPException(status_code=400, detail="Missing to_lang")
    if to_norm not in ALLOWED_LANGS:
        raise HTTPException(status_code=400, detail=f"Unsupported to_lang: {to_norm}")
    if from_norm and from_norm not in ALLOWED_LANGS:
        raise HTTPException(status_code=400, detail=f"Unsupported from_lang: {from_norm}")

    text = (req.text or "").strip()

    if from_norm and from_norm == to_norm:
        return {
            "translatedText": text,
            "to": to_norm,
            "from": from_norm,
            "sourceText": text if req.include_source else None,
        }

    try:
        translator = _get_translator()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Azure Translator not configured: {e}")

    try:
        result = await translator.translate(text, to_lang=to_norm, from_lang=from_norm)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translate failed: {e}")

    return {
        "translatedText": (result.get("translatedText") or "").strip(),
        "to": result.get("to", to_norm),
        "from": result.get("from", from_norm),
        "sourceText": result.get("sourceText") if req.include_source else None,
    }


# â”€â”€ Transcript CRUD â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _require_transcript_repo():
    """Return _transcript_repo or raise 503 if Supabase is unavailable."""
    if _transcript_repo is None:
        raise HTTPException(
            status_code=503,
            detail="Transcript storage is not available",
        )
    return _transcript_repo


@app.post("/api/transcripts")
async def api_transcripts_save(req: TranscriptSaveRequest, request: Request):
    user = _require_user_http(request)
    repo = _require_transcript_repo()

    cid = (req.client_history_id or "").strip()
    mode = (req.mode or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="client_history_id is required")
    if not mode:
        raise HTTPException(status_code=400, detail="mode is required")

    row = repo.upsert(
        google_sub=user["sub"],
        client_history_id=cid,
        mode=mode,
        started_at=req.started_at,
        text=req.text,
    )
    return row


@app.get("/api/transcripts")
async def api_transcripts_list(request: Request, limit: int = 20):
    user = _require_user_http(request)
    repo = _require_transcript_repo()
    limit = max(1, min(limit, 100))
    return repo.list_by_user(google_sub=user["sub"], limit=limit)


@app.delete("/api/transcripts/{transcript_id}")
async def api_transcripts_delete(transcript_id: int, request: Request):
    user = _require_user_http(request)
    repo = _require_transcript_repo()
    deleted = repo.delete_by_id(transcript_id=transcript_id, google_sub=user["sub"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return {"deleted": True}


@app.delete("/api/transcripts")
async def api_transcripts_delete_all(request: Request):
    user = _require_user_http(request)
    repo = _require_transcript_repo()
    count = repo.delete_all_by_user(google_sub=user["sub"])
    return {"deleted": True, "count": count}


@app.websocket("/ws/stt")
async def ws_stt(ws: WebSocket):
    user = _get_user_ws(ws)
    if not user:
        await ws.close(code=4401)
        return

    await ws.accept()

    audio_q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=80)
    loop = asyncio.get_running_loop()

    configured = False
    stopped = False

    async def send(obj):
        try:
            await ws.send_text(json.dumps(obj))
        except Exception:
            pass

    def audio_only_generator() -> Iterator[speech.StreamingRecognizeRequest]:
        while True:
            chunk = audio_q.get()
            if chunk is None:
                break
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    def config_first_generator(streaming_config: speech.StreamingRecognitionConfig) -> Iterator[speech.StreamingRecognizeRequest]:
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
        yield from audio_only_generator()

    def worker(streaming_config: speech.StreamingRecognitionConfig):
        try:
            stt_client, _ = _build_speech_client()

            try:
                responses = stt_client.streaming_recognize(streaming_config, audio_only_generator())
                logger.info("STT streaming_recognize mode: (config, requests)")
            except TypeError:
                try:
                    responses = stt_client.streaming_recognize(config=streaming_config, requests=audio_only_generator())
                    logger.info("STT streaming_recognize mode: (config=..., requests=...)")
                except TypeError:
                    responses = stt_client.streaming_recognize(requests=config_first_generator(streaming_config))
                    logger.info("STT streaming_recognize mode: (requests=config-first)")

            for resp in responses:
                for result in resp.results:
                    if not result.alternatives:
                        continue
                    txt = (result.alternatives[0].transcript or "").strip()
                    if not txt:
                        continue
                    msg = {"type": "final" if result.is_final else "interim", "text": txt}
                    asyncio.run_coroutine_threadsafe(send(msg), loop)

        except Exception as e:
            logger.exception("STT worker error")
            asyncio.run_coroutine_threadsafe(send({"type": "error", "text": str(e)}), loop)

    worker_thread: Optional[threading.Thread] = None

    try:
        while True:
            msg = await ws.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            if msg.get("text") is not None:
                try:
                    obj = json.loads(msg["text"])
                except Exception:
                    continue

                if obj.get("type") == "config" and not configured:
                    configured = True

                    lang = obj.get("lang") or obj.get("languageCode") or obj.get("fromLang") or "en-US"
                    sample_rate = obj.get("sampleRate") or obj.get("sample_rate") or obj.get("sampleRateHz") or 16000
                    try:
                        sample_rate = int(sample_rate)
                    except Exception:
                        sample_rate = 16000

                    enc_str = (obj.get("encoding") or "LINEAR16").upper()
                    enc = speech.RecognitionConfig.AudioEncoding.LINEAR16
                    if hasattr(speech.RecognitionConfig.AudioEncoding, enc_str):
                        enc = getattr(speech.RecognitionConfig.AudioEncoding, enc_str)

                    config = speech.RecognitionConfig(
                        encoding=enc,
                        sample_rate_hertz=sample_rate,
                        language_code=lang,
                        enable_automatic_punctuation=True,
                    )
                    streaming_config = speech.StreamingRecognitionConfig(
                        config=config,
                        interim_results=True,
                    )

                    worker_thread = threading.Thread(target=worker, args=(streaming_config,), daemon=True)
                    worker_thread.start()

                    await send({
                        "type": "status",
                        "text": "STT stream started",
                        "lang": lang,
                        "sampleRate": sample_rate,
                        "encoding": str(enc),
                    })
                    continue

                if obj.get("type") == "stop":
                    stopped = True
                    try:
                        audio_q.put_nowait(None)
                    except Exception:
                        pass
                    await send({"type": "status", "text": "STT stream stopping"})
                    break

                continue

            data = msg.get("bytes")
            if data and configured and not stopped:
                try:
                    audio_q.put_nowait(data)
                except queue.Full:
                    pass

    finally:
        try:
            audio_q.put_nowait(None)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
