from __future__ import annotations

import logging


def test_load_contributor_email_config_disabled_by_default(load_module):
    email_config = load_module("backend.notifications.email_config")

    config = email_config.load_contributor_email_config(environ={})

    assert config.enabled is False
    assert config.is_ready is False
    assert config.disabled_reason == "autoreply_disabled"
    assert config.smtp_port == 587
    assert config.smtp_use_tls is True
    assert config.from_name == "Sign2Text Team"


def test_load_contributor_email_config_rejects_partial_auth(load_module):
    email_config = load_module("backend.notifications.email_config")

    config = email_config.load_contributor_email_config(
        environ={
            "CONTRIB_AUTOREPLY_ENABLED": "true",
            "CONTRIB_SMTP_HOST": "smtp.example.test",
            "CONTRIB_FROM_EMAIL": "hello@example.test",
            "CONTRIB_SMTP_USERNAME": "mailer@example.test",
        }
    )

    assert config.enabled is True
    assert config.is_ready is False
    assert config.disabled_reason == "smtp_auth_incomplete"


def test_load_contributor_email_config_defaults_port_and_tls(load_module):
    email_config = load_module("backend.notifications.email_config")

    config = email_config.load_contributor_email_config(
        environ={
            "CONTRIB_AUTOREPLY_ENABLED": "true",
            "CONTRIB_SMTP_HOST": "smtp.example.test",
            "CONTRIB_SMTP_PORT": "not-a-number",
            "CONTRIB_FROM_EMAIL": "hello@example.test",
        }
    )

    assert config.is_ready is True
    assert config.smtp_port == 587
    assert config.smtp_use_tls is True


