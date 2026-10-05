import os
import json
import logging
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

try:
  from google.cloud import texttospeech
  from google.oauth2 import service_account
except Exception:
  texttospeech = None
  service_account = None

try:
  from backend.secret_config import apply_project_dotenv, google_credentials_candidates, resolve_project_path
except Exception:
  from secret_config import apply_project_dotenv, google_credentials_candidates, resolve_project_path

logger = logging.getLogger("tts_backend")

apply_project_dotenv()

tts_router = APIRouter()

# -------------------------
# Import language helpers (same as translate)
# -------------------------
try:
  from backend.translate import normalize_lang, ALLOWED_LANGS
except Exception:
  from translate import normalize_lang, ALLOWED_LANGS

# -------------------------
# Google TTS client (lazy)
# -------------------------
_tts_client_singleton: Optional["texttospeech.TextToSpeechClient"] = None
_tts_creds_source: Optional[str] = None

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

def _find_tts_creds_candidates() -> List[Path]:
  return google_credentials_candidates(
    "tts",
    project_root=Path(__file__).resolve().parents[1],
    environ=os.environ,
  )

def _get_tts_client() -> Tuple["texttospeech.TextToSpeechClient", str]:
  global _tts_client_singleton, _tts_creds_source

  if texttospeech is None or service_account is None:
    raise RuntimeError(
      "google-cloud-texttospeech is not installed. "
      "Run: pip install google-cloud-texttospeech"
    )

  if _tts_client_singleton is not None:
    return _tts_client_singleton, (_tts_creds_source or "unknown")

  candidates = _find_tts_creds_candidates()
  errors: List[str] = []

  for p in candidates:
    try:
      if p.exists():
        if not _is_service_account_json(p):
          errors.append(f"{p} (not a service_account json)")
          continue

        creds = service_account.Credentials.from_service_account_file(str(p))
        _tts_client_singleton = texttospeech.TextToSpeechClient(credentials=creds)
        _tts_creds_source = str(p)
        logger.info("TTS using credentials: %s", _tts_creds_source)
        return _tts_client_singleton, _tts_creds_source
    except Exception as e:
      errors.append(f"{p} ({type(e).__name__}: {e})")

  # last resort: ADC
  try:
    _tts_client_singleton = texttospeech.TextToSpeechClient()
    _tts_creds_source = "ADC"
    logger.info("TTS using ADC credentials")
    return _tts_client_singleton, _tts_creds_source
  except Exception as e:
    searched = "\n".join([str(c) for c in candidates if c])
    err_text = "\n".join(errors[-6:])
    keys_root = resolve_project_path(
      os.environ.get("GOOGLE_CREDENTIALS_DIR", "keys"),
      Path(__file__).resolve().parents[1],
    )
    raise RuntimeError(
      "Google TTS credentials not found / not usable.\n"
      "Set TTS_KEY_PATH / GOOGLE_TTS_CREDENTIALS / GOOGLE_APPLICATION_CREDENTIALS,\n"
      f"or place a service-account file under {keys_root} using "
      "google-tts-service-account.json or google-service-account.json.\n"
      "Legacy local fallback names tts-service-account.json / stt-service-account.json are still supported.\n"
      f"Searched:\n{searched}\n"
      f"Notes:\n{err_text}"
    ) from e

def tts_status() -> dict:
  if texttospeech is None:
    return {"configured": False, "creds": "missing google-cloud-texttospeech"}
  candidates = _find_tts_creds_candidates()
  for p in candidates:
    if p.exists() and _is_service_account_json(p):
      return {"configured": True, "creds": str(p)}
  return {"configured": bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")), "creds": "ADC/unknown"}

# -------------------------
# Voice mapping
# -------------------------
# Keep mapping by BASE language, but accept locale inputs like "es-ES" / "en-US".
VOICE_MAP: Dict[str, Dict[str, Any]] = {
  "en": {"language_code": "en-US", "name": "en-US-Standard-C"},
  "es": {"language_code": "es-ES", "name": "es-ES-Standard-A"},
  # Google TTS commonly uses ar-XA for Arabic voices (rather than ar-SA)
  "ar": {"language_code": "ar-XA", "name": "ar-XA-Standard-A"},
  # Armenian: best-effort (voice name may vary by project/region availability)
  "hy": {"language_code": "hy-AM", "name": None},
}

def _normalize_locale(s: str) -> str:
  # "es_es" -> "es-ES", "EN-us" -> "en-US"
  s = (s or "").strip()
  if not s:
    return ""
  s = s.replace("_", "-")
  parts = [p for p in s.split("-") if p]
  if not parts:
    return ""
  if len(parts) == 1:
    return parts[0].lower()
  return f"{parts[0].lower()}-{parts[1].upper()}"

def _pick_base_and_locale(raw: str) -> Tuple[str, str]:
  loc = _normalize_locale(raw)
  if not loc:
    return "en", "en-US"
  base = loc.split("-")[0].lower()
  base_norm = normalize_lang(base) or base
  # if base is not one we support, still return it (best-effort)
  return base_norm, loc

class TTSRequest(BaseModel):
  text: str
  lang: str  # accepts "es", "en", "ar", "hy" OR locales like "es-ES", "en-US"

@tts_router.post("/api/tts")
async def api_tts(req: TTSRequest):
  text = (req.text or "").strip()
  if not text:
    raise HTTPException(status_code=400, detail="Missing text")

  if len(text) > 3000:
    raise HTTPException(status_code=400, detail="Text too long (max 3000 chars)")

  base, locale = _pick_base_and_locale(req.lang)

  # If your translate.py restricts languages, keep TTS aligned with it (base codes)
  # But still allow locale inputs by validating the base.
  if base and isinstance(ALLOWED_LANGS, (set, list, tuple)):
    if base not in set(ALLOWED_LANGS):
      # Best-effort fallback: speak with English instead of failing hard
      base = "en"
      locale = "en-US"

  info = VOICE_MAP.get(base) or VOICE_MAP.get("en")

  # Prefer mapped language_code; if missing, fall back to locale; if still empty, en-US.
  language_code = (info.get("language_code") if info else None) or (locale or "en-US")
  voice_name = info.get("name") if info else None

  try:
    client, src = _get_tts_client()
  except Exception as e:
    logger.exception("TTS client init failed")
    raise HTTPException(status_code=500, detail=f"TTS not configured: {e}")

  synthesis_input = texttospeech.SynthesisInput(text=text)

  def _make_voice(name: Optional[str]):
    if name:
      return texttospeech.VoiceSelectionParams(language_code=language_code, name=name)
    return texttospeech.VoiceSelectionParams(language_code=language_code)

  audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)

  try:
    try:
      resp = client.synthesize_speech(
        input=synthesis_input,
        voice=_make_voice(voice_name),
        audio_config=audio_config,
      )
    except Exception:
      # fallback: no specific voice name (lets Google choose an available voice)
      resp = client.synthesize_speech(
        input=synthesis_input,
        voice=_make_voice(None),
        audio_config=audio_config,
      )
  except Exception as e:
    logger.exception("TTS synth failed")
    raise HTTPException(
      status_code=500,
      detail=f"TTS synth failed: {e} (creds={src}, base={base}, lang={language_code}, req={req.lang})",
    )

  audio_bytes = resp.audio_content or b""
  if not audio_bytes:
    raise HTTPException(status_code=500, detail="TTS returned empty audio")

  return Response(
    content=audio_bytes,
    media_type="audio/mpeg",
    headers={"Cache-Control": "no-store"},
  )
