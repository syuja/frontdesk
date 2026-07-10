"""
Check: knowledge_hub_hygiene  [MEDIUM]

Two findings per property:

a) DUPLICATE TOPIC — any topic name appearing more than once on the same
   property, matched case-insensitively and whitespace-trimmed. Confirmed
   live: parent 4d22b785 has two "Parking" topics.

b) DUPLICATE ITEM — any topic containing multiple aggregate_items whose
   content is identical after whitespace/case normalization. Redundant
   entries the AI could arbitrarily choose between.

IMPORTANT SCOPE LIMIT: this check detects only mechanical text duplicates
(exact match after normalization). Semantic contradiction detection —
e.g. two items that say opposite things in different words — is NOT
attempted here. That is a Phase 3 LLM job.

Phase 1 rationale: the KH feeds Hospitable's AI guest-messaging. The real
risk is the AI serving a WRONG or CONFLICTING answer — not an entry merely
being old. Age-based staleness is removed: a 261-day-old address is still
correct. Only duplicate/conflict signals map directly to guest harm and are
unambiguously detectable without LLM assistance.

Pure function: takes pre-fetched kh dict, does no I/O.
"""

from __future__ import annotations

import logging
from collections import Counter

from checks._utils import lookup_prop_name
from checks.finding import AuditData, CheckConfig, Finding, Severity

log = logging.getLogger(__name__)


def _norm(text: str) -> str:
    """Normalize for duplicate detection: lowercase, collapsed whitespace."""
    return " ".join(text.strip().lower().split())


def check_knowledge_hub_hygiene(
    audit: AuditData,
    *,
    now,  # not used — kept for uniform check interface
    config: CheckConfig,
) -> list[Finding]:
    """
    For each property's knowledge hub:
    - Flag any topic name appearing more than once (case-insensitive, trimmed).
    - Flag any topic whose aggregate_items[] contain identical content (exact
      match after whitespace/case normalization).
    """
    prop_index = {p["uuid"]: p for p in audit.props}
    findings: list[Finding] = []

    for prop_uuid, kh_data in audit.kh.items():
        pname = lookup_prop_name(prop_uuid, prop_index)
        topics = kh_data.get("topics") or []

        # ── Duplicate topic names (case-insensitive, trimmed) ──────────────
        norm_to_originals: dict[str, list[str]] = {}
        for t in topics:
            raw = t.get("name") or ""
            if not raw:
                continue
            norm_to_originals.setdefault(_norm(raw), []).append(raw)

        for norm_name, originals in norm_to_originals.items():
            count = len(originals)
            if count <= 1:
                continue
            variants = sorted(set(originals))
            variants_str = " / ".join(f'"{v}"' for v in variants)
            findings.append(Finding(
                check="knowledge_hub_hygiene",
                severity=Severity.LOW,
                property_uuid=prop_uuid,
                property_name=pname,
                title=f'Duplicate KH topic "{norm_name}" ({count}×)',
                detail=(
                    f"topic={variants_str} appears {count} times — "
                    "consolidate to prevent AI serving conflicting answers"
                ),
                entity_id=prop_uuid,
            ))

        # ── Duplicate items within a topic (exact normalized content) ──────
        for topic in topics:
            tname = topic.get("name") or "?"
            items = topic.get("aggregate_items") or []
            if len(items) < 2:
                continue

            norm_to_items: dict[str, list[dict]] = {}
            for item in items:
                key = _norm(str(item.get("content") or ""))
                if not key:
                    continue
                norm_to_items.setdefault(key, []).append(item)

            for norm_content, dupes in norm_to_items.items():
                if len(dupes) < 2:
                    continue
                snippet = norm_content[:60]
                findings.append(Finding(
                    check="knowledge_hub_hygiene",
                    severity=Severity.LOW,
                    property_uuid=prop_uuid,
                    property_name=pname,
                    title=f'Duplicate KH items in "{tname}" ({len(dupes)}× identical content)',
                    detail=(
                        f'topic="{tname}" '
                        f"count={len(dupes)} "
                        f'content="{snippet}{"…" if len(norm_content) > 60 else ""}"'
                    ),
                    entity_id=prop_uuid,
                ))

    return findings
