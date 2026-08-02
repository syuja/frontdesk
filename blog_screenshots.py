#!/usr/bin/env python3
"""
Throwaway script — send synthetic digest screenshots to the blog Telegram group.

Usage:
    uv run python blog_screenshots.py              # all five in order
    uv run python blog_screenshots.py --only A     # single shot

All five screenshots derive from one shared timeline. D1 vs D2 differ only
by whether the status filter is a denylist (buggy) or an allowlist (fixed).

TELEGRAM_CHAT_ID in .env points at the blog group.
TELEGRAM_BOT_TOKEN unchanged. No production group is touched.
"""

import argparse
import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from checks.finding import Finding, Severity, PropertyOccupancy, LockStatus
from hospitable.formatters import format_digest
from hospitable.telegram import send_digest

# ── Identity constants ────────────────────────────────────────────────────────

ACCOUNT_NAME = "Maple Row"

GUEST_MARCO = "Marco"
GUEST_PRIYA = "Priya"
GUEST_LENA  = "Lena"
GUEST_NADIA = "Nadia"   # phantom-only; never appears as a real booking
GUEST_THEO  = "Theo"    # inquiry-only in B; never books

# Short names fall through _short_prop's lookup and are used as-is in output.
_E_UUID = "249a5a68"
_Q_UUID = "307872bb"
_W_UUID = "4d22b785"
_E_NAME = "Ensuite"
_Q_NAME = "Queen"
_W_NAME = "Maple Row"   # whole-unit label; appears as prop in KH findings

LOCK_OK = LockStatus(name="Front Door", pct=78.0, threshold=30.0, offline=False)

_KH_TOPIC = "Parking"   # same duplicate topic every day — KH doesn't change day to day


# ── Shared timeline ───────────────────────────────────────────────────────────
#
# One set of reservations all five screenshots render from.
# Only one entry carries status "not accepted" (the phantom driving D1).
# The real Queen check-in (Priya 7/27) starts on the SAME DAY as the phantom
# checkout so D1 shows a 0-day CRITICAL and D2 shows Priya as next arrival.
#
# Invariants:
#   - No room holds two overlapping accepted reservations.
#   - Each guest is bound to exactly one room for the entire timeline,
#     including inquiries — not just reservations.
#   - Nadia: phantom only (Queen, expired) — never a real booking.
#   - Priya: Queen only (reservation + inquiry in B).
#   - Marco: Ensuite only.
#   - Lena: Ensuite only (future arrival after Marco).

def _d(m, day):
    return datetime.date(2026, m, day)

TIMELINE = [
    # guest         room     uuid      cin         cout        status          sub_cat
    (GUEST_NADIA, _Q_NAME, _Q_UUID, _d(7, 20), _d(7, 27), "not accepted", "expired"),
    (GUEST_PRIYA, _Q_NAME, _Q_UUID, _d(7, 27), _d(7, 29), "accepted",     None),
    (GUEST_MARCO, _E_NAME, _E_UUID, _d(7, 27), _d(8,  1), "accepted",     None),
    (GUEST_LENA,  _E_NAME, _E_UUID, _d(8,  5), _d(8, 10), "accepted",     None),
    (GUEST_PRIYA, _Q_NAME, _Q_UUID, _d(8,  7), _d(8, 12), "accepted",     None),
]
# Columns: guest, room_name, room_uuid, cin, cout, status, sub_category

ROOMS = [
    {"uuid": _E_UUID, "name": _E_NAME, "is_whole_unit": False},
    {"uuid": _Q_UUID, "name": _Q_NAME, "is_whole_unit": False},
]


# ── Core derivation ───────────────────────────────────────────────────────────

def build_occupancy(now, *, accepted_only=True):
    """Derive per-room occupancy from TIMELINE as of now.date().

    accepted_only=True  → fixed behaviour (allowlist: only "accepted" bookings)
    accepted_only=False → buggy behaviour (denylist miss: all statuses pass)
    """
    today = now.date()
    result = []
    for room in ROOMS:
        rname = room["name"]
        entries = [
            r for r in TIMELINE
            if r[1] == rname and (not accepted_only or r[5] == "accepted")
        ]

        current = next(
            (r for r in entries if r[3] <= today <= r[4]),
            None,
        )
        if current:
            guest, _, _, cin, cout, *_ = current
            result.append(PropertyOccupancy(
                property_uuid=room["uuid"], property_name=rname,
                is_whole_unit=room["is_whole_unit"],
                guest_name=guest,
                checkout_date=cout,
                nights=(cout - cin).days,
                next_checkin=None,
            ))
        else:
            future = [r[3] for r in entries if r[3] > today]
            result.append(PropertyOccupancy(
                property_uuid=room["uuid"], property_name=rname,
                is_whole_unit=room["is_whole_unit"],
                guest_name=None, checkout_date=None, nights=None,
                next_checkin=min(future) if future else None,
            ))
    return result


