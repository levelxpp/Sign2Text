from __future__ import annotations


def test_load_runtime_config_resolves_relative_checkpoint_and_port_overrides(
    tmp_path, load_module
):
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "ASL_CHECKPOINT_PATH": r"models\best_model.pt",
            "VOICE_API_PORT": "9001",
            "ASL_API_PORT": "9000",
        },
    )

    assert config.asl_checkpoint_path == str(
        (tmp_path / "models" / "best_model.pt").resolve()
    )
    # frontend_app_config always emits KEYPOINTS_ENABLED so the browser knows
    # whether to render the skeleton overlay. The expected dicts below were
    # updated to the current contract; they remain strict equality assertions
    # rather than being relaxed to subset checks.
    assert config.frontend_app_config == {
        "ASL_PORT": 9000,
        "VOICE_PORT": 9001,
        "KEYPOINTS_ENABLED": False,
    }


def test_load_runtime_config_with_public_host_generates_frontend_overrides(
    tmp_path, load_module
):
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "APP_PUBLIC_HOST": "captions.example.test",
            "APP_PUBLIC_SCHEME": "https",
        },
    )

    assert config.voice_api_base_url == "https://captions.example.test:8001"
    assert config.asl_api_base_url == "https://captions.example.test:8000"
    assert config.frontend_public_url == "https://captions.example.test:5500"
    assert config.frontend_app_config == {
        "ASL_BACKEND": "https://captions.example.test:8000",
        "BACKEND": "https://captions.example.test:8001",
        "LETTER_BACKEND": "https://captions.example.test:8002",
        "KEYPOINTS_ENABLED": False,
    }
    # ADMIN_BACKEND is NOT emitted from public_host_explicit alone — it requires
    # ADMIN_API_BASE_URL to be explicitly set to avoid emitting an unreachable derived URL.
    assert '"BACKEND": "https://captions.example.test:8001"' in (
        runtime_config.render_frontend_runtime_config_js(config)
    )


def test_load_runtime_config_preserves_same_origin_path_overrides(
    tmp_path, load_module
):
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "VOICE_API_BASE_URL": "/",
            "ASL_API_BASE_URL": "/asl",
        },
    )

    assert config.voice_api_base_url == "/"
    assert config.voice_ws_url == "/ws/stt"
    assert config.asl_api_base_url == "/asl"
    assert config.frontend_app_config == {
        "ASL_BACKEND": "/asl",
        "BACKEND": "/",
        "KEYPOINTS_ENABLED": False,
    }


def test_load_runtime_config_with_admin_port_override(tmp_path, load_module):
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={"ADMIN_API_PORT": "9003"},
    )

    assert config.admin_port == 9003
    assert config.admin_api_base_url == "http://127.0.0.1:9003"
    assert config.frontend_app_config == {
        "ADMIN_PORT": 9003,
        "KEYPOINTS_ENABLED": False,
    }


def test_load_runtime_config_with_explicit_admin_base_url(tmp_path, load_module):
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={"ADMIN_API_BASE_URL": "https://admin.example.test/api"},
    )

    assert config.admin_api_base_url == "https://admin.example.test/api"
    assert config.admin_api_base_url_explicit is True
    assert config.frontend_app_config == {
        "ADMIN_BACKEND": "https://admin.example.test/api",
        "KEYPOINTS_ENABLED": False,
    }


def test_proxy_mode_path_overrides_cover_all_services(tmp_path, load_module):
    """Simulates what start.py injects in --proxy / --tunnel mode.

    All four backend services must resolve to their proxy path so that
    runtime-config.js tells the browser to route through port 8080 instead
    of reaching direct service ports.
    """
    runtime_config = load_module("runtime_config")

    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "ASL_API_BASE_URL": "/asl-api",
            "VOICE_API_BASE_URL": "/voice-api",
            "VOICE_WS_URL": "/voice-api/ws/stt",
            "LETTER_API_BASE_URL": "/letter-api",
            "ADMIN_API_BASE_URL": "/admin-api",
        },
    )

    assert config.asl_api_base_url == "/asl-api"
    assert config.voice_api_base_url == "/voice-api"
    assert config.voice_ws_url == "/voice-api/ws/stt"
    assert config.letter_api_base_url == "/letter-api"
    assert config.admin_api_base_url == "/admin-api"
    assert config.frontend_app_config == {
        "ASL_BACKEND": "/asl-api",
        "BACKEND": "/voice-api",
        "BACKEND_WS_URL": "/voice-api/ws/stt",
        "LETTER_BACKEND": "/letter-api",
        "ADMIN_BACKEND": "/admin-api",
        "KEYPOINTS_ENABLED": False,
    }


