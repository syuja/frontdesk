"""
Tests for HospitableClient.get() timeout retry behaviour.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

os.environ.setdefault("HOSPITABLE_PAT", "test-pat")


def _make_client():
    from hospitable.client import HospitableClient
    return HospitableClient()


def _ok_response(data: dict | None = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = 200
    resp._content = __import__("json").dumps(data or {}).encode()
    return resp


# ── ReadTimeout retry ─────────────────────────────────────────────────────────

def test_read_timeout_retries_and_succeeds():
    """A ReadTimeout on the first attempt is retried; success on second attempt."""
    client = _make_client()
    ok = _ok_response({"data": [], "meta": {"last_page": 1}})

    with patch.object(client._session, "get", side_effect=[
        requests.exceptions.ReadTimeout(),
        ok,
    ]) as mock_get, patch("hospitable.client.time.sleep"):
        result = client.get("/reservations")

    assert mock_get.call_count == 2
    assert result == {"data": [], "meta": {"last_page": 1}}


def test_read_timeout_exhausted_raises():
    """Exhausting all MAX_RETRIES with ReadTimeout raises ReadTimeout; all attempts used."""
    from hospitable.client import MAX_RETRIES
    client = _make_client()

    mock_get = patch.object(client._session, "get", side_effect=requests.exceptions.ReadTimeout())
    with mock_get as m, patch("hospitable.client.time.sleep"), pytest.raises(requests.exceptions.ReadTimeout):
        client.get("/reservations")
    assert m.call_count == MAX_RETRIES


# ── ConnectTimeout retry ──────────────────────────────────────────────────────

def test_connect_timeout_retries_and_succeeds():
    """A ConnectTimeout on the first attempt is retried; success on second attempt."""
    client = _make_client()
    ok = _ok_response({"hello": "world"})

    with patch.object(client._session, "get", side_effect=[
        requests.exceptions.ConnectTimeout(),
        ok,
    ]) as mock_get, patch("hospitable.client.time.sleep"):
        result = client.get("/properties")

    assert mock_get.call_count == 2
    assert result == {"hello": "world"}


def test_connect_timeout_exhausted_raises():
    """Exhausting all MAX_RETRIES with ConnectTimeout raises ConnectTimeout; all attempts used."""
    from hospitable.client import MAX_RETRIES
    client = _make_client()

    mock_get = patch.object(client._session, "get", side_effect=requests.exceptions.ConnectTimeout())
    with mock_get as m, patch("hospitable.client.time.sleep"), pytest.raises(requests.exceptions.ConnectTimeout):
        client.get("/properties")
    assert m.call_count == MAX_RETRIES


# ── Backoff on timeout ────────────────────────────────────────────────────────

def test_read_timeout_backoff_delays():
    """Timeout retries sleep with exponential backoff (1s, 2s for MAX_RETRIES=3)."""
    from hospitable.client import MAX_RETRIES
    client = _make_client()
    ok = _ok_response({})

    # Fail first two, succeed on third
    side_effects = [requests.exceptions.ReadTimeout()] * (MAX_RETRIES - 1) + [ok]

    with patch.object(client._session, "get", side_effect=side_effects), \
         patch("hospitable.client.time.sleep") as mock_sleep:
        client.get("/reservations")

    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert sleep_calls == [2 ** i for i in range(MAX_RETRIES - 1)]
