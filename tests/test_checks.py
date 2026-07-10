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
from checks.turnover_gap import check_turnover_gap
from checks.smartlock_battery import check_smartlock_battery

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
        kh={},
        client=None,
        smartlocks=[],
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
    """pending + can_be_sent_now=True + future expires_at → 1 MEDIUM finding (A3 re-map)."""
    audit = _empty_audit(reviews=[_review("rev-0001", "pending", True, expires_at="2025-07-15")])
    findings = check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "actionable_review"
    assert f.severity == Severity.MEDIUM
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


def test_kh_duplicate_topic_fires():
    """Same topic name twice → 1 MEDIUM duplicate finding."""
    d = (NOW - datetime.timedelta(days=10)).isoformat()
    kh = _kh([
        _topic("Parking", [_item(d)]),
        _topic("Parking", [_item(d)]),
        _topic("Check-in", [_item(d)]),
    ])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    dupes = [f for f in findings if "Duplicate" in f.title]
    assert len(dupes) == 1
    assert "parking" in dupes[0].title.lower()
    assert "(2×)" in dupes[0].title
    assert dupes[0].severity == Severity.LOW  # A3 re-map: KH hygiene → LOW


def test_kh_duplicate_topic_case_insensitive_fires():
    """Topic names differing only by case/whitespace count as duplicates."""
    d = (NOW - datetime.timedelta(days=10)).isoformat()
    kh = _kh([
        _topic("Parking", [_item(d)]),
        _topic("parking ", [_item(d)]),  # different case + trailing space
    ])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    dupes = [f for f in findings if "Duplicate" in f.title]
    assert len(dupes) == 1
    assert "(2×)" in dupes[0].title


def test_kh_no_duplicates_no_finding():
    """Unique topic names → no duplicate finding."""
    d = (NOW - datetime.timedelta(days=10)).isoformat()
    kh = _kh([
        _topic("Parking", [_item(d)]),
        _topic("Check-in", [_item(d)]),
    ])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    assert check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_kh_duplicate_fires_stale_not_triggered():
    """Old items + duplicate topic → duplicate finding only; no stale finding."""
    old = (NOW - datetime.timedelta(days=190)).isoformat()
    fresh = (NOW - datetime.timedelta(days=5)).isoformat()
    kh = _kh([
        _topic("Parking", [_item(old)]),
        _topic("Parking", [_item(fresh)]),
    ])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    assert any("Duplicate" in f.title for f in findings)
    assert not any("Stale" in f.title for f in findings)


def test_kh_old_unique_content_no_finding():
    """261-day-old but unique content → no finding (age no longer triggers)."""
    old = (NOW - datetime.timedelta(days=261)).isoformat()
    kh = _kh([_topic("Address", [_item(old, "461 15th St NE, Washington DC 20002")])])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    assert check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_kh_duplicate_items_within_topic_fires():
    """Two items in the same topic with identical content → 1 finding."""
    d = (NOW - datetime.timedelta(days=5)).isoformat()
    kh = _kh([_topic("House Rules", [
        _item(d, "No smoking inside the property."),
        _item(d, "No smoking inside the property."),  # exact duplicate
    ])])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    item_dupes = [f for f in findings if "identical content" in f.title]
    assert len(item_dupes) == 1
    assert "House Rules" in item_dupes[0].title
    assert "(2×" in item_dupes[0].title


def test_kh_duplicate_items_case_whitespace_normalized():
    """Items differing only by case/whitespace count as identical."""
    d = (NOW - datetime.timedelta(days=5)).isoformat()
    kh = _kh([_topic("WiFi", [
        _item(d, "Password: guest123"),
        _item(d, "  Password:  guest123  "),  # extra whitespace
        _item(d, "PASSWORD: GUEST123"),       # different case
    ])])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    item_dupes = [f for f in findings if "identical content" in f.title]
    assert len(item_dupes) == 1
    assert "(3×" in item_dupes[0].title


def test_kh_distinct_items_no_duplicate_finding():
    """Distinct item content within a topic → no finding."""
    d = (NOW - datetime.timedelta(days=5)).isoformat()
    kh = _kh([_topic("House Rules", [
        _item(d, "No smoking inside the property."),
        _item(d, "No parties or events allowed."),
    ])])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    assert check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_kh_empty_hub_no_findings():
    """No topics → no findings."""
    audit = _empty_audit(kh={"aaaa-0001": _kh([])})
    assert check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG) == []


# ── check_turnover_gap ────────────────────────────────────────────────────────

def _res(
    uuid: str,
    checkout: str,
    status: str = "confirmed",
    prop_uuid: str = "aaaa-0001",
    checkin: str | None = None,
) -> dict:
    d: dict = {
        "uuid": uuid, "id": uuid,
        "status": status,
        "check_out": checkout,
        "property_uuid": prop_uuid,
    }
    if checkin is not None:
        d["check_in"] = checkin
    return d


