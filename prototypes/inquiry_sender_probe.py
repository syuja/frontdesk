"""
Diagnostic probe: inquiry thread sender analysis.

For each falsely-flagged inquiry, pull the full raw thread from the API
and print every item in order with the fields the unanswered_inquiry check
keys off. Read-only, GET only.

Run:
    uv run prototypes/inquiry_sender_probe.py

PII policy: sender name/role printed, no emails, phones, or profile URLs.
PAT never printed.
"""
from __future__ import annotations

import sys
import os

# Ensure project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hospitable.client import HospitableClient
import hospitable.data as hdata

# ── Subjects ──────────────────────────────────────────────────────────────────

PROBES = [
    {
        "label":   "Christina",
        "uuid":    "b2da813b",   # short prefix — probe will search for the full UUID
        "note":    "I replied Jul 3 3:45pm; thread has a 'Denied' status line",
    },
    {
        "label":   "Ramy",
        "uuid":    "cc5a385a",
        "note":    "I replied Jul 10 2:30pm",
    },
    {
        "label":   "Blaze",
        "uuid":    "5cfaa995",
        "note":    "I replied Jul 8 8:04pm; thread has a 'Denied' status line",
    },
]

# Fields the check uses to determine sender — printed verbatim for each message
_SENDER_FIELDS = ("sender_type", "sender_role", "sender")

# Fields that distinguish real messages from system/status events
_KIND_FIELDS   = ("content_type", "source", "integration", "platform")


def _scrub_sender(raw) -> str:
    """Return name/type info only; drop any URL-looking values."""
    if raw is None:
        return "None"
    if isinstance(raw, dict):
        # Keep only non-URL string values (first_name, full_name, locale, location)
        safe = {
            k: v for k, v in raw.items()
            if isinstance(v, str) and "http" not in v and k not in ("picture_url", "thumbnail_url")
        }
        return str(safe) if safe else f"{{…{len(raw)} keys, URLs redacted}}"
    return repr(raw)


def _msg_sender_resolved(msg: dict) -> str:
    """Mirror hdata.msg_sender() logic exactly, show what it resolves to."""
    st   = msg.get("sender_type")
    s    = msg.get("sender")
    auth = msg.get("author")
    resolved = st or s or auth
    chain = f"sender_type={st!r} → sender={_scrub_sender(s)} → author={auth!r}"
    return f"{resolved!r}  [chain: {chain}]"