def _kh(topic):
    """Duplicate KH topic finding. title/detail match _render_human regex."""
    return Finding(
        check="knowledge_hub_hygiene",
        severity=Severity.LOW,
        property_uuid=_W_UUID,
        property_name=_W_NAME,
        title=f'Duplicate KH topic "{topic.lower()}" (2×)',
        detail=(
            f'topic="{topic}" appears 2 times — '
            "consolidate to prevent AI serving conflicting answers"
        ),
        entity_id=_W_UUID,
    )


def _digest(findings, now, occupancy):
    return format_digest(
        findings, now,
        account_name=ACCOUNT_NAME,
        lock_statuses=[LOCK_OK],
        occupancy=occupancy,
    )


def _turnover_finding(dep_row, arr_row, severity, title_label):
    """Build a turnover_gap Finding from two TIMELINE rows.

    detail string matches _render_human regex patterns:
        OUT: <room> <YYYY-MM-DD> (<guest>) → IN: <room> <YYYY-MM-DD> (<guest>) | gap=Nd
    prop = arr_row room (arriving property label in the bullet).
    Same-room turnovers make the label unambiguous.
    """
    _, dep_room, dep_uuid, dep_cin, dep_cout, *_ = dep_row
    arr_guest, arr_room, arr_uuid, arr_cin, arr_cout, *_ = arr_row
    dep_guest = dep_row[0]
    gap = (arr_cin - dep_cout).days
    return Finding(
        check="turnover_gap",
        severity=severity,
        property_uuid=arr_uuid,
        property_name=arr_room,
        title=f"Turnover gap — {title_label}",
        detail=(
            f"OUT: {dep_room} {dep_cout.isoformat()} ({dep_guest})"
            f" → IN: {arr_room} {arr_cin.isoformat()} ({arr_guest})"
            f" | gap={gap}d"
        ),
        entity_id="synthetic-res",
        guest_name=arr_guest,
    )


# ── Shot A — product shot (Thu 2026-07-30) ───────────────────────────────────
# 2 findings (2 low) · Ensuite occupied / Queen vacant with next arrival
# Ensuite→Ensuite turnover so the room label is unambiguous in both the
# occupancy block and the finding bullet.
#
# On 7/30 (accepted-only):
#   Ensuite: Marco (cin 7/27, cout 8/1 "Sat 8/1", 5 nights)
#   Queen:   vacant — Priya out 7/29, next accepted arrival Lena... wait,
#            Lena is Ensuite. Next Queen arrival: Priya 8/7 "Fri 8/7".
# Turnover: Marco (Ensuite, out 8/1) → Lena (Ensuite, in 8/5) | gap=4d → LOW
#   → "• Lena arriving 8/5 — Marco left 8/1 (Ensuite), 4-day gap"
#   "(Ensuite)" matches Marco's room in the occupancy block ✓

def shot_a():
    now = datetime.datetime(2026, 7, 30, 11, 0, 0, tzinfo=datetime.timezone.utc)
    occupancy = build_occupancy(now)

    marco_row = next(r for r in TIMELINE if r[0] == GUEST_MARCO)
    lena_real = next(r for r in TIMELINE if r[0] == GUEST_LENA and r[5] == "accepted")
    turnover = _turnover_finding(marco_row, lena_real, Severity.LOW, "4+ days")

    return _digest([turnover, _kh(_KH_TOPIC)], now, occupancy)


# ── Shot B — escalation shot (Sat 2026-07-25) ────────────────────────────────
# 2 findings (1 critical, 1 low) · Both rooms vacant / both next in Mon 7/27
# CRITICAL unanswered inquiry: Priya, 13.5 days, 11h drop-off.
#
# On 7/25 (accepted-only): Lena phantom excluded; Marco and Priya both cin 7/27
# → both rooms "empty, next in Mon 7/27".
#
# gap_h=325.0 → _h_to_human_precise: 325/24=13.54, |13.54-14|=0.46≥0.15 → "13.5 days"
# ages_out_h=11 → "11h"  (< 48h, no rounding)
# → "• Theo — no reply in 13.5 days (Queen), drops off in 11h if unanswered"

