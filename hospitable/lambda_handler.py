"""
AWS Lambda handler for the nightly Hospitable audit digest.

Invoked on a CloudWatch Events schedule. Composes the audit primitives and
delivers the result to Telegram unconditionally — no CLI flags, no --notify gate.
"""
from __future__ import annotations

import datetime
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from hospitable.client import HospitableClient
from checks.runner import build_audit_data, run_all
from checks.finding import CheckConfig, Severity
from hospitable.formatters import format_digest
from hospitable.telegram import send_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("hospitable.lambda")


def handler(event: object, context: object) -> dict:
    """Lambda entry point.

    Pulls all data, runs all checks, formats the digest, and sends it to Telegram.
    Returns a minimal response dict (Lambda ignores it for scheduled invocations).
    """
    client = HospitableClient()
    config = CheckConfig()
    now = datetime.datetime.now(datetime.timezone.utc)

    log.info("Lambda audit run  now=%s", now.isoformat())
    audit = build_audit_data(client)
    log.info(
        "Data fetched — props=%d  inquiries=%d  reviews=%d  reservations=%d",
        len(audit.props), len(audit.inquiries), len(audit.reviews),
        len(audit.reservations),
    )

    findings = run_all(audit, now=now, config=config)

    total = len(findings)
    high = sum(1 for f in findings if f.severity >= Severity.HIGH)
    log.info("Audit complete — %d finding(s) total, %d HIGH or above", total, high)

    digest = format_digest(findings, now, lock_statuses=audit.lock_statuses)
    send_digest(digest)

    return {"statusCode": 200, "findings": total}