def test_turnover_gap_same_day_critical():
    """Prior checkout and next check-in on same day → CRITICAL."""
    out_date = str(TODAY + datetime.timedelta(days=3))
    reservations = [
        _res("res-out", out_date, checkin=str(TODAY)),           # departing
        _res("res-in",  out_date, checkin=out_date),             # arriving same day
    ]
    audit = _empty_audit(reservations=reservations)
    findings = check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "turnover_gap"
    assert f.severity == Severity.CRITICAL
    assert f.entity_id == "res-in"
    assert "same-day" in f.title  # A4: tightness label is in title, not detail


def test_turnover_gap_one_day_high():
    """1-day gap → HIGH."""
    cout = str(TODAY + datetime.timedelta(days=2))
    cin  = str(TODAY + datetime.timedelta(days=3))
    reservations = [
        _res("res-out", cout, checkin=str(TODAY)),
        _res("res-in",  cin,  checkin=cin),
    ]
    audit = _empty_audit(reservations=reservations)
    findings = check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


def test_turnover_gap_three_days_medium():
    """3-day gap → MEDIUM."""
    cout = str(TODAY + datetime.timedelta(days=2))
    cin  = str(TODAY + datetime.timedelta(days=5))
    reservations = [
        _res("res-out", cout, checkin=str(TODAY)),
        _res("res-in",  cin,  checkin=cin),
    ]
    audit = _empty_audit(reservations=reservations)
    findings = check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert findings[0].severity == Severity.MEDIUM


def test_turnover_gap_five_days_low():
    """5-day gap → LOW."""
    cout = str(TODAY + datetime.timedelta(days=1))
    cin  = str(TODAY + datetime.timedelta(days=6))
    reservations = [
        _res("res-out", cout, checkin=str(TODAY)),
        _res("res-in",  cin,  checkin=cin),
    ]
    audit = _empty_audit(reservations=reservations)
    findings = check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert findings[0].severity == Severity.LOW


def test_turnover_gap_cross_room_same_day_critical():
    """Checkout on PROP_A, check-in on PROP_B, same day → CRITICAL (combined calendar)."""
    same_day = str(TODAY + datetime.timedelta(days=3))
    reservations = [
        _res("res-ensuite-out", same_day, checkin=str(TODAY), prop_uuid="aaaa-0001"),
        _res("res-queen-in",   same_day, checkin=same_day,   prop_uuid="bbbb-0002"),
    ]
    audit = _empty_audit(reservations=reservations)
    findings = check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == Severity.CRITICAL
    assert f.property_uuid == "bbbb-0002"   # arriving guest's property
    assert "aaaa-0001" not in f.detail or "bbbb-0002" in f.detail  # both rooms in detail


def test_turnover_gap_beyond_lookahead_no_finding():
    """Check-in beyond the lookahead window → not surfaced."""
    cout = str(TODAY + datetime.timedelta(days=2))
    cin  = str(TODAY + datetime.timedelta(days=20))  # beyond default 7d
    reservations = [
        _res("res-out", cout, checkin=str(TODAY)),
        _res("res-in",  cin,  checkin=cin),
    ]
    audit = _empty_audit(reservations=reservations)
    assert check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_turnover_gap_cancelled_ignored_in_prior_checkout():
    """Cancelled reservation is excluded from combined calendar — no valid prior → skip."""
    same_day = str(TODAY + datetime.timedelta(days=3))
    reservations = [
        _res("res-cancel", same_day, status="cancelled", checkin=str(TODAY)),  # excluded
        _res("res-in",     same_day, checkin=same_day),
    ]
    audit = _empty_audit(reservations=reservations)
    # Only prior is cancelled → no valid prior → check-in skipped
    assert check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_turnover_gap_not_accepted_ignored():
    """not_accepted reservation is excluded via deny-list — no valid prior → skip."""
    same_day = str(TODAY + datetime.timedelta(days=3))
    reservations = [
        _res("res-not-accepted", same_day, status="not_accepted", checkin=str(TODAY)),
        _res("res-in",           same_day, checkin=same_day),
    ]
    audit = _empty_audit(reservations=reservations)
    assert check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG) == []


# ── check_smartlock_battery ───────────────────────────────────────────────────

