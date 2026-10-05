import os
import json
import uuid
import asyncio
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional, Dict, Any

try:
    from backend.secret_config import apply_project_dotenv, read_secret_value
except Exception:
    from secret_config import apply_project_dotenv, read_secret_value


ALLOWED_LANGS = {"en", "es", "ar", "hy"}

LANG_NAME_MAP = {
    "english": "en",
    "spanish": "es",
    "arabic": "ar",
    "armenian": "hy",
}


def normalize_lang(lang: Optional[str]) -> Optional[str]:
    if not lang:
        return None
    s = str(lang).strip().lower()

    if s in LANG_NAME_MAP:
        s = LANG_NAME_MAP[s]

    if "-" in s:
        s = s.split("-", 1)[0].strip()

    return s or None


apply_project_dotenv()


class AzureTranslator:
    def __init__(self):
        self.endpoint = os.getenv(
            "AZURE_TRANSLATOR_ENDPOINT",
            "https://api.cognitive.microsofttranslator.com",
        ).rstrip("/")
        self.region = os.getenv("AZURE_TRANSLATOR_REGION", "westus3")
        self.key = read_secret_value("AZURE_TRANSLATOR_KEY")

        timeout_s = os.getenv("AZURE_TRANSLATOR_TIMEOUT_SEC", "8")
        try:
            self.timeout_sec = float(timeout_s)
        except Exception:
            self.timeout_sec = 8.0

        sem = os.getenv("AZURE_TRANSLATOR_MAX_CONCURRENCY", "5")
        try:
            self._sem = asyncio.Semaphore(int(sem))
        except Exception:
            self._sem = asyncio.Semaphore(5)

        if not self.key:
            raise RuntimeError(
                "AZURE_TRANSLATOR_KEY is missing "
                "(set AZURE_TRANSLATOR_KEY or AZURE_TRANSLATOR_KEY_FILE)."
            )

    async def translate(self, text: str, to_lang: str, from_lang: Optional[str] = None) -> Dict[str, Any]:
        text = (text or "").strip()
        if text == "":
            return {"translatedText": "", "to": to_lang, "from": from_lang, "sourceText": ""}

        to_norm = normalize_lang(to_lang)
        from_norm = normalize_lang(from_lang) if from_lang else None

        if not to_norm:
            raise ValueError("Missing to_lang")
        if to_norm not in ALLOWED_LANGS:
            raise ValueError(f"Unsupported to_lang: {to_norm}")
        if from_norm and from_norm not in ALLOWED_LANGS:
            raise ValueError(f"Unsupported from_lang: {from_norm}")

        if from_norm and from_norm == to_norm:
            return {"translatedText": text, "to": to_norm, "from": from_norm, "sourceText": text}

        async with self._sem:
            return await asyncio.to_thread(self._translate_sync, text, to_norm, from_norm)

    def _translate_sync(self, text: str, to_norm: str, from_norm: Optional[str]) -> Dict[str, Any]:
        query = {"api-version": "3.0", "to": to_norm}
        if from_norm:
            query["from"] = from_norm

        url = f"{self.endpoint}/translate?{urllib.parse.urlencode(query)}"
        body = json.dumps([{"Text": text}], ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url=url,
            data=body,
            method="POST",
            headers={
                "Ocp-Apim-Subscription-Key": self.key,
                "Ocp-Apim-Subscription-Region": self.region,
                "Content-Type": "application/json; charset=UTF-8",
                "X-ClientTraceId": str(uuid.uuid4()),
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(f"Azure HTTP {e.code}: {err_body or e.reason}") from e
        except Exception as e:
            raise RuntimeError(f"Azure request failed: {e}") from e

        data = json.loads(raw)

        translated = ""
        detected_from = None

        if isinstance(data, list) and data and isinstance(data[0], dict):
            item0 = data[0]
            det = item0.get("detectedLanguage")
            if isinstance(det, dict):
                detected_from = det.get("language")

            translations = item0.get("translations")
            if isinstance(translations, list) and translations and isinstance(translations[0], dict):
                translated = translations[0].get("text", "") or ""

        return {
            "translatedText": translated,
            "to": to_norm,
            "from": from_norm or detected_from,
            "sourceText": text,
        }