def shot_b():
    now = datetime.datetime(2026, 7, 25, 11, 0, 0, tzinfo=datetime.timezone.utc)
    occupancy = build_occupancy(now)

    inquiry = Finding(
        check="unanswered_inquiry",
        severity=Severity.CRITICAL,
        property_uuid=_Q_UUID,
        property_name=_Q_NAME,
        title=f"Unanswered inquiry — {GUEST_THEO} last replied 325.0h ago",
        detail="inquiry=synth001 last_message_at=synthetic gap=325.0h | ages out in 11h",
        entity_id="synthetic-inq-b",
        guest_name=GUEST_THEO,
    )
    return _digest([inquiry, _kh(_KH_TOPIC)], now, occupancy)


# ── Shot C — quiet day (Mon 2026-07-27) ──────────────────────────────────────
# 1 finding (1 low) · Both rooms occupied (check-in day) · single LOW KH
#
# On 7/27 (accepted-only):
#   Ensuite: Marco (cin 7/27, cout 8/1 "Sat 8/1", 5 nights)
#   Queen:   Priya (cin 7/27, cout 7/29 "Wed 7/29", 2 nights)
# Lena's Ensuite arrival (8/5) is outside the 7-day lookahead → no turnover.

def shot_c():
    now = datetime.datetime(2026, 7, 27, 11, 0, 0, tzinfo=datetime.timezone.utc)
    occupancy = build_occupancy(now)
    return _digest([_kh(_KH_TOPIC)], now, occupancy)


# ── Shot D1 — bug: before fix (Fri 2026-07-24) ───────────────────────────────
# 2 findings (1 critical, 1 low)
#
# Nadia has an expired "not accepted" reservation in Queen (cin 7/20, cout 7/27).
# The old denylist used "not_accepted" (underscore) — never matched the API's
# "not accepted" (space) — so her record passes the filter and she appears as
# a current Queen occupant.
#
# Priya's real accepted Queen booking starts the SAME DAY as Nadia's phantom
# checkout (both 7/27). The turnover check sees: Nadia out 7/27, Priya in 7/27,
# gap=0 → CRITICAL. A false CRITICAL fired by a ghost reservation.
#
# On 7/24 (buggy filter, accepted_only=False):
#   Ensuite: vacant, next in Mon 7/27 (Marco)
#   Queen:   Nadia phantom, out Mon 7/27 (7-night)

def shot_d1():
    now = datetime.datetime(2026, 7, 24, 11, 0, 0, tzinfo=datetime.timezone.utc)
    occupancy = build_occupancy(now, accepted_only=False)

    phantom = next(r for r in TIMELINE if r[5] != "accepted")
    real_behind = next(
        r for r in TIMELINE
        if r[5] == "accepted" and r[1] == phantom[1] and r[3] == phantom[4]
    )
    turnover = _turnover_finding(phantom, real_behind, Severity.CRITICAL, "same-day")

    return _digest([turnover, _kh(_KH_TOPIC)], now, occupancy)


# ── Shot D2 — bug: after fix (Fri 2026-07-24) ────────────────────────────────
# 1 finding (1 low) — identical input data, allowlist fix applied.
#
# Lena's expired record is excluded. Queen shows vacant with next arrival =
# Priya's real accepted booking (cin 7/27 "Mon 7/27"). The CRITICAL turnover
# finding disappears entirely — it was manufactured by the phantom.
#
# D1 and D2 differ ONLY by the accepted_only flag (status filter).

def shot_d2():
    now = datetime.datetime(2026, 7, 24, 11, 0, 0, tzinfo=datetime.timezone.utc)
    occupancy = build_occupancy(now, accepted_only=True)
    return _digest([_kh(_KH_TOPIC)], now, occupancy)


# ── Entry point ───────────────────────────────────────────────────────────────

SHOTS = {"A": shot_a, "B": shot_b, "C": shot_c, "D1": shot_d1, "D2": shot_d2}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Send synthetic blog screenshots to Telegram."
    )
    parser.add_argument(
        "--only",
        choices=list(SHOTS.keys()),
        metavar="{A,B,C,D1,D2}",
        help="Send a single shot instead of all five in order.",
    )
    args = parser.parse_args()
    selected = [args.only] if args.only else list(SHOTS.keys())

    for label in selected:
        digest = SHOTS[label]()
        print(f"\n── Shot {label} ──────────────────────────────────────────────")
        print(digest)
        print("─" * 60)
        answer = input(f"\nSend Shot {label}? [y/N] ").strip().lower()
        if answer == "y":
            send_digest(digest)
            print("Sent.")
        else:
            print("Skipped.")
