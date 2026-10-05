from __future__ import annotations

import requests


class DummyOllamaResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_gloss_buffer_deduplicates_flushes_and_tracks_translations(load_module):
    new_inference = load_module("new_Inference")
    buffer = new_inference.GlossBuffer()

    assert buffer.add("hello", 0.95) is True
    assert buffer.add("HELLO", 0.90) is False
    assert buffer.add("thanks", 0.80) is True
    assert buffer.current_gloss == ["HELLO", "THANKS"]
    assert buffer.current_gloss_str == "HELLO THANKS"
    assert buffer.is_active is True

    buffer.add_translation("Hello, thanks.")
    assert buffer.last_translation == "Hello, thanks."
    assert buffer.translations == ["Hello, thanks."]
    assert buffer.flush() == ["HELLO", "THANKS"]
    assert buffer.current_gloss == []
    assert buffer.is_active is False


def test_sentence_boundary_detector_fires_after_sustained_rest(load_module):
    new_inference = load_module("new_Inference")
    detector = new_inference.SentenceBoundaryDetector(
        rest_frames_threshold=3,
        min_signs=1,
        motion_threshold=0.1,
    )

    assert detector.update(hands_raised=False, motion=0.0, gloss_size=1) is False

    detector.notify_sign_added()
    assert detector.update(hands_raised=True, motion=0.5, gloss_size=1) is False
    assert detector.update(hands_raised=False, motion=0.05, gloss_size=1) is False
    assert detector.update(hands_raised=False, motion=0.05, gloss_size=1) is False
    assert detector.update(hands_raised=False, motion=0.05, gloss_size=1) is True
    assert detector.update(hands_raised=False, motion=0.05, gloss_size=1) is False


def test_ollama_translate_worker_parses_success_response(monkeypatch, load_module):
    new_inference = load_module("new_Inference")
    translator = new_inference.OllamaTranslator(
        url="http://localhost:11434/api/chat",
        model="test-model",
        timeout=1.5,
    )
    callbacks = []

    def fake_post(url, json, timeout, headers=None):
        assert url == "http://localhost:11434/api/chat"
        assert json["model"] == "test-model"
        assert json["messages"][1]["content"].endswith("ME HUNGRY")
        assert timeout == 1.5
        return DummyOllamaResponse(200, {"message": {"content": "I'm hungry"}})

    monkeypatch.setattr(new_inference.requests, "post", fake_post)
    translator._translating = True

    translator._do_translate("ME HUNGRY", callbacks.append)

    assert translator.last_result == "I'm hungry"
    assert translator.last_error is None
    assert translator.is_translating is False
    assert callbacks == ["I'm hungry"]


def test_ollama_translate_worker_handles_empty_response(monkeypatch, load_module):
    new_inference = load_module("new_Inference")
    translator = new_inference.OllamaTranslator(model="test-model")
    callbacks = []

    monkeypatch.setattr(
        new_inference.requests,
        "post",
        lambda *args, **kwargs: DummyOllamaResponse(200, {"message": {}}),
    )
    translator._translating = True

    translator._do_translate("HELLO", callbacks.append)

    assert translator.last_result == "Hello."
    assert translator.last_error is None
    assert translator.is_translating is False
    assert callbacks == ["Hello."]


def test_ollama_translate_worker_handles_timeout(monkeypatch, load_module):
    new_inference = load_module("new_Inference")
    translator = new_inference.OllamaTranslator(timeout=0.01)

    def raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(new_inference.requests, "post", raise_timeout)
    translator._translating = True

    translator._do_translate("HELLO", None)

    assert translator.last_error == "Timeout"
    assert translator.is_translating is False


def test_runtime_defaults_follow_environment(monkeypatch, tmp_path, load_module):
    checkpoint = tmp_path / "best_model.pt"
    checkpoint.write_text("", encoding="utf-8")

    monkeypatch.setenv("ASL_CHECKPOINT_PATH", str(checkpoint))
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.internal:11434/api/chat")
    monkeypatch.setenv("OLLAMA_MODEL", "phi4-mini")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SEC", "12.5")

    new_inference = load_module("new_Inference")

    assert new_inference.DEFAULT_CHECKPOINT == str(checkpoint)
    assert new_inference.DEFAULT_OLLAMA_URL == "http://ollama.internal:11434/api/chat"
    assert new_inference.DEFAULT_OLLAMA_MODEL == "phi4-mini"
    assert new_inference.DEFAULT_OLLAMA_TIMEOUT == 12.5