def test_notifications_modules_do_not_load_dotenv_on_import(
    monkeypatch,
    load_module,
):
    secret_config = load_module("backend.secret_config")
    calls = []

    def fake_apply_project_dotenv(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("apply_project_dotenv should not run during import")

    monkeypatch.setattr(secret_config, "apply_project_dotenv", fake_apply_project_dotenv)

    load_module("backend.notifications.email_config")
    load_module("backend.notifications.contributor_autoreply")

    assert calls == []


def test_contributor_service_from_env_uses_config_loader(monkeypatch, load_module):
    email_config = load_module("backend.notifications.email_config")
    contributor_autoreply = load_module("backend.notifications.contributor_autoreply")

    expected_config = email_config.ContributorAutoReplyConfig(
        enabled=False,
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_use_tls=True,
        from_email="",
        from_name="Sign2Text Team",
        is_ready=False,
        disabled_reason="autoreply_disabled",
    )
    calls = []

    def fake_loader(environ=None, project_root=None):
        calls.append((environ, project_root))
        return expected_config

    monkeypatch.setattr(
        contributor_autoreply,
        "load_contributor_email_config",
        fake_loader,
    )

    service = contributor_autoreply.ContributorAutoReplyService.from_env(
        environ={"CONTRIB_AUTOREPLY_ENABLED": "false"},
        project_root="C:/tmp/test-project",
    )

    assert calls == [({"CONTRIB_AUTOREPLY_ENABLED": "false"}, "C:/tmp/test-project")]
    assert service._config == expected_config


def test_smtp_email_sender_sends_message_with_tls_and_auth(
    monkeypatch,
    load_module,
):
    email_config = load_module("backend.notifications.email_config")
    email_sender = load_module("backend.notifications.email_sender")

    class FakeSMTP:
        instances = []

        def __init__(self, host, port, timeout):
            self.host = host
            self.port = port
            self.timeout = timeout
            self.ehlo_calls = 0
            self.starttls_called = False
            self.login_args = None
            self.message = None
            FakeSMTP.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            self.ehlo_calls += 1

        def starttls(self, context=None):
            self.starttls_called = context is not None

        def login(self, username, password):
            self.login_args = (username, password)

        def send_message(self, message):
            self.message = message

    monkeypatch.setattr(email_sender.smtplib, "SMTP", FakeSMTP)

    config = email_config.ContributorAutoReplyConfig(
        enabled=True,
        smtp_host="smtp.example.test",
        smtp_port=2525,
        smtp_username="mailer@example.test",
        smtp_password="super-secret-password",
        smtp_use_tls=True,
        from_email="hello@example.test",
        from_name="Sign2Text Team",
        is_ready=True,
        disabled_reason="",
    )
    sender = email_sender.SmtpEmailSender(config)

    result = sender.send(
        email_sender.EmailMessagePayload(
            to_email="person@example.test",
            subject="Subject line",
            text_body="Body text",
        )
    )

    instance = FakeSMTP.instances[0]
    assert result.status == "sent"
    assert result.detail == "email_sent"
    assert instance.host == "smtp.example.test"
    assert instance.port == 2525
    assert instance.starttls_called is True
    assert instance.login_args == (
        "mailer@example.test",
        "super-secret-password",
    )
    assert instance.message["From"] == "Sign2Text Team <hello@example.test>"
    assert instance.message["To"] == "person@example.test"
    assert instance.message["Subject"] == "Subject line"


def test_smtp_email_sender_returns_safe_failure_without_secret_leak(
    monkeypatch,
    load_module,
):
    email_config = load_module("backend.notifications.email_config")
    email_sender = load_module("backend.notifications.email_sender")

    class FailingSMTP:
        def __init__(self, host, port, timeout):
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            return None

        def starttls(self, context=None):
            return None

        def login(self, username, password):
            raise RuntimeError(f"authentication failed for password={password}")

    monkeypatch.setattr(email_sender.smtplib, "SMTP", FailingSMTP)

    config = email_config.ContributorAutoReplyConfig(
        enabled=True,
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_username="mailer@example.test",
        smtp_password="super-secret-password",
        smtp_use_tls=True,
        from_email="hello@example.test",
        from_name="Sign2Text Team",
        is_ready=True,
        disabled_reason="",
    )
    sender = email_sender.SmtpEmailSender(config)

    result = sender.send(
        email_sender.EmailMessagePayload(
            to_email="person@example.test",
            subject="Subject line",
            text_body="Body text",
        )
    )

    assert result.status == "failed"
    assert result.detail == "RuntimeError: email_send_failed"
    assert "super-secret-password" not in result.detail


def test_contributor_service_skips_when_config_is_incomplete(load_module):
    contributor_autoreply = load_module("backend.notifications.contributor_autoreply")

    service = contributor_autoreply.ContributorAutoReplyService.from_env(
        environ={
            "CONTRIB_AUTOREPLY_ENABLED": "true",
            "CONTRIB_FROM_EMAIL": "hello@example.test",
        }
    )

    result = service.send_acknowledgement("person@example.test")

    assert result.status == "skipped"
    assert result.detail == "missing_smtp_host"


def test_contributor_service_logs_masked_email_on_failure(
    caplog,
    load_module,
):
    email_config = load_module("backend.notifications.email_config")
    email_sender = load_module("backend.notifications.email_sender")
    contributor_autoreply = load_module("backend.notifications.contributor_autoreply")

    class FailingSender:
        def send(self, payload):
            return email_sender.EmailSendResult(
                status="failed",
                detail="RuntimeError: email_send_failed",
            )

    config = email_config.ContributorAutoReplyConfig(
        enabled=True,
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_use_tls=True,
        from_email="hello@example.test",
        from_name="Sign2Text Team",
        is_ready=True,
        disabled_reason="",
    )
    service = contributor_autoreply.ContributorAutoReplyService(
        config=config,
        sender=FailingSender(),
    )

    caplog.set_level(logging.INFO, logger="contributor_autoreply")
    caplog.clear()

    result = service.send_acknowledgement("person@example.test")

    log_text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "contributor_autoreply"
    )
    assert result.status == "failed"
    assert "p***n@example.test" in log_text
    assert "person@example.test" not in log_text
