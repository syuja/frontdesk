"""
Internal helpers shared across check modules.
Not part of the public API — import from checks.finding for the contract types.
"""

from __future__ import annotations

import datetime
from typing import Any


def parse_dt(s: Any) -> datetime.datetime | None:
    """
    Parse an ISO 8601 datetime string to a UTC-aware datetime.
    Falls back to midnight UTC for date-only strings (YYYY-MM-DD).
    Returns None on any parse failure or empty input.
    """
    if not s:
        return None
    s = str(s)
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        d = datetime.date.fromisoformat(s[:10])
        return datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def parse_date(s: Any) -> datetime.date | None:
    """Parse a date or datetime string to a naive date. Returns None on failure."""
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def extract_uuid(obj: dict, *keys: str) -> str:
    """
    Try each key in order to find a UUID string.
    Handles str, list[dict], and dict value shapes (from Hospitable includes).
    Returns "" if nothing resolves.
    """
    for key in keys:
        val = obj.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, dict):
                found = str(first.get("id") or first.get("uuid") or "")
                if found:
                    return found
        if isinstance(val, dict):
            found = str(val.get("id") or val.get("uuid") or "")
            if found:
                return found
    return ""


def lookup_prop_name(prop_uuid: str, prop_index: dict[str, dict]) -> str:
    """Return property name for a UUID, falling back to truncated UUID."""
    p = prop_index.get(prop_uuid, {})
    return p.get("name") or (prop_uuid[:8] if prop_uuid else "unknown")
