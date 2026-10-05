from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from backend.hardware import HardwareReport, detect_runtime_profile, log_hardware_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ASL_API_PORT = 8000
DEFAULT_VOICE_API_PORT = 8001
DEFAULT_LETTER_API_PORT = 8002
DEFAULT_ADMIN_API_PORT = 8003
DEFAULT_FRONTEND_PORT = 5500
DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_PUBLIC_HOST = "127.0.0.1"
DEFAULT_PUBLIC_SCHEME = "http"
DEFAULT_SERVICE_TIMEOUT_SEC = 30.0
DEFAULT_ASL_CHECKPOINT_PATH = (
    "backend/checkpoint/popsign_aslcitizen_semlex6_gcn_big/best_model.pt"
)
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b-instruct"
DEFAULT_OLLAMA_TIMEOUT_SEC = 30.0
DEFAULT_SUPABASE_SCHEMA = "public"
DEFAULT_SUPABASE_TIMEOUT_SEC = 10.0


def read_dotenv(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Strip trailing inline comment (` #`) for unquoted values,
        # matching the bash-side parsing in start_server.sh / setup_runpod.sh.
        if not (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            # Empty value followed by comment (e.g. `KEY=  # comment`)
            if value.startswith("#"):
                value = ""
            else:
                comment_pos = value.find(" #")
                if comment_pos >= 0:
                    value = value[:comment_pos].rstrip()
        value = value.strip('"').strip("'")
        if key:
            env[key] = value
    return env


def _load_runtime_env(
    project_root: Path, environ: Optional[Mapping[str, str]] = None
) -> Dict[str, str]:
    env: Dict[str, str] = {}
    source = os.environ if environ is None else environ
    # Allow the caller (e.g. the test suite) to opt out of .env loading so that
    # deployment-style values in a developer's local .env cannot leak into
    # module-level config and make tests non-deterministic.
    skip_dotenv = str(source.get("S2T_SKIP_DOTENV", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not skip_dotenv:
        for candidate in (project_root / ".env", project_root / "backend" / ".env"):
            env.update(read_dotenv(candidate))
    env.update({str(k): str(v) for k, v in source.items()})
    return env


def _read_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = str(env.get(key, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _read_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = str(env.get(key, "")).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_TRUE_BOOL_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_BOOL_VALUES = frozenset({"0", "false", "no", "off"})


def _read_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = str(env.get(key, "")).strip().lower()
    if not raw:
        return default
    if raw in _TRUE_BOOL_VALUES:
        return True
    if raw in _FALSE_BOOL_VALUES:
        return False
    return default


def _normalize_base_url(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw == "/":
        return "/"
    return raw.rstrip("/")


def _resolve_path(project_root: Path, value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Accept Windows-style backslashes from .env on POSIX runners (CI).
    path = Path(raw.replace("\\", "/"))
    if not path.is_absolute():
        path = (project_root / path).resolve()
    return str(path)


def _build_origin(scheme: str, host: str, port: int) -> str:
    return f"{scheme}://{host}:{port}"


def _derive_ws_url(http_base: str) -> str:
    return http_base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def _join_url_path(base: str, path: str) -> str:
    normalized_base = _normalize_base_url(base)
    normalized_path = "/" + str(path or "").lstrip("/")
    if not normalized_base or normalized_base == "/":
        return normalized_path
    return f"{normalized_base}{normalized_path}"


@dataclass(frozen=True)
class RuntimeConfig:
    project_root: Path
    env: Dict[str, str]
    asl_bind_host: str
    asl_port: int
    voice_bind_host: str
    voice_port: int
    letter_bind_host: str
    letter_port: int
    frontend_bind_host: str
    frontend_port: int
    public_host: str
    public_host_explicit: bool
    public_scheme: str
    frontend_public_url: str
    frontend_public_url_explicit: bool
    asl_api_base_url: str
    asl_api_base_url_explicit: bool
    voice_api_base_url: str
    voice_api_base_url_explicit: bool
    letter_api_base_url: str
    letter_api_base_url_explicit: bool
    admin_port: int
    admin_api_base_url: str
    admin_api_base_url_explicit: bool
    voice_ws_url: str
    voice_ws_url_explicit: bool
    asl_checkpoint_path: str
    ollama_url: str
    ollama_model: str
    ollama_timeout_sec: float
    ollama_auth_token: Optional[str]
    ollama_mode: str
    ollama_asl_enabled: bool
    service_timeout_sec: float
    hardware_profile: str
    hardware_report: HardwareReport
    supabase_enabled: bool
    supabase_url: str
    supabase_anon_key: str
    supabase_jwks_url: str
    supabase_service_role_key: str
    supabase_schema: str
    supabase_timeout_sec: float
    supabase_use_for_admin: bool
    keypoints_stream_enabled: bool
    # ── Our (MediaPipe-86 + curated604) pipeline selector ──
    asl_pipeline: str  # "friend" (default) | "ours"
    our_model_checkpoint: str
    our_model_config: str
    our_model_vocab: str
    our_seg_preset: str
    our_synfold: bool
    our_refine_conf: float
    our_blocked_labels: tuple
    # Sliding-cover v3 path (apr29). When our_sliding_mode=True, OursPipelineAdapter
    # uses SlidingLiveEmitter from sliding_inference.py instead of LiveStreamingSpotter.
    our_sliding_mode: bool

    @property
    def frontend_launch_url(self) -> str:
        landing_index = self.project_root / "landing" / "dist" / "index.html"
        if landing_index.exists():
            return f"{self.frontend_public_url}/"
        return f"{self.frontend_public_url}/index.html"

    @property
    def frontend_app_config(self) -> Dict[str, object]:
        app_config: Dict[str, object] = {}

        if self.voice_api_base_url_explicit or self.public_host_explicit:
            app_config["BACKEND"] = self.voice_api_base_url
        elif self.voice_port != DEFAULT_VOICE_API_PORT:
            app_config["VOICE_PORT"] = self.voice_port

        if self.asl_api_base_url_explicit or self.public_host_explicit:
            app_config["ASL_BACKEND"] = self.asl_api_base_url
        elif self.asl_port != DEFAULT_ASL_API_PORT:
            app_config["ASL_PORT"] = self.asl_port

        if self.letter_api_base_url_explicit or self.public_host_explicit:
            app_config["LETTER_BACKEND"] = self.letter_api_base_url
        elif self.letter_port != DEFAULT_LETTER_API_PORT:
            app_config["LETTER_PORT"] = self.letter_port

        if self.admin_api_base_url_explicit:
            app_config["ADMIN_BACKEND"] = self.admin_api_base_url
        elif self.admin_port != DEFAULT_ADMIN_API_PORT:
            app_config["ADMIN_PORT"] = self.admin_port

        if self.voice_ws_url_explicit:
            app_config["BACKEND_WS_URL"] = self.voice_ws_url

        if self.supabase_enabled:
            if self.supabase_url:
                app_config["SUPABASE_URL"] = self.supabase_url
            if self.supabase_anon_key:
                app_config["SUPABASE_ANON_KEY"] = self.supabase_anon_key
        app_config["KEYPOINTS_ENABLED"] = self.keypoints_stream_enabled

        return app_config


def load_runtime_config(
    project_root: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None
) -> RuntimeConfig:
    root = Path(project_root or PROJECT_ROOT)
    env = _load_runtime_env(root, environ)

    asl_bind_host = str(env.get("ASL_BIND_HOST", DEFAULT_BIND_HOST)).strip() or DEFAULT_BIND_HOST
    voice_bind_host = str(env.get("VOICE_BIND_HOST", DEFAULT_BIND_HOST)).strip() or DEFAULT_BIND_HOST
    letter_bind_host = str(env.get("LETTER_BIND_HOST", DEFAULT_BIND_HOST)).strip() or DEFAULT_BIND_HOST
    frontend_bind_host = (
        str(env.get("FRONTEND_BIND_HOST", DEFAULT_BIND_HOST)).strip() or DEFAULT_BIND_HOST
    )

    asl_port = _read_int(env, "ASL_API_PORT", DEFAULT_ASL_API_PORT)
    voice_port = _read_int(env, "VOICE_API_PORT", DEFAULT_VOICE_API_PORT)
    letter_port = _read_int(env, "LETTER_API_PORT", DEFAULT_LETTER_API_PORT)
    admin_port = _read_int(env, "ADMIN_API_PORT", DEFAULT_ADMIN_API_PORT)
    frontend_port = _read_int(env, "FRONTEND_PORT", DEFAULT_FRONTEND_PORT)

    public_host_raw = str(env.get("APP_PUBLIC_HOST", "")).strip()
    public_host = public_host_raw or DEFAULT_PUBLIC_HOST
    public_host_explicit = bool(public_host_raw)
    public_scheme = (
        str(env.get("APP_PUBLIC_SCHEME", DEFAULT_PUBLIC_SCHEME)).strip().lower()
        or DEFAULT_PUBLIC_SCHEME
    )

    frontend_public_url_raw = _normalize_base_url(env.get("FRONTEND_PUBLIC_URL"))
    frontend_public_url_explicit = bool(frontend_public_url_raw)
    frontend_public_url = (
        frontend_public_url_raw
        or _build_origin(public_scheme, public_host, frontend_port)
    )

    asl_api_base_url_raw = _normalize_base_url(env.get("ASL_API_BASE_URL"))
    asl_api_base_url_explicit = bool(asl_api_base_url_raw)
    asl_api_base_url = (
        asl_api_base_url_raw
        or _build_origin(public_scheme, public_host, asl_port)
    )

    voice_api_base_url_raw = _normalize_base_url(env.get("VOICE_API_BASE_URL"))
    voice_api_base_url_explicit = bool(voice_api_base_url_raw)
    voice_api_base_url = (
        voice_api_base_url_raw
        or _build_origin(public_scheme, public_host, voice_port)
    )

    letter_api_base_url_raw = _normalize_base_url(env.get("LETTER_API_BASE_URL"))
    letter_api_base_url_explicit = bool(letter_api_base_url_raw)
    letter_api_base_url = (
        letter_api_base_url_raw
        or _build_origin(public_scheme, public_host, letter_port)
    )

    admin_api_base_url_raw = _normalize_base_url(env.get("ADMIN_API_BASE_URL"))
    admin_api_base_url_explicit = bool(admin_api_base_url_raw)
    admin_api_base_url = (
        admin_api_base_url_raw
        or _build_origin(public_scheme, public_host, admin_port)
    )

    voice_ws_url_raw = _normalize_base_url(env.get("VOICE_WS_URL"))
    voice_ws_url_explicit = bool(voice_ws_url_raw)
    voice_ws_url = voice_ws_url_raw or _join_url_path(
        _derive_ws_url(voice_api_base_url),
        "/ws/stt",
    )

    checkpoint_raw = str(env.get("ASL_CHECKPOINT_PATH", "")).strip()
    asl_checkpoint_path = _resolve_path(
        root,
        checkpoint_raw or DEFAULT_ASL_CHECKPOINT_PATH,
    )

    ollama_url = _normalize_base_url(env.get("OLLAMA_URL")) or DEFAULT_OLLAMA_URL
    ollama_model = (
        str(env.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)).strip()
        or DEFAULT_OLLAMA_MODEL
    )
    ollama_timeout_sec = _read_float(
        env,
        "OLLAMA_TIMEOUT_SEC",
        DEFAULT_OLLAMA_TIMEOUT_SEC,
    )
    ollama_auth_token = str(env.get("OLLAMA_AUTH_TOKEN", "")).strip() or None
    ollama_mode = str(env.get("OLLAMA_MODE", "local")).strip().lower() or "local"
    ollama_asl_enabled = _read_bool(env, "OLLAMA_ASL_ENABLED", True)

    _sb_enabled_raw = str(env.get("SUPABASE_ENABLED", "")).strip().lower()
    supabase_enabled = _sb_enabled_raw in {"1", "true", "yes", "on"}
    supabase_url = str(env.get("SUPABASE_URL", "")).strip()
    supabase_anon_key = str(env.get("SUPABASE_ANON_KEY", "")).strip()
    _jwks_raw = str(env.get("SUPABASE_JWKS_URL", "")).strip()
    supabase_jwks_url = _jwks_raw or (f"{supabase_url.rstrip('/')}/auth/v1/jwks" if supabase_url else "")
    supabase_service_role_key = str(env.get("SUPABASE_SERVICE_ROLE_KEY", "")).strip()
    supabase_schema = (
        str(env.get("SUPABASE_SCHEMA", DEFAULT_SUPABASE_SCHEMA)).strip()
        or DEFAULT_SUPABASE_SCHEMA
    )
    supabase_timeout_sec = _read_float(
        env, "SUPABASE_TIMEOUT_SEC", DEFAULT_SUPABASE_TIMEOUT_SEC
    )
    _sb_admin_raw = str(env.get("SUPABASE_USE_FOR_ADMIN", "")).strip().lower()
    supabase_use_for_admin = _sb_admin_raw in {"1", "true", "yes", "on"}
    keypoints_stream_enabled = _read_bool(env, "KEYPOINTS_STREAM_ENABLED", False)

    asl_pipeline = (str(env.get("ASL_PIPELINE", "friend")).strip().lower() or "friend")
    if asl_pipeline not in ("friend", "ours"):
        asl_pipeline = "friend"
    our_model_checkpoint = _resolve_path(root, str(env.get("OUR_MODEL_CHECKPOINT", "")).strip())
    our_model_config = _resolve_path(root, str(env.get("OUR_MODEL_CONFIG", "")).strip())
    our_model_vocab = _resolve_path(root, str(env.get("OUR_MODEL_VOCAB", "")).strip())
    our_seg_preset = str(env.get("OUR_SEG_PRESET", "")).strip()
    our_synfold = _read_bool(env, "OUR_SYNFOLD", True)
    our_refine_conf = _read_float(env, "OUR_REFINE_CONF", 0.10)
    our_sliding_mode = _read_bool(env, "OUR_SLIDING_MODE", False)
    our_blocked_labels = tuple(
        l.strip().upper()
        for l in str(env.get("OUR_BLOCKED_LABELS", "")).split(",")
        if l.strip()
    )

    service_timeout_sec = _read_float(
        env,
        "SERVICE_TIMEOUT_SEC",
        DEFAULT_SERVICE_TIMEOUT_SEC,
    )

    hw_report = detect_runtime_profile()

    return RuntimeConfig(
        project_root=root,
        env=env,
        asl_bind_host=asl_bind_host,
        asl_port=asl_port,
        voice_bind_host=voice_bind_host,
        voice_port=voice_port,
        letter_bind_host=letter_bind_host,
        letter_port=letter_port,
        frontend_bind_host=frontend_bind_host,
        frontend_port=frontend_port,
        public_host=public_host,
        public_host_explicit=public_host_explicit,
        public_scheme=public_scheme,
        frontend_public_url=frontend_public_url,
        frontend_public_url_explicit=frontend_public_url_explicit,
        asl_api_base_url=asl_api_base_url,
        asl_api_base_url_explicit=asl_api_base_url_explicit,
        voice_api_base_url=voice_api_base_url,
        voice_api_base_url_explicit=voice_api_base_url_explicit,
        letter_api_base_url=letter_api_base_url,
        letter_api_base_url_explicit=letter_api_base_url_explicit,
        admin_port=admin_port,
        admin_api_base_url=admin_api_base_url,
        admin_api_base_url_explicit=admin_api_base_url_explicit,
        voice_ws_url=voice_ws_url,
        voice_ws_url_explicit=voice_ws_url_explicit,
        asl_checkpoint_path=asl_checkpoint_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        ollama_timeout_sec=ollama_timeout_sec,
        ollama_auth_token=ollama_auth_token,
        ollama_mode=ollama_mode,
        ollama_asl_enabled=ollama_asl_enabled,
        service_timeout_sec=service_timeout_sec,
        hardware_profile=hw_report.profile,
        hardware_report=hw_report,
        supabase_enabled=supabase_enabled,
        supabase_url=supabase_url,
        supabase_anon_key=supabase_anon_key,
        supabase_jwks_url=supabase_jwks_url,
        supabase_service_role_key=supabase_service_role_key,
        supabase_schema=supabase_schema,
        supabase_timeout_sec=supabase_timeout_sec,
        supabase_use_for_admin=supabase_use_for_admin,
        keypoints_stream_enabled=keypoints_stream_enabled,
        asl_pipeline=asl_pipeline,
        our_model_checkpoint=our_model_checkpoint,
        our_model_config=our_model_config,
        our_model_vocab=our_model_vocab,
        our_seg_preset=our_seg_preset,
        our_synfold=our_synfold,
        our_refine_conf=our_refine_conf,
        our_blocked_labels=our_blocked_labels,
        our_sliding_mode=our_sliding_mode,
    )


def render_frontend_runtime_config_js(config: RuntimeConfig) -> str:
    payload = json.dumps(config.frontend_app_config, indent=2, sort_keys=True)
    return (
        "// Auto-generated by start.py to keep the frontend aligned with runtime settings.\n"
        f"window.APP_CONFIG = Object.assign(window.APP_CONFIG || {{}}, {payload});\n"
    )


def write_frontend_runtime_config_js(path: Path, config: RuntimeConfig) -> None:
    path.write_text(render_frontend_runtime_config_js(config), encoding="utf-8")
