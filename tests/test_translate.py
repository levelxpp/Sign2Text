from __future__ import annotations

import asyncio
import io
import urllib.error

import pytest


class DummyUrlopenResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("english", "en"),
        ("Spanish", "es"),
        ("ar-SA", "ar"),
        ("hy_AM", "hy_am"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_lang_handles_names_locales_and_empty_values(raw, expected, load_module):
    translate = load_module("translate")
    assert translate.normalize_lang(raw) == expected


def test_translate_blank_text_short_circuits_without_network(monkeypatch, load_module):
    translate = load_module("translate")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "test-key")
    translator = translate.AzureTranslator()
    monkeypatch.setattr(
        translator,
        "_translate_sync",
        lambda *args, **kwargs: pytest.fail("network call should not happen"),
    )

    result = asyncio.run(translator.translate("   ", "es", from_lang="en"))

    assert result == {
        "translatedText": "",
        "to": "es",
        "from": "en",
        "sourceText": "",
    }


def test_translate_same_language_returns_original_text(monkeypatch, load_module):
    translate = load_module("translate")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "test-key")
    translator = translate.AzureTranslator()

    result = asyncio.run(translator.translate("Hello", "en-US", from_lang="english"))

    assert result == {
        "translatedText": "Hello",
        "to": "en",
        "from": "en",
        "sourceText": "Hello",
    }


def test_translate_rejects_unsupported_languages(monkeypatch, load_module):
    translate = load_module("translate")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "test-key")
    translator = translate.AzureTranslator()

    with pytest.raises(ValueError, match="Unsupported to_lang"):
        asyncio.run(translator.translate("Hello", "fr"))


def test_translate_sync_parses_successful_azure_response(monkeypatch, load_module):
    translate = load_module("translate")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "test-key")
    translator = translate.AzureTranslator()

    def fake_urlopen(req, timeout):
        assert "to=es" in req.full_url
        headers = {key.lower(): value for key, value in req.header_items()}
        assert headers["ocp-apim-subscription-key"] == "test-key"
        return DummyUrlopenResponse(
            '[{"detectedLanguage":{"language":"en"},"translations":[{"text":"Hola"}]}]'
        )

    monkeypatch.setattr(translate.urllib.request, "urlopen", fake_urlopen)

    result = translator._translate_sync("Hello", "es", None)

    assert result == {
        "translatedText": "Hola",
        "to": "es",
        "from": "en",
        "sourceText": "Hello",
    }


def test_translate_sync_wraps_http_errors(monkeypatch, load_module):
    translate = load_module("translate")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "test-key")
    translator = translate.AzureTranslator()
    error = urllib.error.HTTPError(
        url="https://example.com",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"slow down"}'),
    )

    def raise_http_error(req, timeout):
        raise error

    monkeypatch.setattr(translate.urllib.request, "urlopen", raise_http_error)

    with pytest.raises(RuntimeError, match="Azure HTTP 429"):
        translator._translate_sync("Hello", "es", None)


def test_translate_reads_key_from_file_env(tmp_path, monkeypatch, load_module):
    translate = load_module("translate")
    key_file = tmp_path / "azure-key.txt"
    key_file.write_text("file-azure-key\n", encoding="utf-8")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY_FILE", str(key_file))

    translator = translate.AzureTranslator()

    assert translator.key == "file-azure-key"
