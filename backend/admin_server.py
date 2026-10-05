#!/usr/bin/env python3
"""
admin_server.py — Isolated admin/contact backend for Sign2Text.

Lightweight FastAPI service on port 8003 that provides:
  - Public contributor intake from the landing-page Contribute form
  - Optional best-effort contributor acknowledgement email for
    successful new lead inserts
  - Private admin endpoints for viewing contributor leads and stats
  - SQLite storage at data/admin.sqlite3 (auto-created)
  - Token-based admin protection via ADMIN_ACCESS_TOKEN env var

This service is intentionally separate from ASL / Voice / Letter
inference logic.  It can be removed without affecting the runtime.

Current public contributor intake endpoint:
  POST /api/public/contributor-leads  — public email intake from
  the landing-page Contribute form.

Usage:
  python -m backend.admin_server                 # defaults
  python -m backend.admin_server --port 8003     # explicit
"""

from __future__ import annotations

import argparse
import csv
from json import JSONDecodeError
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

try:
    from backend.browser_origin import build_cors_settings, configured_browser_origin
    from backend.runtime_config import load_runtime_config
except Exception:
    from browser_origin import build_cors_settings, configured_browser_origin
    from runtime_config import load_runtime_config

logger = logging.getLogger("admin_server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# ─── Paths ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "admin.sqlite3"
RUNTIME_CONFIG = load_runtime_config()
FRONTEND_PORT = RUNTIME_CONFIG.frontend_port
_CORS_FALLBACK_ORIGIN = configured_browser_origin(
    frontend_public_url=RUNTIME_CONFIG.frontend_public_url,
    frontend_public_url_explicit=RUNTIME_CONFIG.frontend_public_url_explicit,
    public_host=RUNTIME_CONFIG.public_host,
    public_scheme=RUNTIME_CONFIG.public_scheme,
    public_host_explicit=RUNTIME_CONFIG.public_host_explicit,
    same_origin_base_url=RUNTIME_CONFIG.admin_api_base_url,
)


def _build_cors_settings(cors_env: Optional[str] = None) -> Dict[str, Any]:
    raw = cors_env if cors_env is not None else os.environ.get("CORS_ORIGINS", "")
    return build_cors_settings(
        explicit_origins=raw,
        frontend_port=FRONTEND_PORT,
        allow_credentials=False,
        fallback_origin=_CORS_FALLBACK_ORIGIN,
        log_label="Admin CORS",
    )

# ─── Supabase storage backend (optional) ─────────────────────────────────
_supabase_lead_repo = None   # Optional[ContributorLeadRepo]
_supabase_feedback_repo = None  # Optional[FeedbackRepo]
_use_supabase_admin: bool = False


def _init_supabase_admin() -> None:
    """Attempt to initialise Supabase repos for admin persistence.

    On any failure the globals stay at their safe defaults and admin_server
    falls back to SQLite automatically.
    """
    global _supabase_lead_repo, _supabase_feedback_repo, _use_supabase_admin
    if not getattr(RUNTIME_CONFIG, "supabase_use_for_admin", False):
        return
    try:
        from backend.supabase_client import get_supabase_client
        from backend.supabase_repo import ContributorLeadRepo, FeedbackRepo

        client = get_supabase_client(RUNTIME_CONFIG)
        if client is None:
            logger.warning(
                "SUPABASE_USE_FOR_ADMIN is set but Supabase is not enabled; using SQLite"
            )
            return
        _supabase_lead_repo = ContributorLeadRepo(client)
        _supabase_feedback_repo = FeedbackRepo(client)
        _use_supabase_admin = True
        logger.info("Admin persistence: Supabase")
    except Exception:
        logger.warning(
            "Supabase admin init failed; falling back to SQLite", exc_info=True
        )


_init_supabase_admin()

# ─── Schema ───────────────────────────────────────────────────────────────
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS contributor_leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT    NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'landing_contribute',
    status     TEXT    NOT NULL DEFAULT 'new',
    notes      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
"""

CREATE_FEEDBACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL DEFAULT '',
    message    TEXT    NOT NULL DEFAULT '',
    source     TEXT    NOT NULL DEFAULT 'home',
    created_at TEXT    NOT NULL
);
"""

ALLOWED_LEAD_STATUSES = {"new", "contacted", "archived"}
MAX_LEAD_NOTES_LENGTH = 2000
MAX_FEEDBACK_MESSAGE_LENGTH = 5000


# ─── Database helpers ─────────────────────────────────────────────────────
_db_lock = threading.Lock()


def _ensure_db() -> None:
    """Create the data directory and database file if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(CREATE_FEEDBACK_TABLE_SQL)
            conn.commit()
            logger.info(f"Database ready at {DB_PATH}")
        finally:
            conn.close()


@contextmanager
def _get_db():
    """Thread-safe context manager for database connections."""
    with _db_lock:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── IP Extraction Helper ────────────────────────────────────────────────
def _extract_ip(request: Request) -> str:
    trusted_proxy_enabled = os.environ.get("TRUSTED_PROXY", "false").lower() == "true"
    ip = request.client.host if request.client else "unknown"
    if trusted_proxy_enabled:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
    return ip


# ─── Admin token validation ──────────────────────────────────────────────
_admin_token_cache: Optional[str] = None
_admin_token_configured: bool = False
_admin_token_checked: bool = False
_admin_token_failure_reason: Optional[str] = None


def _log_admin_event(
    level: int,
    *,
    action: str,
    outcome: str,
    request: Request,
    lead_id: Optional[int] = None,
    changed_fields: Optional[List[str]] = None,
    row_count: Optional[int] = None,
    reason: Optional[str] = None,
    exc_info: bool = False,
) -> None:
    parts = [
        "admin_event",
        f"action={action}",
        f"outcome={outcome}",
        f"method={request.method}",
        f"path={request.url.path}",
        f"ip={_extract_ip(request)}",
    ]
    if lead_id is not None:
        parts.append(f"lead_id={lead_id}")
    if changed_fields:
        parts.append(f"changed_fields={','.join(changed_fields)}")
    if row_count is not None:
        parts.append(f"row_count={row_count}")
    if reason:
        parts.append(f"reason={reason}")
    logger.log(level, " ".join(parts), exc_info=exc_info)


def _check_admin_token() -> None:
    global _admin_token_cache, _admin_token_configured, _admin_token_checked, _admin_token_failure_reason
    if _admin_token_checked:
        return

    token = os.environ.get("ADMIN_ACCESS_TOKEN", "").strip()
    _admin_token_checked = True
    _admin_token_cache = None
    _admin_token_configured = False
    _admin_token_failure_reason = None

    if not token:
        _admin_token_failure_reason = "missing_admin_access_token"
        logger.error("[config] ADMIN_ACCESS_TOKEN is not set. Admin access is disabled.")
        return

    lower_token = token.lower()
    if len(token) < 16 or "change_me" in lower_token or lower_token in ("admin", "password", "your_admin_token_here"):
        _admin_token_failure_reason = "insecure_admin_access_token"
        logger.error(
            f"[config] ADMIN_ACCESS_TOKEN is highly insecure (length={len(token)}). "
            "Must be at least 16 characters and not a placeholder. Admin access is disabled."
        )
        return

    _admin_token_cache = token
    _admin_token_configured = True


def _require_admin_token(request: Request, x_admin_token: Optional[str] = Header(None)) -> str:
    """Raise 401/503 if the provided token is missing, mismatched, or if config is unsafe. Returns IP."""
    _check_admin_token()

    if not _admin_token_configured:
        _log_admin_event(
            logging.ERROR,
            action="admin_access",
            outcome="rejected",
            request=request,
            reason=_admin_token_failure_reason or "insecure_server_configuration",
        )
        raise HTTPException(
            status_code=503,
            detail="Admin access is disabled due to insecure server configuration.",
        )

    ip = _extract_ip(request)
    if not x_admin_token or x_admin_token.strip() != _admin_token_cache:
        _log_admin_event(
            logging.WARNING,
            action="admin_auth",
            outcome="rejected",
            request=request,
            reason="invalid_or_missing_admin_token",
        )
        raise HTTPException(status_code=401, detail="Invalid or missing admin token.")

    return ip


# ─── FastAPI app ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_db()
    yield
    logger.info("Admin server shutdown complete")


app = FastAPI(
    title="Sign2Text Admin API",
    version="1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    **_build_cors_settings(),
)


# ─── Security Helpers ────────────────────────────────────────────────────
_rate_limit_db = {}
_rate_limit_lock = threading.Lock()

def _cleanup_rate_limits() -> None:
    """Remove entries older than 1 hour to prevent memory leaks."""
    cutoff = time.time() - 3600
    with _rate_limit_lock:
        to_remove = [
            ip for ip, data in _rate_limit_db.items() 
            if data["last_time"] < cutoff and all(t < cutoff for t in data["history"])
        ]
        for ip in to_remove:
            del _rate_limit_db[ip]

def _check_rate_limit(ip: str) -> Optional[int]:
    """
    Check if the IP has exceeded limits.
    Returns the number of seconds to wait (Retry-After) if limited, else None.
    Limits: Max 10 per hour, 10s cooldown between requests.
    """
    _cleanup_rate_limits()
    now = time.time()
    
    with _rate_limit_lock:
        data = _rate_limit_db.setdefault(ip, {"last_time": 0, "history": []})
        
        # Cooldown check (10s)
        if now - data["last_time"] < 10:
            return int(10 - (now - data["last_time"]))
            
        # Hourly limit (max 10)
        cutoff = now - 3600
        data["history"] = [t for t in data["history"] if t >= cutoff]
        
        if len(data["history"]) >= 10:
            return 3600 - int(now - data["history"][0])
            
        # OK
        data["last_time"] = now
        data["history"].append(now)
        return None

def _mask_email(email: str) -> str:
    """Masks an email for safe logging: e.g. j***e@domain.com"""
    parts = email.split("@")
    if len(parts) != 2:
        return "***@***.***"
    user, domain = parts
    if len(user) <= 2:
        user_masked = user[0] + "***"
    else:
        user_masked = user[0] + "***" + user[-1]
    return f"{user_masked}@{domain}"


def _send_contributor_autoreply(email: str) -> None:
    try:
        try:
            from backend.notifications.contributor_autoreply import (
                ContributorAutoReplyService,
            )
        except Exception:
            from notifications.contributor_autoreply import ContributorAutoReplyService

        service = ContributorAutoReplyService.from_env(project_root=PROJECT_ROOT)
        service.send_acknowledgement(email)
    except Exception as exc:
        logger.warning(
            "[contributor autoreply failed unexpectedly] Email: %s Reason: %s",
            _mask_email(email),
            type(exc).__name__,
        )


# ─── Public endpoints ────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "admin"}


@app.get("/")
async def root():
    return {
        "service": "Sign2Text Admin",
        "version": "1.0",
        "endpoints": [
            "GET  /health",
            "POST /api/public/contributor-leads",
            "GET  /api/admin/stats",
            "GET  /api/admin/contributor-leads",
            "GET  /api/admin/contributor-leads/export",
            "PATCH /api/admin/contributor-leads/{id}",
            "DELETE /api/admin/contributor-leads/{id}",
            "DELETE /api/admin/contributor-leads",
            "POST /api/public/feedback",
            "GET  /api/admin/feedback",
            "GET  /api/admin/feedback/export",
        ],
    }

class ContributorLeadCreate(BaseModel):
    email: str
    website: Optional[str] = None

class FeedbackCreate(BaseModel):
    name: Optional[str] = None
    message: Optional[str] = None
    source: Optional[str] = "home"
    website: Optional[str] = None  # honeypot

@app.post("/api/public/contributor-leads")
async def public_create_lead(
    lead: ContributorLeadCreate,
    request: Request,
    background_tasks: BackgroundTasks,
):
    # 1. IP extraction and Rate limiting
    ip = _extract_ip(request)

    retry_after = _check_rate_limit(ip)
    if retry_after is not None:
        logger.warning(f"[rate-limited request] IP: {ip}")
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please wait and try again."},
            headers={"Retry-After": str(retry_after)}
        )

    # 2. Honeypot check
    if lead.website:
        logger.warning(f"[honeypot-blocked request] IP: {ip}")
        # Return success safely without saving
        return {"status": "success", "id": -1}

    # 3. Email validation
    email = lead.email.strip().lower()
    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        logger.warning(f"[invalid email] IP: {ip} Email: {_mask_email(lead.email)}")
        raise HTTPException(status_code=400, detail="Invalid email address.")

    masked_email = _mask_email(email)
    now = _utcnow_iso()
    is_new_lead = False
    if _use_supabase_admin:
        existing = _supabase_lead_repo.find_by_email(email)
        if existing:
            logger.info(f"[duplicate submission] IP: {ip} Email: {masked_email}")
            lead_id = existing["id"]
        else:
            result = _supabase_lead_repo.insert(email, "landing_contribute")
            lead_id = result["id"]
            is_new_lead = True
            logger.info(f"[accepted submission] IP: {ip} Email: {masked_email}")
    else:
        with _get_db() as conn:
            # Check for existing email first
            existing = conn.execute(
                "SELECT id FROM contributor_leads WHERE email = ?", (email,)
            ).fetchone()

            if existing:
                # If already submitted, return success idempotently
                logger.info(f"[duplicate submission] IP: {ip} Email: {masked_email}")
                lead_id = existing[0]
            else:
                cursor = conn.execute(
                    "INSERT INTO contributor_leads (email, source, status, notes, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (email, "landing_contribute", "new", "", now, now)
                )
                lead_id = cursor.lastrowid
                is_new_lead = True
                logger.info(f"[accepted submission] IP: {ip} Email: {masked_email}")

    if is_new_lead:
        try:
            background_tasks.add_task(_send_contributor_autoreply, email)
        except Exception as exc:
            logger.warning(
                "[contributor autoreply scheduling failed] Email: %s Reason: %s",
                masked_email,
                type(exc).__name__,
            )

    return {"status": "success", "id": lead_id}


@app.post("/api/public/feedback")
async def public_create_feedback(fb: FeedbackCreate, request: Request):
    ip = _extract_ip(request)

    retry_after = _check_rate_limit(ip)
    if retry_after is not None:
        logger.warning(f"[rate-limited feedback] IP: {ip}")
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please wait and try again."},
            headers={"Retry-After": str(retry_after)},
        )

    if fb.website:
        logger.warning(f"[honeypot-blocked feedback] IP: {ip}")
        return {"status": "success", "id": -1}

    name = (fb.name or "").strip()
    message = (fb.message or "").strip()
    source = (fb.source or "home").strip() or "home"

    if not message:
        logger.info(f"[empty feedback skipped] IP: {ip}")
        return {"status": "success", "id": -1}

    if len(message) > MAX_FEEDBACK_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Message cannot exceed {MAX_FEEDBACK_MESSAGE_LENGTH} characters.",
        )

    if _use_supabase_admin:
        result = _supabase_feedback_repo.insert(name, message, source)
        feedback_id = result["id"]
    else:
        now = _utcnow_iso()
        with _get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO feedback (name, message, source, created_at) "
                "VALUES (?, ?, ?, ?)",
                (name, message, source, now),
            )
            feedback_id = cursor.lastrowid

    logger.info(f"[accepted feedback] IP: {ip} id={feedback_id}")
    return {"status": "success", "id": feedback_id}


# ─── Admin endpoints (token-protected) ───────────────────────────────────
@app.get("/api/admin/stats")
async def admin_stats(request: Request, x_admin_token: Optional[str] = Header(None)):
    _require_admin_token(request, x_admin_token)

    if _use_supabase_admin:
        total = _supabase_lead_repo.count()
        new_count = _supabase_lead_repo.count_by_status("new")
        contacted = _supabase_lead_repo.count_by_status("contacted")
        recent_7d = _supabase_lead_repo.count_recent(7)
        total_feedback = _supabase_feedback_repo.count()
        recent_feedback_7d = _supabase_feedback_repo.count_recent(7)
    else:
        with _get_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM contributor_leads"
            ).fetchone()[0]

            new_count = conn.execute(
                "SELECT COUNT(*) FROM contributor_leads WHERE status = 'new'"
            ).fetchone()[0]

            contacted = conn.execute(
                "SELECT COUNT(*) FROM contributor_leads WHERE status = 'contacted'"
            ).fetchone()[0]

            seven_days_ago = (
                datetime.now(timezone.utc) - timedelta(days=7)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            recent_7d = conn.execute(
                "SELECT COUNT(*) FROM contributor_leads WHERE created_at >= ?",
                (seven_days_ago,),
            ).fetchone()[0]

            total_feedback = conn.execute(
                "SELECT COUNT(*) FROM feedback"
            ).fetchone()[0]

            recent_feedback_7d = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE created_at >= ?",
                (seven_days_ago,),
            ).fetchone()[0]

    return {
        "total_leads": total,
        "new_leads": new_count,
        "contacted_leads": contacted,
        "recent_7d": recent_7d,
        "total_feedback": total_feedback,
        "recent_feedback_7d": recent_feedback_7d,
    }


@app.get("/api/admin/contributor-leads/export")
async def admin_export_leads(request: Request, x_admin_token: Optional[str] = Header(None)):
    _require_admin_token(request, x_admin_token)

    lead_fields = ["id", "email", "source", "status", "notes", "created_at", "updated_at"]
    try:
        if _use_supabase_admin:
            rows = _supabase_lead_repo.list_all()
        else:
            with _get_db() as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT id, email, source, status, notes, created_at, updated_at "
                        "FROM contributor_leads ORDER BY created_at DESC"
                    ).fetchall()
                ]

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(lead_fields)
        for row in rows:
            writer.writerow([row[f] for f in lead_fields])
    except HTTPException:
        raise
    except Exception:
        _log_admin_event(
            logging.ERROR,
            action="export_leads",
            outcome="failed",
            request=request,
            reason="unexpected_server_error",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error.")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"contributor_leads_{timestamp}.csv"

    _log_admin_event(
        logging.INFO,
        action="export_leads",
        outcome="success",
        request=request,
        row_count=len(rows),
    )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/api/admin/contributor-leads")
async def admin_list_leads(request: Request, x_admin_token: Optional[str] = Header(None)):
    _require_admin_token(request, x_admin_token)

    if _use_supabase_admin:
        leads = _supabase_lead_repo.list_all()
    else:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT id, email, source, status, notes, created_at, updated_at "
                "FROM contributor_leads ORDER BY created_at DESC"
            ).fetchall()
        leads = [dict(row) for row in rows]

    return {"leads": leads, "count": len(leads)}


@app.get("/api/admin/feedback/export")
async def admin_export_feedback(request: Request, x_admin_token: Optional[str] = Header(None)):
    _require_admin_token(request, x_admin_token)

    fb_fields = ["id", "name", "message", "source", "created_at"]
    try:
        if _use_supabase_admin:
            rows = _supabase_feedback_repo.list_all()
        else:
            with _get_db() as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT id, name, message, source, created_at "
                        "FROM feedback ORDER BY created_at DESC"
                    ).fetchall()
                ]

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(fb_fields)
        for row in rows:
            writer.writerow([row[f] for f in fb_fields])
    except HTTPException:
        raise
    except Exception:
        _log_admin_event(
            logging.ERROR,
            action="export_feedback",
            outcome="failed",
            request=request,
            reason="unexpected_server_error",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error.")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"feedback_{timestamp}.csv"

    _log_admin_event(
        logging.INFO,
        action="export_feedback",
        outcome="success",
        request=request,
        row_count=len(rows),
    )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/api/admin/feedback")
async def admin_list_feedback(request: Request, x_admin_token: Optional[str] = Header(None)):
    _require_admin_token(request, x_admin_token)

    if _use_supabase_admin:
        items = _supabase_feedback_repo.list_all()
    else:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, message, source, created_at "
                "FROM feedback ORDER BY created_at DESC"
            ).fetchall()
        items = [dict(row) for row in rows]

    return {"feedback": items, "count": len(items)}


@app.patch("/api/admin/contributor-leads/{lead_id}")
async def admin_update_lead(
    lead_id: int,
    request: Request,
    x_admin_token: Optional[str] = Header(None),
):
    _require_admin_token(request, x_admin_token)

    try:
        try:
            body = await request.json()
        except JSONDecodeError:
            _log_admin_event(
                logging.WARNING,
                action="update_lead",
                outcome="rejected",
                request=request,
                lead_id=lead_id,
                reason="malformed_json",
            )
            raise HTTPException(status_code=400, detail="Malformed JSON body.")

        if not isinstance(body, dict):
            _log_admin_event(
                logging.WARNING,
                action="update_lead",
                outcome="rejected",
                request=request,
                lead_id=lead_id,
                reason="request_body_must_be_json_object",
            )
            raise HTTPException(status_code=400, detail="Request body must be a JSON object.")

        updates = {}
        changed_fields: List[str] = []

        if "status" in body:
            status = body["status"]
            if status not in ALLOWED_LEAD_STATUSES:
                _log_admin_event(
                    logging.WARNING,
                    action="update_lead",
                    outcome="rejected",
                    request=request,
                    lead_id=lead_id,
                    reason="invalid_status",
                )
                raise HTTPException(
                    status_code=400,
                    detail="Invalid status. Must be one of: new, contacted, archived.",
                )
            updates["status"] = status
            changed_fields.append("status")

        if "notes" in body:
            notes = body["notes"]
            if notes is None:
                notes = ""
            elif not isinstance(notes, str):
                _log_admin_event(
                    logging.WARNING,
                    action="update_lead",
                    outcome="rejected",
                    request=request,
                    lead_id=lead_id,
                    reason="notes_must_be_string_or_null",
                )
                raise HTTPException(status_code=400, detail="Notes must be a string or null.")

            notes = notes.strip()
            if len(notes) > MAX_LEAD_NOTES_LENGTH:
                _log_admin_event(
                    logging.WARNING,
                    action="update_lead",
                    outcome="rejected",
                    request=request,
                    lead_id=lead_id,
                    reason="notes_too_long",
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Notes cannot exceed {MAX_LEAD_NOTES_LENGTH} characters.",
                )
            updates["notes"] = notes
            changed_fields.append("notes")

        if not updates:
            _log_admin_event(
                logging.WARNING,
                action="update_lead",
                outcome="rejected",
                request=request,
                lead_id=lead_id,
                reason="no_valid_fields",
            )
            raise HTTPException(
                status_code=400,
                detail="No valid fields to update. Allowed: status, notes.",
            )

        updates["updated_at"] = _utcnow_iso()

        if _use_supabase_admin:
            result = _supabase_lead_repo.update(lead_id, **updates)
            if not result:
                _log_admin_event(
                    logging.WARNING,
                    action="update_lead",
                    outcome="rejected",
                    request=request,
                    lead_id=lead_id,
                    reason="lead_not_found",
                )
                raise HTTPException(status_code=404, detail="Lead not found.")
            row = result
        else:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [lead_id]

            with _get_db() as conn:
                cursor = conn.execute(
                    f"UPDATE contributor_leads SET {set_clause} WHERE id = ?",
                    values,
                )
                if cursor.rowcount == 0:
                    _log_admin_event(
                        logging.WARNING,
                        action="update_lead",
                        outcome="rejected",
                        request=request,
                        lead_id=lead_id,
                        reason="lead_not_found",
                    )
                    raise HTTPException(status_code=404, detail="Lead not found.")

                row = dict(
                    conn.execute(
                        "SELECT id, email, source, status, notes, created_at, updated_at "
                        "FROM contributor_leads WHERE id = ?",
                        (lead_id,),
                    ).fetchone()
                )

        _log_admin_event(
            logging.INFO,
            action="update_lead",
            outcome="success",
            request=request,
            lead_id=lead_id,
            changed_fields=changed_fields,
        )
        return {"lead": row}
    except HTTPException:
        raise
    except Exception:
        _log_admin_event(
            logging.ERROR,
            action="update_lead",
            outcome="failed",
            request=request,
            lead_id=lead_id,
            reason="unexpected_server_error",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.delete("/api/admin/contributor-leads/{lead_id}")
async def admin_delete_lead(
    lead_id: int,
    request: Request,
    x_admin_token: Optional[str] = Header(None),
):
    _require_admin_token(request, x_admin_token)

    try:
        if _use_supabase_admin:
            deleted = _supabase_lead_repo.delete(lead_id)
            if not deleted:
                _log_admin_event(
                    logging.WARNING,
                    action="delete_lead",
                    outcome="rejected",
                    request=request,
                    lead_id=lead_id,
                    reason="lead_not_found",
                )
                raise HTTPException(status_code=404, detail="Lead not found.")
        else:
            with _get_db() as conn:
                cursor = conn.execute(
                    "DELETE FROM contributor_leads WHERE id = ?",
                    (lead_id,)
                )
                if cursor.rowcount == 0:
                    _log_admin_event(
                        logging.WARNING,
                        action="delete_lead",
                        outcome="rejected",
                        request=request,
                        lead_id=lead_id,
                        reason="lead_not_found",
                    )
                    raise HTTPException(status_code=404, detail="Lead not found.")
    except HTTPException:
        raise
    except Exception:
        _log_admin_event(
            logging.ERROR,
            action="delete_lead",
            outcome="failed",
            request=request,
            lead_id=lead_id,
            reason="unexpected_server_error",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error.")

    _log_admin_event(
        logging.INFO,
        action="delete_lead",
        outcome="success",
        request=request,
        lead_id=lead_id,
        row_count=1,
    )
    return {"status": "success", "detail": f"Lead {lead_id} deleted."}


@app.delete("/api/admin/contributor-leads")
async def admin_delete_all_leads(
    request: Request,
    x_admin_token: Optional[str] = Header(None),
):
    _require_admin_token(request, x_admin_token)

    try:
        if _use_supabase_admin:
            count = _supabase_lead_repo.delete_all()
        else:
            with _get_db() as conn:
                cursor = conn.execute("DELETE FROM contributor_leads")
                count = cursor.rowcount
    except HTTPException:
        raise
    except Exception:
        _log_admin_event(
            logging.ERROR,
            action="delete_all_leads",
            outcome="failed",
            request=request,
            reason="unexpected_server_error",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error.")

    _log_admin_event(
        logging.INFO,
        action="delete_all_leads",
        outcome="success",
        request=request,
        row_count=count,
    )
    return {"status": "success", "detail": f"All {count} leads deleted."}


# ─── CLI entry point ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Sign2Text Admin Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8003, help="Bind port")
    args = parser.parse_args()

    logger.info(f"Starting Admin server on {args.host}:{args.port}")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