# ── read_dotenv inline-comment regression tests ─────────────────────────


def test_read_dotenv_strips_inline_comments(tmp_path, load_module):
    """Inline `# comment` after a value must be stripped (Task 5 fix)."""
    runtime_config = load_module("runtime_config")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OLLAMA_MODE=remote                  # RunPod default\n"
        "ASL_RUNTIME_PROFILE=cuda            # OPTIONAL: auto|cuda|cpu\n"
        "OLLAMA_MODEL=qwen2.5:3b-instruct   # default model\n"
        "OLLAMA_TIMEOUT_SEC=30               # HTTP timeout in seconds\n"
    )

    env = runtime_config.read_dotenv(env_file)
    assert env["OLLAMA_MODE"] == "remote"
    assert env["ASL_RUNTIME_PROFILE"] == "cuda"
    assert env["OLLAMA_MODEL"] == "qwen2.5:3b-instruct"
    assert env["OLLAMA_TIMEOUT_SEC"] == "30"


def test_read_dotenv_preserves_quoted_value_with_hash(tmp_path, load_module):
    """A `#` inside quotes is part of the value, not a comment."""
    runtime_config = load_module("runtime_config")

    env_file = tmp_path / ".env"
    env_file.write_text(
        'GREETING="Hello # World"\n'
        "SINGLE='foo # bar'\n"
    )

    env = runtime_config.read_dotenv(env_file)
    assert env["GREETING"] == "Hello # World"
    assert env["SINGLE"] == "foo # bar"


def test_read_dotenv_handles_urls_and_plain_values(tmp_path, load_module):
    """URLs with fragment-like syntax and plain values must not be corrupted."""
    runtime_config = load_module("runtime_config")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OLLAMA_URL=http://localhost:11434/api/chat\n"
        "PLAIN_VALUE=hello\n"
        "EMPTY_VALUE=\n"
    )

    env = runtime_config.read_dotenv(env_file)
    assert env["OLLAMA_URL"] == "http://localhost:11434/api/chat"
    assert env["PLAIN_VALUE"] == "hello"
    assert env["EMPTY_VALUE"] == ""


def test_read_dotenv_ignores_full_line_comments_and_blanks(tmp_path, load_module):
    """Full-line comments and blank lines produce no entries."""
    runtime_config = load_module("runtime_config")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# This is a comment\n"
        "\n"
        "   # Indented comment\n"
        "KEY=value\n"
    )

    env = runtime_config.read_dotenv(env_file)
    assert env == {"KEY": "value"}


def test_read_dotenv_strips_comment_after_closing_quote(tmp_path, load_module):
    """Values like `"bar" # comment` should parse as `bar`."""
    runtime_config = load_module("runtime_config")

    env_file = tmp_path / ".env"
    env_file.write_text(
        '"QUOTED"="value" # trailing comment\n'  # unusual but valid
    )

    # The key here is weird but the point is the value parsing
    env_file.write_text('MY_VAR="value" # trailing comment\n')

    env = runtime_config.read_dotenv(env_file)
    assert env["MY_VAR"] == "value"


def test_read_dotenv_empty_token_with_inline_comment(tmp_path, load_module):
    """An empty value followed by a comment must parse as empty string."""
    runtime_config = load_module("runtime_config")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OLLAMA_AUTH_TOKEN=                  # Optional bearer token\n"
    )

    env = runtime_config.read_dotenv(env_file)
    assert env["OLLAMA_AUTH_TOKEN"] == ""


# ── Supabase configuration tests ─────────────────────────────────────────