def _lock(
    dev_id: str = "d4a4673b",
    name: str = "Front",
    pct: int | None = 54,
    threshold: int | None = 30,
    status: str = "good",
    online: bool = True,
    issues: list | None = None,
    state_online: bool | None = None,
) -> dict:
    """Synthetic smartlock device dict matching live API shape."""
    battery: dict = {"status": status}
    if pct is not None:
        battery["percentage"] = pct
    if threshold is not None:
        battery["threshold"] = threshold
    state: dict = {"battery": battery, "locked": True}
    if state_online is not None:
        state["online"] = state_online
    return {
        "id": dev_id, "name": name,
        "device_type": "smartlock",
        "online": online,
        "issues": issues if issues is not None else [],
        "state": state,
        "_prop_uuid": "aaaa-0001",
    }


def test_smartlock_healthy_no_finding():
    """54%, threshold 30, status good, online, no issues → no finding."""
    audit = _empty_audit(smartlocks=[_lock(pct=54, threshold=30, status="good")])
    assert check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_smartlock_49pct_status_low_no_finding():
    """Current live state: 49%, threshold 30, status 'low', online, empty issues → NO finding.
    Proves we do not flag on battery.status alone — status 'low' is not a trigger."""
    audit = _empty_audit(smartlocks=[_lock(pct=49, threshold=30, status="low")])
    assert check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG) == []


def test_smartlock_below_threshold_critical():
    """29% < threshold 30% → CRITICAL (primary tripwire a)."""
    audit = _empty_audit(smartlocks=[_lock(pct=29, threshold=30, status="low")])
    findings = check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
    assert "configured threshold" in findings[0].detail


def test_smartlock_floor_backstop_no_threshold():
    """19% with threshold missing → FLAGGED via floor backstop (tripwire b, default 20%)."""
    audit = _empty_audit(smartlocks=[_lock(pct=19, threshold=None, status="low")])
    findings = check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "floor" in findings[0].detail


def test_smartlock_issues_entry_critical():
    """issues[] has low-battery entry, 50%, status 'good' → FLAGGED (tripwire c)."""
    issues = [{"type": "low_battery", "description": "Battery is low"}]
    audit = _empty_audit(smartlocks=[_lock(pct=50, threshold=30, status="good", issues=issues)])
    findings = check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "issues[]" in findings[0].detail


def test_smartlock_battery_object_missing_critical():
    """battery object missing from state → FLAGGED (tripwire d — uncertainty)."""
    lock = {**_lock(), "state": {}}  # state present but no 'battery' key
    audit = _empty_audit(smartlocks=[lock])
    findings = check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "battery data missing" in findings[0].detail


def test_smartlock_percentage_none_critical():
    """battery.percentage None → FLAGGED (tripwire d — uncertainty)."""
    audit = _empty_audit(smartlocks=[_lock(pct=None, threshold=30)])
    findings = check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "percentage missing" in findings[0].detail


def test_smartlock_device_offline_critical():
    """device.online False → FLAGGED (tripwire e — cannot confirm guest entry)."""
    audit = _empty_audit(smartlocks=[_lock(online=False)])
    findings = check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "offline" in findings[0].detail


def test_smartlock_state_online_false_critical():
    """state.online False → FLAGGED (tripwire e)."""
    audit = _empty_audit(smartlocks=[_lock(state_online=False)])
    findings = check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "offline" in findings[0].detail


def test_smartlock_dedup_same_id_yields_one():
    """Same device id from 3 listings → _dedup_smartlocks produces exactly one device."""
    from checks.runner import _dedup_smartlocks
    readings = [_lock("dev-x"), _lock("dev-x"), _lock("dev-x")]
    assert len(_dedup_smartlocks(readings)) == 1


