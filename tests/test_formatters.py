"""
Tests for hospitable/formatters.py and hospitable/telegram.py.

No live API calls, no real Telegram sends — all HTTP is mocked.
"""
from __future__ import annotations

import datetime
import re
from unittest.mock import MagicMock, patch

import pytest

from checks.finding import Finding, Severity
from hospitable.formatters import (
    TELEGRAM_MAX_UTF16,
    _DROPS_OFF_TMPL,
    _h_to_human,
    _h_to_human_precise,
    _short_prop,
    _utf16_len,
    format_digest,
    format_verbose,
    truncate_digest,
)

NOW = datetime.datetime(2025, 7, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _f(
    check: str = "unanswered_inquiry",
    severity: Severity = Severity.HIGH,
    property_name: str = "Ensuite Room",
    title: str = "Unanswered inquiry — Alice last replied 5.0h ago",
    detail: str = "inquiry=abc123 last_message_at=2025-07-05T07:00:00Z gap=5.0h | ages out in 331h",
    guest_name: str | None = "Alice",
    entity_id: str | None = "b2da813b-xxxx-xxxx-xxxx",
) -> Finding:
    return Finding(
        check=check,
        severity=severity,
        property_uuid="aaaa-0001",
        property_name=property_name,
        title=title,
        detail=detail,
        entity_id=entity_id,
        guest_name=guest_name,
    )


# ── Human formatter ────────────────────────────────────────────────────────────

def test_format_digest_severity_grouping_and_order():
    """All 4 severity groups appear in CRITICAL → LOW order with correct emoji."""
    findings = [
        _f(
            severity=Severity.LOW,
            check="turnover_gap",
            title="Turnover gap — 4+ days",
            detail="OUT: PropertyA 2025-07-01 (Bob) → IN: PropertyA 2025-07-09 (Carol) | gap=8d",
            guest_name="Carol",
        ),
        _f(
            severity=Severity.MEDIUM,
            check="actionable_review",
            title="Actionable review — Dave, window open now",
            detail="review=rev001 | checkout=2025-07-01 (4d ago) | expires=2025-07-19 (14d remaining)",
            guest_name="Dave",
        ),
        _f(
            severity=Severity.HIGH,
            check="unanswered_inquiry",
            title="Unanswered inquiry — Eve last replied 20.0h ago",
            detail="inquiry=xyz gap=20.0h | ages out in 316h",
            guest_name="Eve",
        ),
        _f(
            severity=Severity.CRITICAL,
            check="unanswered_inquiry",
            title="Unanswered inquiry — Frank last replied 100.0h ago",
            detail="inquiry=xyz gap=100.0h | ages out in 236h",
            guest_name="Frank",
        ),
    ]
    digest = format_digest(findings, NOW)

    assert "🔴" in digest
    assert "🟠" in digest
    assert "🟡" in digest
    assert "⚪" in digest

    # CRITICAL → HIGH → MEDIUM → LOW
    assert digest.index("🔴") < digest.index("🟠")
    assert digest.index("🟠") < digest.index("🟡")
    assert digest.index("🟡") < digest.index("⚪")

    assert "Frank" in digest
    assert "Eve" in digest
    assert "Dave" in digest


def test_format_digest_zero_findings_allclear():
    """Zero findings → all-clear message; date is still present."""
    digest = format_digest([], NOW)
    assert "✅" in digest
    assert "All clear" in digest
    assert "2025-07-05" in digest


def test_h_to_human_conversions():
    """_h_to_human produces correct human duration labels."""
    assert _h_to_human(177.0) == "~7 days"   # 177 / 24 = 7.375 → round → 7
    assert _h_to_human(3.6)   == "3h"         # int(3.6) = 3
    assert _h_to_human(0.5)   == "30m"
    assert _h_to_human(48.0)  == "~2 days"
    assert _h_to_human(24.5)  == "24h"    # < 48h threshold → hours (floored)


def test_format_digest_unknown_guest_no_crash():
    """guest_name=None → renders 'unknown guest', does not crash."""
    finding = _f(
        title="Unanswered inquiry — unknown guest last replied 10.0h ago",
        detail="inquiry=abc gap=10.0h | ages out in 326h",
        guest_name=None,
    )
    digest = format_digest([finding], NOW)
    assert "unknown guest" in digest


# ── Property name shortening ──────────────────────────────────────────────────

def test_short_prop_ensuite():
    assert _short_prop("Villa del Encanto | Ensuite Room") == "Ensuite"

def test_short_prop_queen():
    assert _short_prop("Villa del Encanto | Queen Room") == "Queen"

def test_short_prop_whole_unit():
    assert _short_prop("Villa del Encanto - Ensuite and Queen") == "Whole unit"

def test_short_prop_unknown_falls_back_to_raw():
    assert _short_prop("Some Other Property") == "Some Other Property"

def test_short_prop_appears_in_digest():
    """Bullet uses short label; the 'Villa del Encanto |' room prefix is dropped."""
    f = _f(property_name="Villa del Encanto | Ensuite Room")
    digest = format_digest([f], NOW)
    bullet = next(ln for ln in digest.splitlines() if ln.startswith("•"))
    assert "Ensuite" in bullet
    # The room-qualifier pipe must not appear in the bullet
    assert "Villa del Encanto |" not in bullet


# ── Turnover reword ───────────────────────────────────────────────────────────

def _turnover_finding(
    departing: str = "Mike",
    arriving: str = "Karina",
    dep_date: str = "2025-07-05",
    arr_date: str = "2025-07-14",
    gap: int = 9,
    prop: str = "Villa del Encanto | Queen Room",
) -> Finding:
    return _f(
        check="turnover_gap",
        severity=Severity.LOW,
        property_name=prop,
        title="Turnover gap — 4+ days",
        detail=(
            f"OUT: {prop} {dep_date} ({departing})"
            f" → IN: {prop} {arr_date} ({arriving})"
            f" | gap={gap}d"
        ),
        guest_name=arriving,
    )

def test_turnover_line_shape():
    """Turnover line leads with arriving guest + date, departing as context, one gap figure."""
    digest = format_digest([_turnover_finding()], NOW)
    assert "arriving" in digest
    assert "left" in digest
    assert "Queen" in digest        # short label
    assert "9-day gap" in digest

def test_turnover_no_old_format():
    """Old double-gap format ('4+ days (Nd gap)') must not appear."""
    digest = format_digest([_turnover_finding()], NOW)
    assert "4+ days" not in digest
    # "9d gap" (old parenthesised form) must not appear; "9-day gap" is the new form
    assert "(9d gap)" not in digest

def test_turnover_date_as_md():
    """Dates render as M/D, not ISO."""
    digest = format_digest([_turnover_finding()], NOW)
    assert "7/14" in digest
    assert "7/5" in digest


# ── Inquiry "drops off" reword ────────────────────────────────────────────────

def test_inquiry_no_ages_out():
    """'ages out' must not appear anywhere in the human digest."""
    f = _f(
        title="Unanswered inquiry — Christina last replied 178.0h ago",
        detail="inquiry=abc gap=178.0h | ages out in 158h",
        guest_name="Christina",
    )
    digest = format_digest([f], NOW)
    assert "ages out" not in digest

def test_inquiry_drops_off_wording():
    """New drop-off wording is present and uses _DROPS_OFF_TMPL."""
    f = _f(
        title="Unanswered inquiry — Alice last replied 5.0h ago",
        detail="inquiry=abc gap=5.0h | ages out in 331h",
        guest_name="Alice",
    )
    digest = format_digest([f], NOW)
    assert "drops off" in digest


# ── Rounding collision ────────────────────────────────────────────────────────

def test_rounding_collision_different_strings():
    """Elapsed and remaining that differ must not render as the same string."""
    # 178h elapsed ≈ 7.4 days; 158h remaining ≈ 6.6 days — both naively round to ~7 days
    f = _f(
        title="Unanswered inquiry — Christina last replied 178.0h ago",
        detail="inquiry=abc gap=178.0h | ages out in 158h",
        guest_name="Christina",
    )
    digest = format_digest([f], NOW)
    # Extract the two duration strings from the bullet line
    line = next(ln for ln in digest.splitlines() if "Christina" in ln)
    # "no reply in X ... drops off in Y"
    m = re.search(r"no reply in (.+?) \(", line)
    elapsed_str = m.group(1) if m else ""
    m2 = re.search(r"drops off in (.+?) if", line)
    remaining_str = m2.group(1) if m2 else ""
    assert elapsed_str != remaining_str, (
        f"Elapsed and remaining rendered identically: {elapsed_str!r}"
    )

def test_h_to_human_precise_near_boundary():
    """_h_to_human_precise uses decimal when value is off-centre."""
    # 178h / 24 = 7.417 — not close to 7 (diff = 0.417 > 0.15) → show decimal
    assert _h_to_human_precise(178.0) == "7.4 days"
    # 168h / 24 = 7.0 exactly → "~7 days"
    assert _h_to_human_precise(168.0) == "~7 days"


# ── Secondary sort within severity tier ──────────────────────────────────────

def test_secondary_sort_turnover_before_kh():
    """Within LOW, turnover_gap finding appears before knowledge_hub_hygiene."""
    kh = _f(
        check="knowledge_hub_hygiene",
        severity=Severity.LOW,
        title='KH topic "Parking" has duplicate items (2× items)',
        detail="topic=Parking | 2 duplicate items",
    )
    turnover = _turnover_finding()
    # Pass KH first to prove sort overrides insertion order
    digest = format_digest([kh, turnover], NOW)
    assert digest.index("arriving") < digest.index("KH duplicate"), (
        "turnover_gap bullet should appear before knowledge_hub_hygiene bullet"
    )


# ── Verbose formatter ─────────────────────────────────────────────────────────

def test_format_verbose_preserves_entity_and_detail():
    """Verbose format emits entity= UUID prefix and the raw detail with full timestamp."""
    finding = _f()
    verbose = format_verbose([finding])
    assert "entity=b2da813b" in verbose                        # truncated UUID
    assert "last_message_at=2025-07-05T07:00:00Z" in verbose  # raw timestamp preserved


# ── Truncation ────────────────────────────────────────────────────────────────

def test_truncation_at_finding_boundary():
    """Over-limit digest truncates before a bullet, appends '…(N more)', stays within budget."""
    findings = [
        _f(
            severity=Severity.HIGH,
            guest_name=f"Guest{i}",
            title=f"Unanswered inquiry — Guest{i} last replied {i * 10}.0h ago",
            detail=f"inquiry=abc gap={i * 10}.0h | ages out in {336 - i * 10}h",
        )
        for i in range(1, 10)  # 9 findings
    ]
    digest = format_digest(findings, NOW)

    small_limit = 300  # well below the real 4096 to force truncation
    result = truncate_digest(digest, max_utf16=small_limit)

    assert _utf16_len(result) <= small_limit, "Result exceeds UTF-16 budget"
    assert "more)" in result, "Expected '…(N more)' suffix"

    # Every retained bullet must be an unmodified original bullet line
    original_bullets = {ln for ln in digest.split("\n") if ln.startswith("•")}
    for ln in result.split("\n"):
        if ln.startswith("•"):
            assert ln in original_bullets, f"Partial/modified bullet: {ln!r}"

    # Count check: N in "…(N more)" must equal omitted bullets
    result_lines = result.split("\n")
    kept_bullets = sum(1 for ln in result_lines if ln.startswith("•"))
    omitted = len(findings) - kept_bullets
    if omitted > 0:
        assert f"…({omitted} more)" in result


# ── Telegram send ─────────────────────────────────────────────────────────────

def test_telegram_send_payload_shape(monkeypatch):
    """send_digest posts correct payload shape; no real network call is made."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    from hospitable.telegram import send_digest

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None

    with patch("hospitable.telegram.requests.post", return_value=mock_resp) as mock_post:
        send_digest("Test digest text")

    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    payload = mock_post.call_args[1]["json"]

    assert "api.telegram.org" in call_url
    assert payload["chat_id"] == "12345"
    assert "Test digest text" in payload["text"]
    # Token goes in the URL path (Telegram API design) — must NOT appear in the JSON body
    assert "test-token-abc" not in str(payload)


def test_telegram_missing_token_raises(monkeypatch):
    """Missing TELEGRAM_BOT_TOKEN → RuntimeError with descriptive message; no HTTP call."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from hospitable.telegram import send_digest

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        send_digest("Test digest")


def test_telegram_uses_default_chat_id(monkeypatch):
    """When TELEGRAM_CHAT_ID is unset, the default channel ID is used."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    from hospitable import telegram as tg_module
    from hospitable.telegram import send_digest

    with patch("hospitable.telegram.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.return_value = None
        send_digest("Default chat test")

    payload = mock_post.call_args[1]["json"]
    assert payload["chat_id"] == tg_module._DEFAULT_CHAT_ID
