"""
Unit tests for build_occupancy_view().

Synthetic fixtures only — no live API calls.
"""
from __future__ import annotations

import datetime

from checks.finding import AuditData, PropertyOccupancy
from checks.runner import build_occupancy_view

NOW = datetime.datetime(2026, 7, 24, 12, 0, 0, tzinfo=datetime.timezone.utc)
TODAY = NOW.date()  # 2026-07-24

# ── Synthetic properties ──────────────────────────────────────────────────────

_PC_CHILD  = {"type": "child",  "parent": "4d22b785", "children": [], "siblings": ["307872bb"]}
_PC_CHILD2 = {"type": "child",  "parent": "4d22b785", "children": [], "siblings": ["249a5a68"]}
_PC_PARENT = {"type": "parent", "parent": None,       "children": ["249a5a68", "307872bb"], "siblings": []}

ENSUITE = {"uuid": "249a5a68", "name": "Villa del Encanto | Ensuite Room",       "parent_child": _PC_CHILD}
QUEEN   = {"uuid": "307872bb", "name": "Villa del Encanto | Queen Room",          "parent_child": _PC_CHILD2}
WHOLE   = {"uuid": "4d22b785", "name": "Villa del Encanto - Ensuite and Queen",   "parent_child": _PC_PARENT}


def _audit(*props, reservations=None):
    return AuditData(
        props=list(props),
        inquiries=[],
        reviews=[],
        reservations=reservations or [],
        kh={},
        client=None,
    )


def _res(prop_uuid, checkin, checkout, guest_first="Alice", status=None):
    """Minimal synthetic reservation dict."""
    return {
        "property_uuid": prop_uuid,
        "check_in": checkin,
        "check_out": checkout,
        "guest": {"first_name": guest_first},
        "reservation_status": {"current": {"category": status or "accepted"}},
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_occupied_room():
    """Property with a current guest returns name, checkout, and nights."""
    res = _res(ENSUITE["uuid"], "2026-07-22", "2026-07-26", guest_first="Cardon")
    occ = build_occupancy_view(_audit(ENSUITE, reservations=[res]), NOW)
    assert len(occ) == 1
    o = occ[0]
    assert o.guest_name == "Cardon"
    assert o.checkout_date == datetime.date(2026, 7, 26)
    assert o.nights == 4
    assert o.next_checkin is None
    assert not o.is_whole_unit


def test_empty_with_upcoming():
    """Vacant property with a future reservation returns next_checkin."""
    res = _res(QUEEN["uuid"], "2026-07-28", "2026-07-31", guest_first="Priya")
    occ = build_occupancy_view(_audit(QUEEN, reservations=[res]), NOW)
    assert len(occ) == 1
    o = occ[0]
    assert o.guest_name is None
    assert o.checkout_date is None
    assert o.nights is None
    assert o.next_checkin == datetime.date(2026, 7, 28)


def test_empty_no_upcoming():
    """Vacant property with no future reservations returns next_checkin=None."""
    res = _res(ENSUITE["uuid"], "2026-07-10", "2026-07-20", guest_first="Sam")
    occ = build_occupancy_view(_audit(ENSUITE, reservations=[res]), NOW)
    assert len(occ) == 1
    o = occ[0]
    assert o.guest_name is None
    assert o.next_checkin is None


def test_whole_unit_booking():
    """Parent property with current booking is marked is_whole_unit=True."""
    res = _res(WHOLE["uuid"], "2026-07-21", "2026-07-26", guest_first="Priya")
    occ = build_occupancy_view(_audit(WHOLE, reservations=[res]), NOW)
    assert len(occ) == 1
    o = occ[0]
    assert o.is_whole_unit is True
    assert o.guest_name == "Priya"
    assert o.nights == 5


def test_cancelled_reservation_excluded():
    """Cancelled reservations are not treated as current or upcoming."""
    res = _res(ENSUITE["uuid"], "2026-07-22", "2026-07-28", status="cancelled")
    occ = build_occupancy_view(_audit(ENSUITE, reservations=[res]), NOW)
    o = occ[0]
    assert o.guest_name is None
    assert o.next_checkin is None


def test_not_accepted_expired_excluded():
    """'not accepted' / sub_category='expired' reservation is excluded (real API shape)."""
    res = {
        "property_uuid": QUEEN["uuid"],
        "check_in": "2026-07-22",
        "check_out": "2026-07-27",
        "guest": {"first_name": "Emmanuel"},
        "reservation_status": {"current": {"category": "not accepted", "sub_category": "expired"}},
    }
    occ = build_occupancy_view(_audit(QUEEN, reservations=[res]), NOW)
    o = occ[0]
    assert o.guest_name is None
    assert o.next_checkin is None


def test_checkout_today_is_occupied():
    """A guest checking out today is still in the room at 6am digest time."""
    res = _res(QUEEN["uuid"], "2026-07-21", "2026-07-24")
    occ = build_occupancy_view(_audit(QUEEN, reservations=[res]), NOW)
    o = occ[0]
    assert o.guest_name is not None
    assert o.checkout_date == datetime.date(2026, 7, 24)


def test_checkin_today_is_occupied():
    """A guest checking in today (checkin == today) is current."""
    res = _res(ENSUITE["uuid"], "2026-07-24", "2026-07-27", guest_first="Jordan")
    occ = build_occupancy_view(_audit(ENSUITE, reservations=[res]), NOW)
    o = occ[0]
    assert o.guest_name == "Jordan"
    assert o.nights == 3


def test_multiple_properties_independent():
    """Each property is resolved independently."""
    res_e = _res(ENSUITE["uuid"], "2026-07-22", "2026-07-26", guest_first="Cardon")
    res_q = _res(QUEEN["uuid"],   "2026-07-27", "2026-07-30", guest_first="Priya")
    occ = build_occupancy_view(
        _audit(ENSUITE, QUEEN, reservations=[res_e, res_q]), NOW
    )
    assert len(occ) == 2
    by_uuid = {o.property_uuid: o for o in occ}
    assert by_uuid[ENSUITE["uuid"]].guest_name == "Cardon"
    assert by_uuid[QUEEN["uuid"]].guest_name is None
    assert by_uuid[QUEEN["uuid"]].next_checkin == datetime.date(2026, 7, 27)


def test_real_property_is_whole_unit_resolution():
    """The three real properties resolve is_whole_unit correctly from dict parent_child."""
    occ = build_occupancy_view(_audit(ENSUITE, QUEEN, WHOLE), NOW)
    by_uuid = {o.property_uuid: o for o in occ}
    assert by_uuid["249a5a68"].is_whole_unit is False  # child room
    assert by_uuid["307872bb"].is_whole_unit is False  # child room
    assert by_uuid["4d22b785"].is_whole_unit is True   # parent listing


def test_next_checkin_picks_soonest():
    """When multiple future reservations exist, next_checkin is the earliest."""
    r1 = _res(ENSUITE["uuid"], "2026-08-10", "2026-08-14")
    r2 = _res(ENSUITE["uuid"], "2026-07-30", "2026-08-02")
    occ = build_occupancy_view(_audit(ENSUITE, reservations=[r1, r2]), NOW)
    assert occ[0].next_checkin == datetime.date(2026, 7, 30)
