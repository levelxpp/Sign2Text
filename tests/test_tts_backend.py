from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeTextToSpeech:
    class AudioEncoding:
        MP3 = "MP3"

    class SynthesisInput:
        def __init__(self, text: str):
            self.text = text

    class VoiceSelectionParams:
        def __init__(self, language_code: str, name: str | None = None):
            self.language_code = language_code
            self.name = name

    class AudioConfig:
        def __init__(self, audio_encoding: str):
            self.audio_encoding = audio_encoding


class RecordingTtsClient:
    def __init__(self, *, audio: bytes = b"audio-bytes", fail_first: bool = False):
        self.audio = audio
        self.fail_first = fail_first
        self.calls = []

    def synthesize_speech(self, *, input, voice, audio_config):
        self.calls.append(
            {
                "text": input.text,
                "language_code": voice.language_code,
                "name": voice.name,
                "audio_encoding": audio_config.audio_encoding,
            }
        )
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("preferred voice unavailable")
        return SimpleNamespace(audio_content=self.audio)


def test_is_service_account_json_detects_valid_files(tmp_path, load_module):
    tts_backend = load_module("tts_backend")
    good = tmp_path / "good.json"
    bad = tmp_path / "bad.json"
    good.write_text(
        json.dumps({"type": "service_account", "client_email": "svc@example.com"}),
        encoding="utf-8",
    )
    bad.write_text(json.dumps({"type": "user"}), encoding="utf-8")

    assert tts_backend._is_service_account_json(good) is True
    assert tts_backend._is_service_account_json(bad) is False


def test_find_tts_creds_candidates_deduplicates_environment_paths(
    tmp_path, monkeypatch, load_module
):
    tts_backend = load_module("tts_backend")
    shared = tmp_path / "tts.json"

    for key in (
        "TTS_KEY_PATH",
        "GOOGLE_TTS_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_STT_CREDENTIALS",
        "STT_KEY_PATH",
    ):
        monkeypatch.setenv(key, str(shared))

    candidates = [str(path) for path in tts_backend._find_tts_creds_candidates()]

    assert candidates.count(str(shared)) == 1
    assert candidates[0] == str(shared)


def test_find_tts_creds_candidates_includes_generic_then_legacy_key_names(
    monkeypatch, load_module
):
    tts_backend = load_module("tts_backend")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_DIR", "mounted-secrets")

    candidates = [
        str(path)
        for path in tts_backend._find_tts_creds_candidates()
        if "mounted-secrets" in str(path)
    ]

    assert candidates[:4] == [
        str((Path(tts_backend.__file__).resolve().parents[1] / "mounted-secrets" / "google-tts-service-account.json").resolve()),
        str((Path(tts_backend.__file__).resolve().parents[1] / "mounted-secrets" / "google-service-account.json").resolve()),
        str((Path(tts_backend.__file__).resolve().parents[1] / "mounted-secrets" / "tts-service-account.json").resolve()),
        str((Path(tts_backend.__file__).resolve().parents[1] / "mounted-secrets" / "stt-service-account.json").resolve()),
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("es_es", "es-ES"),
        ("EN-us", "en-US"),
        ("hy", "hy"),
        ("", ""),
    ],
)
def test_normalize_locale_formats_locale_strings(raw, expected, load_module):
    tts_backend = load_module("tts_backend")
    assert tts_backend._normalize_locale(raw) == expected


def test_pick_base_and_locale_normalizes_to_supported_base(load_module):
    tts_backend = load_module("tts_backend")
    assert tts_backend._pick_base_and_locale("es_MX") == ("es", "es-MX")
    assert tts_backend._pick_base_and_locale("") == ("en", "en-US")


def test_api_tts_falls_back_to_english_and_retries_without_voice_name(
    monkeypatch, load_module
):
    tts_backend = load_module("tts_backend")
    client = RecordingTtsClient(fail_first=True)
    monkeypatch.setattr(tts_backend, "texttospeech", FakeTextToSpeech)
    monkeypatch.setattr(tts_backend, "_get_tts_client", lambda: (client, "test-creds"))

    response = asyncio.run(
        tts_backend.api_tts(tts_backend.TTSRequest(text="Hello there", lang="pt-BR"))
    )

    assert response.body == b"audio-bytes"
    assert response.media_type == "audio/mpeg"
    assert client.calls[0]["language_code"] == "en-US"
    assert client.calls[0]["name"] == "en-US-Standard-C"
    assert client.calls[1]["name"] is None


def test_api_tts_rejects_empty_audio(monkeypatch, load_module):
    tts_backend = load_module("tts_backend")
    client = RecordingTtsClient(audio=b"")
    monkeypatch.setattr(tts_backend, "texttospeech", FakeTextToSpeech)
    monkeypatch.setattr(tts_backend, "_get_tts_client", lambda: (client, "test-creds"))

    with pytest.raises(tts_backend.HTTPException, match="empty audio"):
        asyncio.run(
            tts_backend.api_tts(tts_backend.TTSRequest(text="Hello", lang="en-US"))
        )
