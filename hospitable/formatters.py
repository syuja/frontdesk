"""
Digest formatters for the Hospitable auditor.

format_digest()   → human-readable output for Telegram and default console view
format_verbose()  → debug output with entity= UUIDs and raw timestamps (--verbose)
truncate_digest() → fit within Telegram's 4096 UTF-16 unit limit at a finding boundary
"""
from __future__ import annotations

import datetime
import re

from checks.finding import Finding, Severity

# ── Constants ─────────────────────────────────────────────────────────────────

# Severity display order: CRITICAL first, LOW last
_SEV_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]

# Emoji prefix per severity — keeps visual hierarchy in plain-text Telegram messages
_SEV_EMOJI: dict[Severity, str] = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH:     "🟠",
    Severity.MEDIUM:   "🟡",
    Severity.LOW:      "⚪",
}

# Short room labels for the human digest — every finding is this property, so the
# full "Villa del Encanto |" prefix is redundant. Unknown names fall back to raw.
_PROP_SHORT: dict[str, str] = {
    "Villa del Encanto | Ensuite Room":        "Ensuite",
    "Villa del Encanto | Queen Room":          "Queen",
    "Villa del Encanto - Ensuite and Queen":   "Whole unit",
}

# Within a severity tier, lower rank floats to the top.
# Guest-affecting checks (turnover, inquiry, review) outrank hygiene checks.
_CHECK_RANK: dict[str, int] = {
    "turnover_gap":         1,
    "unanswered_inquiry":   2,
    "actionable_review":    3,
    "smartlock_battery":    4,
    "knowledge_hub_hygiene": 5,
}

# Wording for the "inquiry will age out" tail — single source so it's easy to tweak.
_DROPS_OFF_TMPL = "drops off in {t} if unanswered"

# Telegram per-message hard limit in UTF-16 code units.
# Python str length undercounts emoji / em-dashes (each counts as 2 UTF-16 units).
TELEGRAM_MAX_UTF16 = 4096


# ── UTF-16 helpers ─────────────────────────────────────────────────────────────

def _utf16_len(s: str) -> int:
    """Length in UTF-16 code units — what Telegram counts against its 4096 limit."""
    return len(s.encode("utf-16-le")) // 2


# ── Human-time helpers ────────────────────────────────────────────────────────

def _short_prop(name: str) -> str:
    """Map a full property name to its short digest label; fall back to raw name."""
    return _PROP_SHORT.get(name, name)


def _h_to_human(hours: float) -> str:
    """Convert a float hours value to a short human-readable duration string.

    < 1h → minutes (floor); < 48h → hours (floor); >= 48h → approximate days.
    """
    if hours < 1:
        return f"{max(1, int(hours * 60))}m"
    if hours < 48:
        return f"{int(hours)}h"
    days = round(hours / 24)
    return f"~{days} day{'s' if days != 1 else ''}"


def _h_to_human_precise(hours: float) -> str:
    """Like _h_to_human but uses one decimal near day boundaries to avoid collisions.

    Used when two durations from the same finding might otherwise round to the same
    string (e.g. 178h elapsed vs 158h remaining both showing "~7 days").
    """
    if hours < 1:
        return f"{max(1, int(hours * 60))}m"
    if hours < 48:
        return f"{int(hours)}h"
    days_exact = hours / 24
    days_rounded = round(days_exact)
    # If the rounded integer would collide with an adjacent value, show one decimal
    if abs(days_exact - days_rounded) >= 0.15:
        return f"{days_exact:.1f} days"
    return f"~{days_rounded} day{'s' if days_rounded != 1 else ''}"


def _extract_float(pattern: str, text: str) -> float | None:
    """Return the first captured float from text, or None if the pattern doesn't match."""
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def _fmt_date(iso_date: str) -> str:
    """Format a YYYY-MM-DD date string as M/D (e.g. '2026-07-14' → '7/14')."""
    try:
        d = datetime.date.fromisoformat(iso_date[:10])
        return f"{d.month}/{d.day}"
    except ValueError:
        return iso_date


# ── Per-check human line renderer ─────────────────────────────────────────────