def test_smartlock_dedup_offline_wins():
    """Two readings of same lock — one online, one offline → merged device is offline."""
    from checks.runner import _dedup_smartlocks
    online_r  = _lock("dev-y", pct=49, online=True)
    offline_r = _lock("dev-y", pct=49, online=False)
    result = _dedup_smartlocks([online_r, offline_r])
    assert len(result) == 1
    assert result[0].get("online") is False
    # And the check flags the merged device
    audit = _empty_audit(smartlocks=result)
    findings = check_smartlock_battery(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "offline" in findings[0].detail


# ── A5 tests: guest names, escalation, severity re-map, label text ────────────

def test_unanswered_inquiry_guest_name_in_title(monkeypatch):
    """Guest first name surfaced in title when present."""
    thread = {
        "messages": [_msg("guest", 5.0)],
        "guest": {"first_name": "Maria", "last_name": "S"},
    }
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-name-a")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "Maria" in findings[0].title
    assert findings[0].guest_name == "Maria"


def test_unanswered_inquiry_unknown_guest_fallback(monkeypatch):
    """No guest object → title reads 'unknown guest'; no crash."""
    thread = {"messages": [_msg("guest", 5.0)]}  # no 'guest' key
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-name-b")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "unknown guest" in findings[0].title
    assert findings[0].guest_name is None


def test_unanswered_inquiry_40h_is_high(monkeypatch):
    """40h gap < 72h escalation threshold → HIGH."""
    thread = {"messages": [_msg("guest", 40.0)]}
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-esc-a")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


def test_unanswered_inquiry_100h_is_critical(monkeypatch):
    """100h gap >= 72h escalation threshold → CRITICAL."""
    thread = {"messages": [_msg("guest", 100.0)]}
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-esc-b")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL


def test_unanswered_inquiry_ages_out_detail(monkeypatch):
    """Detail line shows gap and time until age-out."""
    thread = {"messages": [_msg("guest", 100.0)]}
    import hospitable.data as hdata
    monkeypatch.setattr(hdata, "get_inquiry_thread", lambda _c, _u: thread)

    audit = _empty_audit(inquiries=[_inq("inq-esc-c")], client=MagicMock())
    findings = check_unanswered_inquiry(audit, now=NOW, config=DEFAULT_CONFIG)
    assert "ages out in" in findings[0].detail


def test_actionable_review_severity_is_medium():
    """A3 re-map: actionable_review → MEDIUM (not HIGH)."""
    audit = _empty_audit(reviews=[_review("rev-sev", "pending", True, expires_at="2025-07-15")])
    findings = check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert findings[0].severity == Severity.MEDIUM


def test_actionable_review_guest_name_in_title():
    """Review with guest object → first name in title."""
    review = _review("rev-gn", "pending", True, expires_at="2025-07-15",
                     guest={"first_name": "James", "last_name": "T"})
    audit = _empty_audit(reviews=[review])
    findings = check_actionable_review(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "James" in findings[0].title
    assert findings[0].guest_name == "James"


def test_kh_severity_is_low():
    """A3 re-map: knowledge_hub_hygiene → LOW (not MEDIUM)."""
    d = (NOW - datetime.timedelta(days=10)).isoformat()
    kh = _kh([_topic("Parking", [_item(d)]), _topic("Parking", [_item(d)])])
    audit = _empty_audit(kh={"aaaa-0001": kh})
    findings = check_knowledge_hub_hygiene(audit, now=NOW, config=DEFAULT_CONFIG)
    assert all(f.severity == Severity.LOW for f in findings)


def test_turnover_gap_title_tightness_label():
    """A4: title uses tightness label; detail has numeric gap once; no duplication."""
    cout = str(TODAY + datetime.timedelta(days=1))
    cin  = str(TODAY + datetime.timedelta(days=6))  # 5-day gap → LOW / "4+ days"
    reservations = [
        _res("res-out", cout, checkin=str(TODAY)),
        _res("res-in",  cin,  checkin=cin),
    ]
    audit = _empty_audit(reservations=reservations)
    findings = check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    f = findings[0]
    assert "4+ days" in f.title
    assert "5d gap" not in f.title   # old repeated pattern must not appear
    assert "gap=5d" in f.detail


def test_turnover_gap_guest_names_in_detail():
    """Departing and arriving guest names appear in detail."""
    cout = str(TODAY + datetime.timedelta(days=2))
    cin  = str(TODAY + datetime.timedelta(days=3))
    res_out = {**_res("res-out", cout, checkin=str(TODAY)), "guest": {"first_name": "Maria"}}
    res_in  = {**_res("res-in",  cin,  checkin=cin),        "guest": {"first_name": "James"}}
    audit = _empty_audit(reservations=[res_out, res_in])
    findings = check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "Maria" in findings[0].detail
    assert "James" in findings[0].detail


def test_turnover_gap_unknown_guest_fallback():
    """No guest object → detail shows 'unknown guest'; no crash."""
    cout = str(TODAY + datetime.timedelta(days=2))
    cin  = str(TODAY + datetime.timedelta(days=3))
    reservations = [
        _res("res-out", cout, checkin=str(TODAY)),
        _res("res-in",  cin,  checkin=cin),
    ]
    audit = _empty_audit(reservations=reservations)
    findings = check_turnover_gap(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) == 1
    assert "unknown guest" in findings[0].detail


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

    same_day = str(TODAY + datetime.timedelta(days=3))
    audit = AuditData(
        props=[PROP_A],
        inquiries=[],
        reviews=[_review("rev-x", "pending", True, expires_at="2025-07-15")],
        reservations=[
            _res("res-out", same_day, checkin=str(TODAY)),   # prior checkout
            _res("res-in",  same_day, checkin=same_day),     # same-day → CRITICAL
        ],
        kh={},
        client=MagicMock(),
    )
    findings = run_all(audit, now=NOW, config=DEFAULT_CONFIG)
    assert len(findings) >= 2, "Expected at least CRITICAL (turnover) and MEDIUM (review)"
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, reverse=True), "Findings not sorted high→low"
    assert findings[0].severity == Severity.CRITICAL