def probe_inquiry(client: HospitableClient, label: str, uuid_prefix: str, note: str) -> None:
    print()
    print("━" * 70)
    print(f"  {label}  ({uuid_prefix}…)")
    print(f"  Note: {note}")
    print("━" * 70)

    # First, find the full UUID from the inquiry list
    # We can also just call get_inquiry_thread with the prefix — the API
    # requires a full UUID. Fetch the inquiry list to resolve it.
    props_raw = client.get("/properties")
    props = props_raw.get("data", [])
    prop_uuids = [str(p.get("id") or p.get("uuid") or "") for p in props]

    full_uuid: str | None = None
    page = 1
    while True:
        params = [("properties[]", u) for u in prop_uuids]
        params += [("per_page", "50"), ("page", str(page))]
        body = client.get("/inquiries", params=params)
        items = body.get("data", [])
        for inq in items:
            uid = str(inq.get("id") or inq.get("uuid") or "")
            if uid.startswith(uuid_prefix):
                full_uuid = uid
                break
        if full_uuid:
            break
        meta = body.get("meta", {})
        if page >= meta.get("last_page", 1):
            break
        page += 1

    if not full_uuid:
        print(f"  ERROR: inquiry {uuid_prefix}… not found in inquiry list.")
        print("  (May have converted to a reservation → 410 on thread fetch)")
        # Try fetching directly anyway
        full_uuid = uuid_prefix

    print(f"  Full UUID: {full_uuid}")
    print()

    # Fetch the raw thread — bypass data.get_inquiry_thread() to see the
    # un-normalized shape before msg_sender() is applied
    try:
        raw_body = client.get(
            f"/inquiries/{full_uuid}",
            params={"include": "messages,guest"},
        )
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "?")
        print(f"  FETCH ERROR: HTTP {status} — {exc}")
        return

    detail = raw_body.get("data", {})

    # Guest info (name only, no contact details)
    guest = detail.get("guest") or {}
    print(f"  Guest: first_name={guest.get('first_name')!r}  "
          f"locale={guest.get('locale')!r}")
    print()

    # Raw messages
    raw_msgs = detail.get("messages")
    if raw_msgs is None:
        print("  'messages' key ABSENT from thread detail.")
        print(f"  Detail keys: {list(detail.keys())}")
        return

    if isinstance(raw_msgs, dict):
        print("  WARNING: messages is a single dict (not list) — wrapping.")
        raw_msgs = [raw_msgs]

    if not isinstance(raw_msgs, list):
        print(f"  ERROR: unexpected messages type: {type(raw_msgs)}")
        return

    print(f"  Total items in thread: {len(raw_msgs)}")
    print()

    for i, msg in enumerate(raw_msgs):
        is_last = (i == len(raw_msgs) - 1)
        marker = "  ◀ LAST (what check uses)" if is_last else ""

        print(f"  [{i}]{marker}")
        print(f"    created_at : {msg.get('created_at') or msg.get('sent_at')!r}")

        # Sender fields (the check's decision point)
        for field in _SENDER_FIELDS:
            val = msg.get(field)
            if field == "sender" and isinstance(val, dict):
                print(f"    {field:12}: {_scrub_sender(val)}")
            else:
                print(f"    {field:12}: {val!r}")

        # Event kind fields (distinguish real messages from status events)
        for field in _KIND_FIELDS:
            val = msg.get(field)
            if val is not None:
                print(f"    {field:12}: {val!r}")

        # Resolved sender (mirrors hdata.msg_sender() exactly)
        print(f"    msg_sender()→ {_msg_sender_resolved(msg)}")

        # Body preview
        body_text = msg.get("body") or msg.get("text") or msg.get("content")
        if body_text:
            preview = str(body_text).replace("\n", " ")[:80]
            print(f"    body         : {preview!r}")
        else:
            print(f"    body         : (empty/None)  raw keys: {sorted(msg.keys())}")

        print()

    # Verdict for this thread
    last = raw_msgs[-1]
    last_resolved = hdata.msg_sender(last)   # use the real function
    print(f"  ── Verdict ──")
    print(f"  check sees messages[-1].sender_type = {last.get('sender_type')!r}")
    print(f"  hdata.msg_sender(last)              = {last_resolved!r}")
    print(f"  check condition (sender == 'guest') = {last_resolved == 'guest'}")

    # Find the last message that looks like a real human message
    # (has a non-empty body and a recognisable sender)
    real_msgs = [m for m in raw_msgs if m.get("body") or m.get("text") or m.get("content")]
    if real_msgs:
        last_real = real_msgs[-1]
        last_real_sender = hdata.msg_sender(last_real)
        print(f"  Last item WITH a body: index={raw_msgs.index(last_real)}, "
              f"sender={last_real_sender!r}, "
              f"content_type={last_real.get('content_type')!r}")

    # Flag if the literal last item has no body (system/status event candidate)
    if not (last.get("body") or last.get("text") or last.get("content")):
        print()
        print("  ⚠  Last item has no body — likely a system/status event.")
        print(f"     content_type={last.get('content_type')!r}  "
              f"source={last.get('source')!r}  "
              f"platform={last.get('platform')!r}")


def main() -> None:
    client = HospitableClient()

    verdicts: list[tuple[str, str, bool, bool]] = []
    # (label, last_sender, check_fires, last_is_bodyless)

    for probe in PROBES:
        probe_inquiry(client, probe["label"], probe["uuid"], probe["note"])

    # ── Cross-probe summary ───────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  SUMMARY — run one more time to capture verdicts above, then read:")
    print("=" * 70)
    print("""
  Hypothesis A: a system/status event (e.g. 'Denied', 'Inquiry', 'Withdrawn')
    is the LAST item in the thread and has sender_type='guest' or a non-host
    sender, causing the check to treat it as an unanswered guest message.
    Evidence: look for items with empty body and content_type != 'text/plain'.

  Hypothesis B: host replies have sender_type=None (or an unexpected value)
    so msg_sender() falls through to sender (a dict) → resolves to a truthy
    non-'host' value → host reply not recognized → guest is last real sender.
    Evidence: look for host messages where sender_type is None/absent.

  Hypothesis C: message ordering — the API returns messages newest-first,
    so messages[-1] is the OLDEST item, not the latest.
    Evidence: compare created_at of messages[0] vs messages[-1].

  Hypothesis D: something else — e.g. the thread is split across inquiry +
    reservation records and only the inquiry half is fetched.
""")


if __name__ == "__main__":
    main()
