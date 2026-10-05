from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence

try:
    from backend.runtime_config import read_dotenv
except Exception:
    from runtime_config import read_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOOGLE_CREDENTIALS_DIR = "keys"

_GOOGLE_CREDENTIAL_ENV_KEYS = {
    "stt": (
        "GOOGLE_STT_CREDENTIALS",
        "STT_KEY_PATH",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_TTS_CREDENTIALS",
        "TTS_KEY_PATH",
    ),
    "tts": (
        "GOOGLE_TTS_CREDENTIALS",
        "TTS_KEY_PATH",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_STT_CREDENTIALS",
        "STT_KEY_PATH",
    ),
}

_GOOGLE_CREDENTIAL_FILENAMES = {
    "stt": (
        "google-stt-service-account.json",
        "google-service-account.json",
        "stt-service-account.json",
        "tts-service-account.json",
    ),
    "tts": (
        "google-tts-service-account.json",
        "google-service-account.json",
        "tts-service-account.json",
        "stt-service-account.json",
    ),
}


def resolve_project_path(
    value: str | Path,
    project_root: Optional[Path] = None,
) -> Path:
    root = Path(project_root or PROJECT_ROOT)
    raw = str(value or "").strip()
    if not raw:
        return root
    path = Path(raw)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def load_project_dotenv(project_root: Optional[Path] = None) -> Dict[str, str]:
    root = Path(project_root or PROJECT_ROOT)
    env: Dict[str, str] = {}
    for candidate in (root / ".env", root / "backend" / ".env"):
        env.update(read_dotenv(candidate))
    return env


def apply_project_dotenv(
    environ: Optional[MutableMapping[str, str]] = None,
    project_root: Optional[Path] = None,
) -> MutableMapping[str, str]:
    target = os.environ if environ is None else environ
    # Respect the same test-side opt-out honored by runtime_config so that
    # import-time dotenv mutation of os.environ is also suppressed.
    if str(target.get("S2T_SKIP_DOTENV", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return target
    for key, value in load_project_dotenv(project_root).items():
        target.setdefault(key, value)
    return target


def normalize_path_env(
    environ: MutableMapping[str, str],
    key: str,
    project_root: Optional[Path] = None,
) -> None:
    raw = str(environ.get(key, "")).strip()
    if not raw:
        return
    environ[key] = str(resolve_project_path(raw, project_root))


def read_secret_value(
    name: str,
    *,
    default: str = "",
    environ: Optional[Mapping[str, str]] = None,
    project_root: Optional[Path] = None,
) -> str:
    source = os.environ if environ is None else environ
    raw = str(source.get(name, "")).strip()
    if raw:
        return raw

    file_key = f"{name}_FILE"
    file_ref = str(source.get(file_key, "")).strip()
    if not file_ref:
        return default

    path = resolve_project_path(file_ref, project_root)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"{file_key} points to a missing file: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not read {file_key}: {path} ({exc})") from exc
    return value or default


def read_bool_env(
    name: str,
    *,
    default: bool = False,
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    source = os.environ if environ is None else environ
    raw = str(source.get(name, "")).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def find_first_existing_path(paths: Sequence[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def google_credentials_candidates(
    purpose: str,
    *,
    project_root: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> List[Path]:
    kind = str(purpose or "").strip().lower()
    if kind not in _GOOGLE_CREDENTIAL_ENV_KEYS:
        raise ValueError(f"Unsupported Google credential purpose: {purpose}")

    root = Path(project_root or PROJECT_ROOT)
    source = os.environ if environ is None else environ
    candidates: List[Path] = []

    for key in _GOOGLE_CREDENTIAL_ENV_KEYS[kind]:
        raw = str(source.get(key, "")).strip()
        if raw:
            candidates.append(resolve_project_path(raw, root))

    credentials_dir_raw = (
        str(source.get("GOOGLE_CREDENTIALS_DIR", DEFAULT_GOOGLE_CREDENTIALS_DIR)).strip()
        or DEFAULT_GOOGLE_CREDENTIALS_DIR
    )
    credentials_dir = resolve_project_path(credentials_dir_raw, root)
    for filename in _GOOGLE_CREDENTIAL_FILENAMES[kind]:
        candidates.append(credentials_dir / filename)

    deduped: List[Path] = []
    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped
