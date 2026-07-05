"""
Unit tests for all 4 audit checks.

All tests use synthetic fixtures — no live API calls.
audit.client is set to None; check_unanswered_inquiry receives a
pre-built mock that returns canned threads without touching the network.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from checks.finding import AuditData, CheckConfig, Finding, Severity
from checks.unanswered_inquiry import check_unanswered_inquiry
from checks.actionable_review import check_actionable_review
from checks.knowledge_hub_hygiene import check_knowledge_hub_hygiene
from checks.missing_turnover_task import check_missing_turnover_task

# ── Fixtures ──────────────────────────────────────────────────────────────────

NOW = datetime.datetime(2025, 7, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)
TODAY = NOW.date()

PROP_A = {"uuid": "aaaa-0001", "id": "aaaa-0001", "name": "Beach House"}
PROP_B = {"uuid": "bbbb-0002", "id": "bbbb-0002", "name": "Mountain Cabin"}

DEFAULT_CONFIG = CheckConfig()


def _empty_audit(**overrides) -> AuditData:
    base = dict(
        props=[PROP_A, PROP_B],
        inquiries=[],
        reviews=[],
        reservations=[],
        tasks=[],
        kh={},
        client=None,
    )
    base.update(overrides)
    return AuditData(**base)


# ── check_unanswered_inquiry ──────────────────────────────────────────────────

def _make_inquiry_client(threads: dict[str, dict]) -> MagicMock:
    """
    Mock HospitableClient whose get_inquiry_thread returns pre-built threads.
    Keyed by inquiry UUID.
    """
    import hospitable.data as hdata

    mock_client = MagicMock()

    def fake_get_inquiry_thread(client, inq_uuid):
        return threads.get(inq_uuid, {"messages": []})

    # Patch at the module level so check_unanswered_inquiry's import resolves
    import checks.unanswered_inquiry as mod
    mod_get = mod.__dict__.get("hdata")
    # We'll use monkeypatching via the mock client — the check calls
    # hdata.get_inquiry_thread(audit.client, inq_uuid), so we intercept via
    # a patched hdata in the module namespace.
    return mock_client


def _msg(sender: str, hours_ago: float) -> dict:
    """Build a synthetic message dict."""
    ts = NOW - datetime.timedelta(hours=hours_ago)
    return {
        "sender": sender,
        "sender_type": sender,
        "created_at": ts.isoformat(),
    }


def _inq(uuid: str, prop_uuid: str = "aaaa-0001") -> dict:
    return {"uuid": uuid, "id": uuid, "property_uuid": prop_uuid}


def test_unanswered_inquiry_flags_guest_last_over_threshold(monkeypatch):
    """Last sender = guest, gap > threshold → 1 finding."""
    inq_uuid = "inq-0001"
    thread = {"messages": [_msg("host", 10), _msg("guest", 4)]}  # 4h ago

    import checks.unanswered_inquiry as mod
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _client, uuid: thread)

    audit = _empty_audit(
        inquiries=[_inq(inq_uuid)],
        client=MagicMock(),
    )
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "unanswered_inquiry"
    assert f.severity == Severity.HIGH
    assert f.entity_id == inq_uuid
    assert "4.0h" in f.title


def test_unanswered_inquiry_host_last_no_finding(monkeypatch):
    """Last sender = host → no finding regardless of gap."""
    thread = {"messages": [_msg("guest", 20), _msg("host", 5)]}

    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _client, uuid: thread)

    audit = _empty_audit(inquiries=[_inq("inq-0002")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert findings == []


def test_unanswered_inquiry_under_threshold_no_finding(monkeypatch):
    """Last sender = guest but gap < threshold → no finding."""
    thread = {"messages": [_msg("guest", 1)]}  # 1h ago, threshold=3h

    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _client, uuid: thread)

    audit = _empty_audit(inquiries=[_inq("inq-0003")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert findings == []


def test_unanswered_inquiry_exactly_at_threshold_fires(monkeypatch):
    """Gap == threshold → fires (spec says >=, not >)."""
    thread = {"messages": [_msg("guest", 3.0)]}  # exactly 3h

    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _client, uuid: thread)

    audit = _empty_audit(inquiries=[_inq("inq-0004")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1


def test_unanswered_inquiry_just_under_threshold_no_finding(monkeypatch):
    """Gap just under threshold → no finding."""
    thread = {"messages": [_msg("guest", 2.9)]}

    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _client, uuid: thread)

    audit = _empty_audit(inquiries=[_inq("inq-0004b")], client=MagicMock())
    assert check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_unanswered_inquiry_410_skipped(monkeypatch):
    """410 from the API means inquiry became a reservation — skip it."""
    import requests
    import hospitable.data as hdata

    resp = MagicMock()
    resp.status_code = 410
    exc = requests.HTTPError(response=resp)
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _client, uuid: (_ for _ in ()).throw(exc))

    audit = _empty_audit(inquiries=[_inq("inq-0005")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert findings == []


def test_unanswered_inquiry_empty_thread_skipped(monkeypatch):
    """Inquiry with no messages → skip."""
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: {"messages": []})

    audit = _empty_audit(inquiries=[_inq("inq-0006")], client=MagicMock())
    assert check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_unanswered_inquiry_within_max_age_window_flagged(monkeypatch):
    """Gap within [stale_hours, max_age_hours] → flagged."""
    thread = {"messages": [_msg("guest", 100.0)]}  # 100h: between 3h and 336h
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-0007")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "100.0h" in findings[0].title


def test_unanswered_inquiry_above_max_age_no_finding(monkeypatch):
    """Gap > inquiry_max_age_hours → not flagged (dead/spam thread)."""
    thread = {"messages": [_msg("guest", 400.0)]}  # 400h > 336h default
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-0008")], client=MagicMock())
    assert check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_unanswered_inquiry_exactly_at_max_age_fires(monkeypatch):
    """Gap exactly at inquiry_max_age_hours → fires (inclusive upper bound, <= not <)."""
    thread = {"messages": [_msg("guest", 336.0)]}  # exactly 336h = 14d
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-0009")], client=MagicMock())
    assert len(check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)) == 1


def test_unanswered_inquiry_closed_status_skipped(monkeypatch):
    """Inquiry with a terminal status → skipped before thread is fetched."""
    import hospitable.data as hdata
    # Thread would fire if fetched (guest replied 10h ago, within window)
    thread = {"messages": [_msg("guest", 10.0)]}
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    for closed_status in ("cancelled", "declined", "expired", "withdrawn", "closed"):
        inq = {**_inq(f"inq-closed-{closed_status}"), "status": closed_status}
        audit = _empty_audit(inquiries=[inq], client=MagicMock())
        result = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
        assert result == [], f"Expected no finding for status={closed_status!r}, got {result}"


def test_unanswered_inquiry_custom_max_age(monkeypatch):
    """inquiry_max_age_hours is respected from CheckConfig."""
    thread = {"messages": [_msg("guest", 50.0)]}  # 50h ago
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-0010")], client=MagicMock())
    # Default max=336h → 50h is within window → flagged
    assert len(check_unanswered_inquiry(audit, now=NOW, config=CheckConfig())) == 1
    # Tight max=24h → 50h exceeds window → not flagged
    assert check_unanswered_inquiry(audit, now=NOW, config=CheckConfig(inquiry_max_age_hours=24)) == []


# ── check_actionable_review ───────────────────────────────────────────────────

def _review(uuid: str, status: str, can_send: bool, **extra) -> dict:
    return {
        "uuid": uuid, "id": uuid,
        "status": status,
        "can_be_sent_now": can_send,
        "property_uuid": "aaaa-0001",
        **extra,
    }


def test_actionable_review_pending_can_send_fires():
    """pending + can_be_sent_now=True + future expires_at → 1 finding."""
    audit = _empty_audit(reviews=[_review("rev-0001", "pending", True, expires_at="2025-07-15")])
    findings = check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "actionable_review"
    assert f.severity == Severity.HIGH
    assert f.entity_id == "rev-0001"


def test_actionable_review_submitted_skipped():
    """Already submitted → no finding."""
    audit = _empty_audit(reviews=[_review("rev-0002", "submitted", True)])
    assert check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_actionable_review_pending_cannot_send_skipped():
    """pending but can_be_sent_now=False → no finding."""
    audit = _empty_audit(reviews=[_review("rev-0003", "pending", False)])
    assert check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_actionable_review_with_expires_at_in_detail():
    """expires_at is surfaced in detail when present."""
    review = _review(
        "rev-0004", "pending", True,
        expires_at="2025-07-10",
        check_out="2025-06-25",
    )
    audit = _empty_audit(reviews=[review])
    findings = check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "expires=2025-07-10" in findings[0].detail


def test_actionable_review_checkout_fallback_within_window_flagged():
    """expires_at absent + checkout within window → flagged via fallback path."""
    # Checkout 4d ago; fallback deadline = 4d ago + 14d = 10d from now
    audit = _empty_audit(reviews=[_review("rev-0005", "pending", True, check_out="2025-07-01")])
    findings = check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "window=checkout+" in findings[0].detail


def test_actionable_review_multiple_only_actionable_flagged():
    """Mix of reviews — only the pending+can_send one with an open window fires."""
    reviews = [
        _review("rev-0006", "pending", True, check_out="2025-07-01"),  # 4d ago, 10d left
        _review("rev-0007", "submitted", True, check_out="2025-07-01"),
        _review("rev-0008", "pending", False, check_out="2025-07-01"),
        _review("rev-0009", "expired", False, check_out="2025-07-01"),
    ]
    audit = _empty_audit(reviews=reviews)
    findings = check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert findings[0].entity_id == "rev-0006"


def test_actionable_review_expires_at_past_no_finding():
    """expires_at present but already past → not flagged."""
    audit = _empty_audit(reviews=[_review("rev-past", "pending", True, expires_at="2025-07-04")])
    assert check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_actionable_review_checkout_beyond_window_no_finding():
    """expires_at absent + checkout > window days ago → not flagged (the real fix)."""
    # Checkout 273d ago; fallback deadline = 273d ago + 14d = 259d ago — window closed.
    review = _review("rev-old", "pending", True, check_out="2024-10-05")
    audit = _empty_audit(reviews=[review])
    assert check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_actionable_review_expires_at_exactly_today_no_finding():
    """expires_at == today → window closed (exclusive: must be > today to flag)."""
    audit = _empty_audit(reviews=[_review("rev-today", "pending", True, expires_at="2025-07-05")])
    assert check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_actionable_review_custom_window_days():
    """review_window_days is respected from CheckConfig."""
    # Checkout 10d ago; fallback deadline = 10d ago + 14d = 4d remaining → fires
    review = _review("rev-cw", "pending", True, check_out="2025-06-25")
    audit = _empty_audit(reviews=[review])
    assert len(check_actionable_review(audit, now=NOW, config=CheckConfig())) == 1
    # Tight window=7d → deadline = 10d ago + 7d = 3d ago → closed → silent
    assert check_actionable_review(audit, now=NOW, config=CheckConfig(review_window_days=7)) == []


# ── check_knowledge_hub_hygiene ───────────────────────────────────────────────

def _item(updated_at: str, content: str = "some content") -> dict:
    return {"updated_at": updated_at, "content": content, "state": "active", "is_edited": False}


def _kh(topics: list[dict]) -> dict:
    return {"topics": topics, "sources": [], "property": {}}


def _topic(name: str, items: list[dict]) -> dict:
    return {"name": name, "aggregate_items": items, "updated_at": items[0]["updated_at"] if items else None}


def test_kh_stale_item_fires():
    """Item not updated in > 180d → 1 MEDIUM stale finding."""
    old_date = (NOW - datetime.timedelta(days=200)).isoformat()
    kh = _kh([_topic("Check-in", [_item(old_date)])])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    stale = [f for f in findings if "Stale" in f.title]
    assert len(stale) == 1
    assert "200d" in stale[0].title
    assert stale[0].severity == Severity.MEDIUM


def test_kh_fresh_item_no_finding():
    """Item updated recently → no stale finding."""
    fresh_date = (NOW - datetime.timedelta(days=10)).isoformat()
    kh = _kh([_topic("Check-in", [_item(fresh_date)])])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    stale = [f for f in findings if "Stale" in f.title]
    assert stale == []


def test_kh_duplicate_topic_fires():
    """Same topic name twice → 1 MEDIUM duplicate finding."""
    kh = _kh([
        _topic("Parking", [_item((NOW - datetime.timedelta(days=10)).isoformat())]),
        _topic("Parking", [_item((NOW - datetime.timedelta(days=20)).isoformat())]),
        _topic("Check-in", [_item((NOW - datetime.timedelta(days=5)).isoformat())]),
    ])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    dupes = [f for f in findings if "Duplicate" in f.title]
    assert len(dupes) == 1
    assert "Parking" in dupes[0].title
    assert "(2×)" in dupes[0].title


def test_kh_no_duplicates_no_finding():
    """Unique topic names → no duplicate finding."""
    kh = _kh([
        _topic("Parking", [_item((NOW - datetime.timedelta(days=10)).isoformat())]),
        _topic("Check-in", [_item((NOW - datetime.timedelta(days=5)).isoformat())]),
    ])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    dupes = [f for f in findings if "Duplicate" in f.title]
    assert dupes == []


def test_kh_stale_and_duplicate_both_fire():
    """Old item + duplicate topic → both findings present."""
    old = (NOW - datetime.timedelta(days=190)).isoformat()
    fresh = (NOW - datetime.timedelta(days=5)).isoformat()
    kh = _kh([
        _topic("Parking", [_item(old)]),
        _topic("Parking", [_item(fresh)]),
    ])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    assert any("Duplicate" in f.title for f in findings)
    assert any("Stale" in f.title for f in findings)


def test_kh_empty_hub_no_findings():
    """No topics → no findings."""
    audit = _empty_audit(kh={"aaaa-0001": _kh([])})
    assert check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_kh_custom_stale_threshold():
    """Threshold respects config.kh_stale_days."""
    slightly_old = (NOW - datetime.timedelta(days=100)).isoformat()
    kh = _kh([_topic("WiFi", [_item(slightly_old)])])
    audit = _empty_audit(kh={"aaaa-0001": kh})

    # Default threshold=180 → not stale
    findings_default = check_knowledge_hub_hygiene(audit, now=NOW, config=CheckConfig(kh_stale_days=180))
    assert not any("Stale" in f.title for f in findings_default)

    # Tight threshold=90 → stale
    findings_tight = check_knowledge_hub_hygiene(audit, now=NOW, config=CheckConfig(kh_stale_days=90))
    assert any("Stale" in f.title for f in findings_tight)


# ── check_missing_turnover_task ───────────────────────────────────────────────

def _res(uuid: str, checkout: str, status: str = "confirmed", prop_uuid: str = "aaaa-0001") -> dict:
    return {
        "uuid": uuid, "id": uuid,
        "status": status,
        "check_out": checkout,
        "property_uuid": prop_uuid,
    }


def _task(prop_uuid: str, start_date: str) -> dict:
    return {"property_uuid": prop_uuid, "start_date": start_date, "uuid": "task-x"}


def test_turnover_no_task_fires():
    """Upcoming checkout with no matching task → 1 LOW finding."""
    checkout = str(TODAY + datetime.timedelta(days=3))
    audit = _empty_audit(
        reservations=[_res("res-0001", checkout)],
        tasks=[],
    )
    findings = check_missing_turnover_task(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "missing_turnover_task"
    assert f.severity == Severity.LOW
    assert f.entity_id == "res-0001"


def test_turnover_matching_task_no_finding():
    """Task on the checkout date → no finding."""
    checkout = str(TODAY + datetime.timedelta(days=3))
    audit = _empty_audit(
        reservations=[_res("res-0002", checkout)],
        tasks=[_task("aaaa-0001", checkout)],
    )
    assert check_missing_turnover_task(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_turnover_task_within_window_no_finding():
    """Task 1 day before checkout still counts (±1 day window)."""
    checkout = str(TODAY + datetime.timedelta(days=5))
    task_date = str(TODAY + datetime.timedelta(days=4))  # 1 day before
    audit = _empty_audit(
        reservations=[_res("res-0003", checkout)],
        tasks=[_task("aaaa-0001", task_date)],
    )
    assert check_missing_turnover_task(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_turnover_outside_lookahead_no_finding():
    """Checkout beyond lookahead_days → not flagged."""
    checkout = str(TODAY + datetime.timedelta(days=20))  # beyond default 14d
    audit = _empty_audit(reservations=[_res("res-0004", checkout)], tasks=[])
    assert check_missing_turnover_task(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_turnover_cancelled_reservation_skipped():
    """Cancelled reservation → not flagged even if no task."""
    checkout = str(TODAY + datetime.timedelta(days=2))
    audit = _empty_audit(
        reservations=[_res("res-0005", checkout, status="cancelled")],
        tasks=[],
    )
    assert check_missing_turnover_task(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_turnover_wrong_property_task_not_matched():
    """Task exists but for a different property → still flagged."""
    checkout = str(TODAY + datetime.timedelta(days=3))
    audit = _empty_audit(
        reservations=[_res("res-0006", checkout, prop_uuid="aaaa-0001")],
        tasks=[_task("bbbb-0002", checkout)],  # different property
    )
    findings = check_missing_turnover_task(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1


def test_turnover_custom_lookahead():
    """Respects config.turnover_lookahead_days."""
    checkout = str(TODAY + datetime.timedelta(days=10))
    audit = _empty_audit(reservations=[_res("res-0007", checkout)], tasks=[])

    # Default 14d → flagged
    assert len(check_missing_turnover_task(audit, now=NOW, config=CheckConfig())) == 1
    # Short 7d window → not flagged
    assert check_missing_turnover_task(audit, now=NOW, config=CheckConfig(turnover_lookahead_days=7)) == []


# ── Severity ordering ─────────────────────────────────────────────────────────

def test_severity_ordering():
    """Severity sorts correctly: CRITICAL > HIGH > MEDIUM > LOW."""
    assert Severity.CRITICAL > Severity.HIGH
    assert Severity.HIGH > Severity.MEDIUM
    assert Severity.MEDIUM > Severity.LOW


def test_run_all_sorted_high_to_low(monkeypatch):
    """run_all returns findings sorted high severity first."""
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: {"messages": []})

    from checks.runner import run_all
    from checks.finding import AuditData

    old_kh_date = (NOW - datetime.timedelta(days=200)).isoformat()
    checkout = str(TODAY + datetime.timedelta(days=3))

    audit = AuditData(
        props=[PROP_A],
        inquiries=[],
        reviews=[_review("rev-x", "pending", True)],
        reservations=[_res("res-x", checkout)],
        tasks=[],
        kh={"aaaa-0001": _kh([_topic("WiFi", [_item(old_kh_date)])])},
        client=MagicMock(),
    )
    findings = run_all(audit, now=NOW, config=DEFAULT_CONFIG)
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, reverse=True), "Findings not sorted high→low"
