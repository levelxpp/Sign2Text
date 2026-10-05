from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.supabase_client import SupabaseClient, get_supabase_client, is_supabase_available


def _make_config(**overrides):
    defaults = {
        "supabase_enabled": False,
        "supabase_url": "",
        "supabase_service_role_key": "",
        "supabase_schema": "public",
        "supabase_timeout_sec": 10.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── is_supabase_available ────────────────────────────────────────────────


def test_is_supabase_available_when_enabled():
    assert is_supabase_available(_make_config(supabase_enabled=True)) is True


def test_is_supabase_available_when_disabled():
    assert is_supabase_available(_make_config(supabase_enabled=False)) is False


# ── get_supabase_client ──────────────────────────────────────────────────


def test_get_supabase_client_returns_none_when_disabled():
    assert get_supabase_client(_make_config()) is None


def test_get_supabase_client_returns_client_when_enabled():
    config = _make_config(
        supabase_enabled=True,
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="sk-test-key-1234",
    )
    client = get_supabase_client(config)
    assert isinstance(client, SupabaseClient)
    client.close()


def test_get_supabase_client_raises_when_enabled_but_no_url():
    config = _make_config(
        supabase_enabled=True,
        supabase_service_role_key="sk-test-key-1234",
    )
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        get_supabase_client(config)


def test_get_supabase_client_raises_when_enabled_but_no_key():
    config = _make_config(
        supabase_enabled=True,
        supabase_url="https://x.supabase.co",
    )
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        get_supabase_client(config)


# ── SupabaseClient repr / str ────────────────────────────────────────────


def test_service_role_key_not_in_repr():
    secret = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.super-secret"
    client = SupabaseClient(
        base_url="https://x.supabase.co",
        service_role_key=secret,
    )
    assert secret not in repr(client)
    assert secret not in str(client)
    assert "...cret" in repr(client)  # last 4 chars of the key


# ── SupabaseClient is lazy ───────────────────────────────────────────────


def test_client_does_not_create_httpx_on_init():
    client = SupabaseClient(
        base_url="https://x.supabase.co",
        service_role_key="sk-test",
    )
    assert client._http is None


# ── PostgREST method contracts (mocked httpx) ───────────────────────────


def _mock_response(json_data=None, status_code=200, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else []
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    return resp


def test_select_sends_get_request():
    client = SupabaseClient(
        base_url="https://x.supabase.co",
        service_role_key="sk-test",
    )
    mock_http = MagicMock()
    mock_http.get.return_value = _mock_response([{"id": 1}])
    client._http = mock_http

    result = client.select("profiles", {"email": "eq.a@b.com"})

    mock_http.get.assert_called_once_with(
        "/profiles", params={"email": "eq.a@b.com"}
    )
    assert result == [{"id": 1}]


def test_insert_sends_post_request():
    client = SupabaseClient(
        base_url="https://x.supabase.co",
        service_role_key="sk-test",
    )
    mock_http = MagicMock()
    mock_http.post.return_value = _mock_response([{"id": 1, "name": "Test"}])
    client._http = mock_http

    result = client.insert("feedback", {"name": "Test"})

    mock_http.post.assert_called_once_with("/feedback", json={"name": "Test"})
    assert result == {"id": 1, "name": "Test"}


def test_upsert_sends_post_with_merge_prefer():
    client = SupabaseClient(
        base_url="https://x.supabase.co",
        service_role_key="sk-test",
    )
    mock_http = MagicMock()
    mock_http.post.return_value = _mock_response([{"google_sub": "sub1"}])
    client._http = mock_http

    result = client.upsert("profiles", {"google_sub": "sub1"}, on_conflict="google_sub")

    call_kwargs = mock_http.post.call_args
    assert call_kwargs.kwargs.get("params") == {"on_conflict": "google_sub"}
    assert "resolution=merge-duplicates" in call_kwargs.kwargs.get("headers", {}).get("Prefer", "")


def test_head_count_parses_content_range():
    client = SupabaseClient(
        base_url="https://x.supabase.co",
        service_role_key="sk-test",
    )
    mock_http = MagicMock()
    mock_http.head.return_value = _mock_response(
        headers={"content-range": "0-9/42"}
    )
    client._http = mock_http

    assert client.head_count("contributor_leads") == 42


def test_head_count_handles_empty_table():
    client = SupabaseClient(
        base_url="https://x.supabase.co",
        service_role_key="sk-test",
    )
    mock_http = MagicMock()
    mock_http.head.return_value = _mock_response(
        headers={"content-range": "*/0"}
    )
    client._http = mock_http

    assert client.head_count("contributor_leads") == 0


def test_context_manager_closes_client():
    with SupabaseClient(
        base_url="https://x.supabase.co",
        service_role_key="sk-test",
    ) as client:
        assert isinstance(client, SupabaseClient)
    assert client._http is None
