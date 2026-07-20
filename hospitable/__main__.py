"""
Entry point for the Hospitable auditor.

Default: pull all data, run all checks, print human digest to console.
--verbose          : debug format with entity= UUIDs and raw timestamps.
--notify           : also deliver the human digest to Telegram.
--smoke            : data-pull smoke test only (no check logic).
--fake-low-battery : DEV ONLY — replace real lock reading with percentage=15 to
                     exercise the CRITICAL smartlock path end-to-end. Never use in
                     production; the Lambda path has no such flag.

Run:
    uv run -m hospitable             # human digest to console
    uv run -m hospitable --verbose   # debug format
    uv run -m hospitable --notify    # human digest + Telegram delivery
    uv run -m hospitable --smoke     # data layer smoke test
"""

import datetime
import logging
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("hospitable")


# ── Audit mode (default) ──────────────────────────────────────────────────────

def _run_audit() -> None:
    from hospitable.client import HospitableClient
    from checks.runner import build_audit_data, run_all
    from checks.finding import CheckConfig, Severity
    from hospitable.formatters import format_digest, format_verbose

    client = HospitableClient()
    config = CheckConfig()
    now = datetime.datetime.now(datetime.timezone.utc)
    verbose = "--verbose" in sys.argv
    notify = "--notify" in sys.argv

    log.info("Starting audit run  now=%s", now.isoformat())
    audit = build_audit_data(client)
    log.info(
        "Data fetched — props=%d  inquiries=%d  reviews=%d  reservations=%d",
        len(audit.props), len(audit.inquiries), len(audit.reviews),
        len(audit.reservations),
    )

    if "--fake-low-battery" in sys.argv:
        if not audit.smartlocks:
            log.error("--fake-low-battery: no smartlocks found to substitute — aborting")
            sys.exit(1)
        _real = audit.smartlocks[0]
        audit.smartlocks = [{
            **_real,
            "online": True,
            "issues": [],
            "state": {
                **(_real.get("state") or {}),
                "online": True,
                "battery": {"percentage": 15, "threshold": 30, "status": "low"},
            },
        }]
        log.warning("⚠️  FAKE LOW BATTERY INJECTED — TEST MODE, not a real reading")

    findings = run_all(audit, now=now, config=config)

    print()
    if verbose:
        print(format_verbose(findings))
    else:
        print(format_digest(findings, now))

    total = len(findings)
    high = sum(1 for f in findings if f.severity >= Severity.HIGH)
    log.info("Audit complete — %d finding(s) total, %d HIGH or above", total, high)

    if notify:
        from hospitable.telegram import send_digest
        try:
            send_digest(format_digest(findings, now))
        except Exception as exc:
            log.error(
                "Telegram delivery failed (%s) — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
                type(exc).__name__,
            )
            sys.exit(1)


# ── Smoke test mode (--smoke) ─────────────────────────────────────────────────

def _run_smoke() -> None:
    from hospitable.client import HospitableClient
    import hospitable.data as data

    client = HospitableClient()

    log.info("── Properties ──────────────────────────────────────────────────")
    props = data.get_properties(client)
    log.info("  total: %d", len(props))
    for p in props:
        log.info(
            "  %s  %r  parent_child=%s",
            p["uuid"][:8], p.get("name"), p.get("parent_child"),
        )

    if not props:
        log.error("No properties returned — verify PAT and account")
        sys.exit(1)

    prop_uuids = [p["uuid"] for p in props]

    log.info("── Reservations ─────────────────────────────────────────────────")
    today = datetime.date.today()
    start = today - datetime.timedelta(days=90)
    end = today + datetime.timedelta(days=60)
    reservations = data.get_reservations(client, prop_uuids, start, end)
    log.info("  %s → %s: %d reservations", start, end, len(reservations))
    if reservations:
        r0 = reservations[0]
        log.info("  sample  uuid=%s  status=%s", r0["uuid"][:8], r0.get("status"))

    log.info("── Inquiries ────────────────────────────────────────────────────")
    inquiries = data.get_inquiries(client, prop_uuids)
    log.info("  total: %d", len(inquiries))
    if inquiries:
        i0 = inquiries[0]
        log.info("  sample  uuid=%s  status=%s", i0["uuid"][:8], i0.get("status"))

    log.info("── Guest reviews ────────────────────────────────────────────────")
    reviews = data.get_guest_reviews(client)
    log.info("  total: %d", len(reviews))
    pending = [r for r in reviews if r.get("status") == "pending"]
    actionable = [r for r in reviews if r.get("can_be_sent_now")]
    log.info("  pending: %d  can_be_sent_now: %d", len(pending), len(actionable))
    if pending:
        p0 = pending[0]
        log.info(
            "  sample pending  uuid=%s  expires_at=%s  can_send=%s",
            p0["uuid"][:8], p0.get("expires_at"), p0.get("can_be_sent_now"),
        )

    log.info("── Knowledge hub ────────────────────────────────────────────────")
    for puuid in prop_uuids:
        kh = data.get_knowledge_hub(client, puuid)
        topics = kh.get("topics") or []
        total_items = sum(len(t.get("aggregate_items") or []) for t in topics)
        log.info(
            "  prop %s  topics: %d  items: %d",
            puuid[:8], len(topics), total_items,
        )
        for t in topics:
            items = t.get("aggregate_items") or []
            log.info("    [%s]  %d items  updated=%s", t.get("name"), len(items), t.get("updated_at"))

    log.info("── Tasks ────────────────────────────────────────────────────────")
    tasks = data.get_tasks(client, prop_uuids, today, today + datetime.timedelta(days=30))
    log.info("  next 30d: %d tasks", len(tasks))
    if tasks:
        t0 = tasks[0]
        log.info("  sample  uuid=%s  type=%s", t0["uuid"][:8], t0.get("type") or t0.get("task_type"))

    if reservations:
        log.info("── Messages (sample, throttled) ─────────────────────────────────")
        res_uuid = reservations[0]["uuid"]
        log.info("  fetching messages for reservation %s…", res_uuid[:8])
        msgs = data.get_reservation_messages(client, res_uuid)
        log.info("  message count: %d", len(msgs))
        if msgs:
            last = msgs[-1]
            log.info("  last message  sender=%s", last.get("sender"))

    log.info("── Smoke test complete ───────────────────────────────────────────")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if "--smoke" in sys.argv:
        _run_smoke()
    else:
        _run_audit()


if __name__ == "__main__":
    main()
