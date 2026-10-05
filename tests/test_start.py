from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_read_dotenv_parses_comments_quotes_and_blank_lines(tmp_path, load_module):
    start = load_module("start")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "PLAIN=value",
                'QUOTED="two words"',
                "SINGLE='three words'",
                "EMPTY=",
                "INVALID LINE",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert start.read_dotenv(env_file) == {
        "PLAIN": "value",
        "QUOTED": "two words",
        "SINGLE": "three words",
        "EMPTY": "",
    }


def test_resolve_path_var_makes_relative_paths_absolute(tmp_path, monkeypatch, load_module):
    start = load_module("start")
    monkeypatch.setattr(start, "ROOT", tmp_path)

    env = {"GOOGLE_STT_CREDENTIALS": r"keys\stt-service-account.json"}
    start.resolve_path_var(env, "GOOGLE_STT_CREDENTIALS")

    assert env["GOOGLE_STT_CREDENTIALS"] == str(
        (tmp_path / "keys" / "stt-service-account.json").resolve()
    )


def test_build_child_env_uses_default_key_locations_and_sets_application_creds(
    tmp_path, monkeypatch, load_module
):
    start = load_module("start")
    monkeypatch.setattr(start, "ROOT", tmp_path)
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    stt_file = keys_dir / "stt-service-account.json"
    tts_file = keys_dir / "tts-service-account.json"
    stt_file.write_text("{}", encoding="utf-8")
    tts_file.write_text("{}", encoding="utf-8")

    for key in (
        "GOOGLE_STT_CREDENTIALS",
        "GOOGLE_TTS_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CREDENTIALS_DIR",
        "STT_KEY_PATH",
        "TTS_KEY_PATH",
        "PYTHONUNBUFFERED",
    ):
        monkeypatch.delenv(key, raising=False)

    env = start.build_child_env()

    assert env["GOOGLE_STT_CREDENTIALS"] == str(stt_file.resolve())
    assert env["GOOGLE_TTS_CREDENTIALS"] == str(tts_file.resolve())
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == env["GOOGLE_TTS_CREDENTIALS"]
    assert env["PYTHONUNBUFFERED"] == "1"


def test_build_child_env_keeps_google_creds_unset_when_no_local_files_exist(
    tmp_path, monkeypatch, load_module
):
    start = load_module("start")
    monkeypatch.setattr(start, "ROOT", tmp_path)

    for key in (
        "GOOGLE_STT_CREDENTIALS",
        "GOOGLE_TTS_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CREDENTIALS_DIR",
        "STT_KEY_PATH",
        "TTS_KEY_PATH",
    ):
        monkeypatch.delenv(key, raising=False)

    env = start.build_child_env()

    assert "GOOGLE_STT_CREDENTIALS" not in env
    assert "GOOGLE_TTS_CREDENTIALS" not in env
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env


def test_validate_required_files_exits_when_explicit_credentials_are_missing(
    tmp_path, load_module
):
    start = load_module("start")

    env = {"GOOGLE_STT_CREDENTIALS": str(tmp_path / "missing-stt.json")}

    with pytest.raises(SystemExit):
        start.validate_required_files(env)


def test_validate_required_files_allows_adc_when_no_google_files_are_configured(
    load_module,
):
    start = load_module("start")

    start.validate_required_files({})


def test_validate_required_files_replaces_stale_application_credentials(
    tmp_path, load_module
):
    start = load_module("start")
    tts_file = tmp_path / "tts.json"
    tts_file.write_text("{}", encoding="utf-8")

    env = {
        "GOOGLE_TTS_CREDENTIALS": str(tts_file),
        "GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "stale-gac.json"),
    }

    start.validate_required_files(env)

    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == str(tts_file)


def test_validate_required_files_drops_stale_application_credentials_without_replacement(
    tmp_path, load_module
):
    start = load_module("start")

    env = {
        "GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "stale-gac.json"),
    }

    start.validate_required_files(env)

    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env


