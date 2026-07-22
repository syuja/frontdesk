"""
Regression test for the Lambda handler.

Guards against the silent-no-send bug: if handler() ever runs without calling
send_digest, this test fails loudly.

All I/O is mocked — no live API calls, no Telegram traffic.
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

from checks.finding import AuditData, CheckConfig, LockStatus, Severity
from checks.finding import Finding


_NOW = datetime.datetime(2026, 7, 21, 11, 0, 0, tzinfo=datetime.timezone.utc)

_AUDIT = AuditData(
    props=[{"uuid": "prop-0001", "name": "Test House"}],
    inquiries=[],
    reviews=[],
    reservations=[],
    kh={},
    client=None,
    smartlocks=[],
    lock_statuses=[],
)

_FINDINGS = [
    Finding(
        check="smartlock_battery",
        severity=Severity.CRITICAL,
        property_uuid="prop-0001",
        property_name="Test House",
        title="Smartlock battery critical",
        detail="battery=15.0% threshold=30.0%",
        extra={"pct": 15.0, "threshold": 30.0, "offline": False},
    )
]


def test_handler_calls_send_digest():
    """handler() must call send_digest exactly once with a non-empty string."""
    with (
        patch("hospitable.lambda_handler.HospitableClient") as mock_client_cls,
        patch("hospitable.lambda_handler.build_audit_data", return_value=_AUDIT),
        patch("hospitable.lambda_handler.run_all", return_value=_FINDINGS),
        patch("hospitable.lambda_handler.datetime") as mock_dt,
        patch("hospitable.lambda_handler.send_digest") as mock_send,
    ):
        mock_dt.datetime.now.return_value = _NOW
        mock_dt.timezone.utc = datetime.timezone.utc

        from hospitable.lambda_handler import handler
        result = handler({}, None)

    mock_send.assert_called_once()
    digest_arg = mock_send.call_args[0][0]
    assert isinstance(digest_arg, str) and len(digest_arg) > 0
    assert result["statusCode"] == 200


def test_handler_sends_even_with_no_findings():
    """handler() must call send_digest even when run_all returns zero findings."""
    with (
        patch("hospitable.lambda_handler.HospitableClient"),
        patch("hospitable.lambda_handler.build_audit_data", return_value=_AUDIT),
        patch("hospitable.lambda_handler.run_all", return_value=[]),
        patch("hospitable.lambda_handler.datetime") as mock_dt,
        patch("hospitable.lambda_handler.send_digest") as mock_send,
    ):
        mock_dt.datetime.now.return_value = _NOW
        mock_dt.timezone.utc = datetime.timezone.utc

        from hospitable.lambda_handler import handler
        handler({}, None)

    mock_send.assert_called_once()