def _render_human(f: Finding) -> str:
    """One bullet line for a single Finding in the human digest.

    Builds a 'who / what / where' line from structured Finding fields.
    Targeted float extraction from title/detail is used only for durations
    (numbers are structured data, not free prose).
    """
    guest = f.guest_name or "unknown guest"
    prop = _short_prop(f.property_name)

    if f.check == "unanswered_inquiry":
        gap_h = _extract_float(r"last replied ([\d.]+)h ago", f.title) or 0.0
        ages_h = _extract_float(r"ages out in (\d+)h", f.detail)
        elapsed_str = _h_to_human_precise(gap_h)
        if ages_h is not None:
            remaining_str = _h_to_human_precise(ages_h)
            # If both sides would render identically, force one-decimal on remaining
            if remaining_str == elapsed_str:
                remaining_str = f"{ages_h / 24:.1f} days"
            tail = f", {_DROPS_OFF_TMPL.format(t=remaining_str)}"
        else:
            tail = ""
        return f"• {guest} — no reply in {elapsed_str} ({prop}){tail}"

    if f.check == "actionable_review":
        days_left = _extract_float(r"(\d+)d remaining", f.detail)
        days_str = f"{int(days_left)} days left" if days_left is not None else "window open"
        return f"• {guest}'s review — {days_str} to submit ({prop})"

    if f.check == "knowledge_hub_hygiene":
        topic_m = re.search(r'"([^"]+)"', f.title)
        topic = topic_m.group(1) if topic_m else "topic"
        count_m = re.search(r"\((\d+)×", f.title)
        count = count_m.group(1) if count_m else "?"
        kind = "item duplicates in" if "items" in f.title else "topic"
        return f'• KH duplicate {kind} "{topic}" \xd7{count} ({prop})'

    if f.check == "turnover_gap":
        # detail format: "OUT: <pname> <YYYY-MM-DD> (<guest>) → IN: <pname> <YYYY-MM-DD> (<guest>) | gap=Nd"
        # Property names contain spaces so we anchor on the ISO date pattern instead of \S+
        dep_guest_m = re.search(r"OUT: .+? \(([^)]+)\)", f.detail)
        dep_date_m  = re.search(r"OUT: .+?(\d{4}-\d{2}-\d{2})", f.detail)
        arr_guest_m = re.search(r"→ IN: .+? \(([^)]+)\)", f.detail)
        arr_date_m  = re.search(r"→ IN: .+?(\d{4}-\d{2}-\d{2})", f.detail)
        gap_m       = re.search(r"gap=(\d+)d", f.detail)
        departing   = dep_guest_m.group(1) if dep_guest_m else "unknown guest"
        dep_date    = _fmt_date(dep_date_m.group(1)) if dep_date_m else "?"
        arriving    = arr_guest_m.group(1) if arr_guest_m else guest
        arr_date    = _fmt_date(arr_date_m.group(1)) if arr_date_m else "?"
        gap_d       = gap_m.group(1) if gap_m else "?"
        return f"• {arriving} arriving {arr_date} — {departing} left {dep_date} ({prop}), {gap_d}-day gap"

    if f.check == "smartlock_battery":
        lock_m = re.search(r"— (.+)$", f.title)
        lock = lock_m.group(1) if lock_m else "lock"
        tw_m = re.search(r"tripwire: (.+)$", f.detail)
        if tw_m:
            tw_raw = tw_m.group(1)
            tw = tw_raw.split(";")[0].strip()
        else:
            tw = "check detail"
        return f'• Smartlock "{lock}" — {tw} ({prop})'

    # Fallback: use the title as-is
    return f"• {f.title} ({prop})"


# ── Formatters ────────────────────────────────────────────────────────────────

def format_digest(
    findings: list[Finding],
    now: datetime.datetime,
    account_name: str = "Villa del Encanto",
) -> str:
    """Human-readable digest: compact header + emoji-grouped finding bullets.

    Zero findings → all-clear message so the reader knows the run completed.
    Suitable for Telegram and default console output.
    """
    date_str = now.strftime("%Y-%m-%d")

    if not findings:
        return f"\U0001f3e0 {account_name} — {date_str}\n\n✅ All clear — no issues found"

    counts: dict[Severity, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    total = len(findings)
    count_parts = [
        f"{counts[sev]} {str(sev).lower()}"
        for sev in _SEV_ORDER
        if counts.get(sev, 0) > 0
    ]
    header = (
        f"\U0001f3e0 {account_name} — {date_str} — "
        f"{total} finding{'s' if total != 1 else ''} ({', '.join(count_parts)})"
    )

    by_sev: dict[Severity, list[Finding]] = {}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)

    lines: list[str] = [header]
    for sev in _SEV_ORDER:
        group = by_sev.get(sev)
        if not group:
            continue
        # Secondary sort: guest-affecting checks before hygiene (lower rank = higher)
        group = sorted(group, key=lambda x: _CHECK_RANK.get(x.check, 99))
        lines.append(f"\n{_SEV_EMOJI[sev]} {sev}")
        for f in group:
            lines.append(_render_human(f))

    return "\n".join(lines)


def format_verbose(findings: list[Finding]) -> str:
    """Debug/verbose format — preserves entity= UUIDs, raw detail, full timestamps.

    Matches the previous default console output; activated via --verbose.
    """
    if not findings:
        return "No findings — account looks clean."

    by_sev: dict[Severity, list[Finding]] = {}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)

    lines: list[str] = []
    for sev in _SEV_ORDER:
        group = by_sev.get(sev)
        if not group:
            continue
        bar = "═" * 60
        lines.append(bar)
        lines.append(f"  {sev}  ({len(group)} finding{'s' if len(group) != 1 else ''})")
        lines.append(bar)
        for f in group:
            lines.append(f"[{sev}] {f.check}  —  {f.property_name}")
            lines.append(f"  {f.title}")
            lines.append(f"  {f.detail}")
            if f.entity_id:
                lines.append(f"  entity={f.entity_id[:8]}")
            lines.append("")

    return "\n".join(lines)


def truncate_digest(digest: str, max_utf16: int = TELEGRAM_MAX_UTF16) -> str:
    """Truncate a formatted digest at a finding bullet boundary.

    Appends '…(N more)' where N is the count of dropped finding bullet lines.
    Non-bullet lines (account header, severity group labels) are always preserved.
    Returns the original string unchanged if it fits within max_utf16.
    """
    if _utf16_len(digest) <= max_utf16:
        return digest

    lines = digest.split("\n")
    total_bullets = sum(1 for ln in lines if ln.startswith("•"))

    kept: list[str] = []
    kept_bullets = 0

    for ln in lines:
        is_bullet = ln.startswith("•")

        if is_bullet:
            omitted_after = total_bullets - kept_bullets - 1
            # Draft: include this bullet + a suffix for still-remaining ones
            draft_suffix = f"\n…({omitted_after} more)" if omitted_after > 0 else ""
            draft = "\n".join(kept + [ln]) + draft_suffix
            if _utf16_len(draft) > max_utf16:
                still_omitted = total_bullets - kept_bullets
                kept.append(f"…({still_omitted} more)")
                break
            kept.append(ln)
            kept_bullets += 1
        else:
            kept.append(ln)

    return "\n".join(kept)