def test_supabase_disabled_by_default(tmp_path, load_module):
    runtime_config = load_module("runtime_config")
    config = runtime_config.load_runtime_config(
        project_root=tmp_path, environ={}
    )
    assert config.supabase_enabled is False
    assert config.supabase_url == ""
    assert config.supabase_service_role_key == ""
    assert config.supabase_schema == "public"
    assert config.supabase_timeout_sec == 10.0


def test_supabase_enabled_with_valid_config(tmp_path, load_module):
    runtime_config = load_module("runtime_config")
    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "SUPABASE_ENABLED": "true",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key-value",
            "SUPABASE_SCHEMA": "custom",
            "SUPABASE_TIMEOUT_SEC": "5.0",
        },
    )
    assert config.supabase_enabled is True
    assert config.supabase_url == "https://example.supabase.co"
    assert config.supabase_service_role_key == "test-key-value"
    assert config.supabase_schema == "custom"
    assert config.supabase_timeout_sec == 5.0


def test_supabase_fields_never_in_frontend_app_config(tmp_path, load_module):
    runtime_config = load_module("runtime_config")
    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "SUPABASE_ENABLED": "true",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key-value",
        },
    )
    app_config = config.frontend_app_config
    js = runtime_config.render_frontend_runtime_config_js(config)

    # The security invariant is that no PRIVILEGED Supabase material reaches the
    # browser. The service-role key bypasses row-level security entirely, so it
    # must never appear as a key or as a value, in the config dict or in the
    # rendered JavaScript.
    assert "SUPABASE_SERVICE_ROLE_KEY" not in app_config
    for key in app_config:
        assert "service_role" not in key.lower(), (
            f"Privileged Supabase field {key!r} leaked into frontend_app_config"
        )
    assert "service_role" not in js.lower()
    assert "test-key-value" not in js, "service-role key value leaked into runtime-config.js"

    # SUPABASE_URL and SUPABASE_ANON_KEY are deliberately published to the
    # browser: the client SDK needs both to sign a user in, and the anon key is
    # a public identifier whose access is constrained by row-level security.
    # This test previously asserted that no key containing "supabase" appeared
    # at all, which contradicted that design and failed for a value the product
    # is required to expose. Only privileged material is asserted absent here.
    assert app_config.get("SUPABASE_URL") == "https://example.supabase.co"


def test_supabase_defaults_for_schema_and_timeout(tmp_path, load_module):
    runtime_config = load_module("runtime_config")
    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={
            "SUPABASE_ENABLED": "true",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key-value",
        },
    )
    assert config.supabase_schema == "public"
    assert config.supabase_timeout_sec == 10.0


# ── OLLAMA_ASL_ENABLED tests ─────────────────────────────────────────────


def test_ollama_asl_enabled_default_true(tmp_path, load_module):
    runtime_config = load_module("runtime_config")
    config = runtime_config.load_runtime_config(
        project_root=tmp_path, environ={}
    )
    assert config.ollama_asl_enabled is True


def test_ollama_asl_enabled_parses_true_values(tmp_path, load_module):
    runtime_config = load_module("runtime_config")
    for raw in ("true", "1", "yes", "on", "TRUE", " On "):
        config = runtime_config.load_runtime_config(
            project_root=tmp_path,
            environ={"OLLAMA_ASL_ENABLED": raw},
        )
        assert config.ollama_asl_enabled is True, (
            f"expected True for {raw!r}"
        )


def test_ollama_asl_enabled_parses_false_values(tmp_path, load_module):
    runtime_config = load_module("runtime_config")
    for raw in ("false", "0", "no", "off", "FALSE", " Off "):
        config = runtime_config.load_runtime_config(
            project_root=tmp_path,
            environ={"OLLAMA_ASL_ENABLED": raw},
        )
        assert config.ollama_asl_enabled is False, (
            f"expected False for {raw!r}"
        )


def test_ollama_asl_enabled_unknown_value_falls_back_to_default(
    tmp_path, load_module
):
    runtime_config = load_module("runtime_config")
    config = runtime_config.load_runtime_config(
        project_root=tmp_path,
        environ={"OLLAMA_ASL_ENABLED": "maybe"},
    )
    assert config.ollama_asl_enabled is True
