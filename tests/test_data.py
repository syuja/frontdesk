"""
Tests for the last_message_at incremental-filter addition to
hospitable/data.py's get_reservations() and get_inquiries() (Phase 3 Step 1,
M4). Everything else in this module is exercised indirectly via
tests/test_checks.py and tests/test_ingest.py -- this file covers just the
new, additive kwarg.
"""
from __future__ import annotations

import datetime
import os
from unittest.mock import patch

os.environ.setdefault("HOSPITABLE_PAT", "test-pat")

import hospitable.data as hdata
from hospitable.client import HospitableClient


def _client() -> HospitableClient:
    return HospitableClient()


def test_get_reservations_omits_last_message_at_when_not_set():
    client = _client()
    with patch.object(client, "get_pages", return_value=[]) as mock_pages:
        hdata.get_reservations(client, ["p1"], datetime.date(2026, 1, 1), datetime.date(2026, 2, 1))
    params = dict(mock_pages.call_args.kwargs["params"])
    assert "last_message_at" not in params


def test_get_reservations_includes_last_message_at_when_set():
    client = _client()
    with patch.object(client, "get_pages", return_value=[]) as mock_pages:
        hdata.get_reservations(
            client, ["p1"], datetime.date(2026, 1, 1), datetime.date(2026, 2, 1),
            last_message_at="2026-08-01T00:00:00Z",
        )
    params = dict(mock_pages.call_args.kwargs["params"])
    assert params["last_message_at"] == "2026-08-01T00:00:00Z"


def test_get_reservations_existing_params_unaffected_by_new_kwarg():
    """Backward compatibility: existing callers (checks/*.py) that don't pass
    last_message_at must see identical params to before this change."""
    client = _client()
    with patch.object(client, "get_pages", return_value=[]) as mock_pages:
        hdata.get_reservations(client, ["p1"], datetime.date(2026, 1, 1), datetime.date(2026, 2, 1))
    params = dict(mock_pages.call_args.kwargs["params"])
    assert params["start_date"] == "2026-01-01"
    assert params["end_date"] == "2026-02-01"
    assert params["date_query"] == "checkin"


def test_get_inquiries_omits_last_message_at_when_not_set():
    client = _client()
    with patch.object(client, "get_pages", return_value=[]) as mock_pages:
        hdata.get_inquiries(client, ["p1"])
    params = dict(mock_pages.call_args.kwargs["params"])
    assert "last_message_at" not in params


def test_get_inquiries_includes_last_message_at_when_set():
    client = _client()
    with patch.object(client, "get_pages", return_value=[]) as mock_pages:
        hdata.get_inquiries(client, ["p1"], last_message_at="2026-08-01T00:00:00Z")
    params = dict(mock_pages.call_args.kwargs["params"])
    assert params["last_message_at"] == "2026-08-01T00:00:00Z"