def test_ollama_translator_sends_auth_header_when_token_set(monkeypatch, load_module):
    new_inference = load_module("new_Inference")
    translator = new_inference.OllamaTranslator(
        url="https://hosted.example.com/api/chat",
        model="test-model",
        timeout=5.0,
        auth_token="test-secret-token",
    )

    captured_kwargs = {}

    def fake_post(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return DummyOllamaResponse(200, {"message": {"content": "Hello."}})

    monkeypatch.setattr(new_inference.requests, "post", fake_post)
    translator._translating = True

    translator._do_translate("HELLO", None)

    assert captured_kwargs.get("headers") == {"Authorization": "Bearer test-secret-token"}


def test_ollama_translator_no_auth_header_when_token_empty(monkeypatch, load_module):
    new_inference = load_module("new_Inference")
    translator = new_inference.OllamaTranslator(
        url="http://localhost:11434/api/chat",
        model="test-model",
    )

    captured_kwargs = {}

    def fake_post(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return DummyOllamaResponse(200, {"message": {"content": "Hello."}})

    monkeypatch.setattr(new_inference.requests, "post", fake_post)
    translator._translating = True

    translator._do_translate("HELLO", None)

    # Empty dict passed as headers — requests library treats None the same as absent
    assert captured_kwargs.get("headers") is None


def test_ollama_translator_check_availability_sends_auth_header(monkeypatch, load_module):
    new_inference = load_module("new_Inference")
    translator = new_inference.OllamaTranslator(
        url="https://hosted.example.com/api/chat",
        model="test-model",
        auth_token="check-token",
    )

    captured_kwargs = {}

    def fake_post(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return DummyOllamaResponse(200, {"message": {"content": "ok"}})

    monkeypatch.setattr(new_inference.requests, "post", fake_post)

    result = translator.check_availability()

    assert result is True
    assert captured_kwargs.get("headers") == {"Authorization": "Bearer check-token"}


def test_ollama_translator_fallback_on_401(monkeypatch, load_module):
    new_inference = load_module("new_Inference")
    translator = new_inference.OllamaTranslator(
        url="https://hosted.example.com/api/chat",
        model="test-model",
        auth_token="bad-token",
    )
    callbacks = []

    monkeypatch.setattr(
        new_inference.requests,
        "post",
        lambda *args, **kwargs: DummyOllamaResponse(401, {}),
    )
    translator._translating = True

    translator._do_translate("ME HUNGRY", callbacks.append)

    assert translator.last_error == "HTTP 401"
    assert translator.is_translating is False
    # Fallback normalizer still produces output
    assert len(callbacks) == 1
    assert callbacks[0]  # non-empty fallback sentence


def test_ollama_translator_check_availability_returns_false_on_401(monkeypatch, load_module):
    """check_availability() must return False when the remote endpoint rejects auth."""
    new_inference = load_module("new_Inference")
    translator = new_inference.OllamaTranslator(
        url="https://hosted.example.com/api/chat",
        model="test-model",
        auth_token="bad-token",
    )

    monkeypatch.setattr(
        new_inference.requests,
        "post",
        lambda *args, **kwargs: DummyOllamaResponse(401, {}),
    )

    result = translator.check_availability()

    assert result is False
    assert translator.is_available is False


def test_runtime_config_loads_ollama_auth_token_and_mode(tmp_path, load_module):
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "OLLAMA_AUTH_TOKEN": "  my-secret-token  ",
            "OLLAMA_MODE": "remote",
        },
    )

    assert config.ollama_auth_token == "my-secret-token"
    assert config.ollama_mode == "remote"


def test_runtime_config_ollama_auth_token_defaults_to_none(tmp_path, load_module):
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={},
    )

    assert config.ollama_auth_token is None
    assert config.ollama_mode == "local"


def test_ollama_auth_token_not_in_frontend_config(tmp_path, load_module):
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "OLLAMA_AUTH_TOKEN": "secret-token",
            "OLLAMA_MODE": "remote",
        },
    )

    frontend_config = config.frontend_app_config
    rendered_js = runtime_config.render_frontend_runtime_config_js(config)

    assert "secret-token" not in str(frontend_config)
    assert "secret-token" not in rendered_js
    assert "OLLAMA_AUTH_TOKEN" not in str(frontend_config)
    assert "OLLAMA_AUTH_TOKEN" not in rendered_js


def test_default_config_dict_does_not_contain_auth_token(monkeypatch, load_module):
    """The _default_config dict in api_server (returned in /start responses)
    must never contain the Ollama auth token."""
    monkeypatch.setenv("OLLAMA_AUTH_TOKEN", "should-not-leak")
    api_server = load_module("api_server")

    config_dict = api_server._default_config
    config_str = str(config_dict)

    assert "should-not-leak" not in config_str
    assert "auth_token" not in config_str
    assert "OLLAMA_AUTH_TOKEN" not in config_str