def test_validate_asl_checkpoint_allows_existing_checkpoint(tmp_path, load_module):
    start = load_module("start")
    checkpoint = tmp_path / "best_model.pt"
    checkpoint.write_text("", encoding="utf-8")

    start.validate_asl_checkpoint(
        SimpleNamespace(asl_checkpoint_path=str(checkpoint))
    )


def test_validate_asl_checkpoint_exits_with_actionable_message(
    tmp_path, monkeypatch, load_module
):
    start = load_module("start")
    messages = []
    missing_checkpoint = tmp_path / "missing.pt"
    monkeypatch.setattr(
        start,
        "log",
        lambda message, tag="SYSTEM": messages.append((tag, message)),
    )

    with pytest.raises(SystemExit):
        start.validate_asl_checkpoint(
            SimpleNamespace(asl_checkpoint_path=str(missing_checkpoint))
        )

    assert any(tag == "ERROR" for tag, _message in messages)
    assert any(
        "ASL checkpoint file not found." in message
        and str(missing_checkpoint) in message
        and start.DEFAULT_ASL_CHECKPOINT_PATH in message
        for _tag, message in messages
    )


def test_choose_python_prefers_project_venv(tmp_path, monkeypatch, load_module):
    start = load_module("start")
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(start, "VENV_PYTHON", venv_python)

    assert start.choose_python() == str(venv_python)


def test_configure_commands_uses_runtime_ports_and_hosts(monkeypatch, load_module):
    start = load_module("start")
    # Isolate admin bind-host env read so the test is deterministic regardless of local .env
    monkeypatch.delenv("ADMIN_BIND_HOST", raising=False)
    start.configure_commands(
        SimpleNamespace(
            asl_bind_host="0.0.0.0",
            asl_port=9100,
            voice_bind_host="127.0.0.1",
            voice_port=9101,
            letter_bind_host="127.0.0.1",
            letter_port=9102,
            frontend_bind_host="0.0.0.0",
            frontend_port=5600,
            admin_port=8003,
        )
    )

    def svc(name):
        return next(s for s in start.SERVICES if s.name == name)

    asl = svc("ASL")
    assert asl.port == 9100
    assert asl.connect_host == "127.0.0.1"
    assert asl.command[-4:] == ["--host", "0.0.0.0", "--port", "9100"]

    voice = svc("VOICE")
    assert voice.port == 9101
    assert voice.connect_host == "127.0.0.1"
    assert voice.command[-4:] == ["--host", "127.0.0.1", "--port", "9101"]

    letter = svc("LETTER")
    assert letter.port == 9102
    assert letter.connect_host == "127.0.0.1"
    assert letter.command[-4:] == ["--host", "127.0.0.1", "--port", "9102"]

    # ADMIN uses admin_port from config; bind host still reads from env (inherits asl_bind_host)
    admin = svc("ADMIN")
    assert admin.port == 8003
    assert admin.connect_host == "127.0.0.1"
    assert admin.command[-4:] == ["--host", "0.0.0.0", "--port", "8003"]

    frontend = svc("FRONTEND")
    assert frontend.port == 5600
    assert frontend.connect_host == "127.0.0.1"
    assert frontend.command[-3:] == ["5600", "--bind", "0.0.0.0"]


def test_get_port_pids_parses_netstat_output(monkeypatch, load_module):
    start = load_module("start")
    sample = "\n".join(
        [
            "  TCP    127.0.0.1:8000     0.0.0.0:0      LISTENING       1111",
            "  TCP    127.0.0.1:8000     127.0.0.1:5123 ESTABLISHED     9999",
            "  TCP    127.0.0.1:8001     0.0.0.0:0      LISTENING       2222",
        ]
    )
    monkeypatch.setattr(
        start.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=sample),
    )

    assert start.get_port_pids(8000) == [1111]


def test_get_pid_name_parses_tasklist_csv(monkeypatch, load_module):
    start = load_module("start")
    monkeypatch.setattr(
        start.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout='"python.exe","1234","Console","1","10,000 K"\n'
        ),
    )

    assert start.get_pid_name(1234) == "python.exe"
