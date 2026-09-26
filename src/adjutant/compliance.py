"""Compliance surface: Jurisdictional AI disclosures, claim substantiation, policy rejection routing, and signed compliance exports."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
import re
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.errors import DomainError
from adjutant.security import ApprovalSigner


# Jurisdiction and platform AI transparency disclosure requirements (EU AI Act Art. 50, FTC 16 CFR 255, Google/Meta policy)
DISCLOSURE_RULES = {
    "EU": {
        "text": "AI-generated synthetic media (EU AI Act Art. 50 Compliant)",
        "position": "bottom_right",
        "applies_globally": True,
    },
    "US": {
        "text": "Created with AI assistance (FTC 16 CFR 255 Disclosure)",
        "position": "bottom_left",
        "applies_globally": True,
    },
    "GLOBAL": {
        "text": "AI Generated Creative",
        "position": "bottom_right",
        "applies_globally": True,
    },
}

# Unsubstantiated superlative patterns and health claim regexes
UNSUBSTANTIATED_SUPERLATIVE_PATTERNS = [
    r"\b#1\b",
    r"\bnumber one\b",
    r"\bworld'?s best\b",
    r"\bguaranteed cure\b",
    r"\b100% cure\b",
    r"\bmiracle weight loss\b",
    r"\brisk-free profit\b",
    r"\bunlimited returns\b",
    r"\bguaranteed income\b",
    r"\bclinically proven to prevent disease\b",
]


def apply_ai_disclosure(
    scene_graph: dict[str, Any],
    jurisdiction: str,
    channel: str | None = None,
) -> dict[str, Any]:
    """S13.1: Apply jurisdictional AI transparency disclosure at render time."""
    rule = DISCLOSURE_RULES.get(jurisdiction.upper()) or DISCLOSURE_RULES["GLOBAL"]

    updated = dict(scene_graph)
    layers = list(updated.get("layers", []))

    # Check if disclosure layer already exists
    if any(l.get("id") == "compliance_ai_disclosure" for l in layers):
        return updated

    pos = (
        {"bottom_pct": 0.03, "right_pct": 0.04}
        if rule["position"] == "bottom_right"
        else {"bottom_pct": 0.03, "left_pct": 0.04}
    )

    disclosure_layer = {
        "id": "compliance_ai_disclosure",
        "type": "compliance_badge",
        "text": rule["text"],
        "position": pos,
        "font_size_pt": 9,
        "opacity": 0.85,
        "background_pill": True,
    }
    layers.append(disclosure_layer)
    updated["layers"] = layers
    return updated


def verify_claim_substantiation(
    copy_text: str,
    brand_proof_points: list[str] | None = None,
) -> tuple[bool, str | None]:
    """S13.2: Verify claims.

    An unsubstantiated superlative or health claim blocks the render with a specific reason.
    """
    proofs = [p.lower() for p in (brand_proof_points or [])]
    text_lower = copy_text.lower()

    for pattern in UNSUBSTANTIATED_SUPERLATIVE_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            matched_phrase = match.group(0)
            # If substantiated by an explicit proof point matching the claim, allow
            if any(matched_phrase in p for p in proofs):
                continue
            return (
                False,
                f"UnsubstantiatedClaimError: Phrase '{matched_phrase}' is an unsubstantiated superlative or health claim prohibited without verified proof documentation.",
            )

    return True, None


def route_policy_rejection(
    conn: Connection,
    brand_id: UUID,
    channel: str,
    campaign_object_id: UUID,
    platform_rejection_payload: dict[str, Any],
) -> UUID:
    """S13.3: Route verbatim platform rejection text to a human escalation and never auto-retry."""
    verbatim_text = platform_rejection_payload.get("rejection_reason") or platform_rejection_payload.get("error_message") or "Ad disapproved by platform policy review."
    policy_code = platform_rejection_payload.get("policy_code", "GENERIC_POLICY_VIOLATION")

    escalation_id = conn.execute(
        """INSERT INTO escalation(
            brand_id, trigger_type, scope_kind, scope_id, context, state
        ) VALUES (
            %s, 'platform_policy_rejection', 'campaign_object', %s, %s, 'open'
        ) RETURNING id""",
        (
            brand_id,
            campaign_object_id,
            Jsonb({
                "channel": channel,
                "policy_code": policy_code,
                "verbatim_rejection_text": verbatim_text,
                "raw_response": platform_rejection_payload,
                "auto_retry_blocked": True,
            }),
        ),
    ).fetchone()["id"]

    # Mark object as policy_rejected in DB to prevent runner from cycling
    conn.execute(
        "UPDATE campaign_object SET state='policy_rejected' WHERE id=%s",
        (campaign_object_id,),
    )

    return escalation_id


def generate_signed_compliance_export(
    conn: Connection,
    brand_id: UUID,
    start_time: datetime,
    end_time: datetime,
    signer: ApprovalSigner | None = None,
) -> dict[str, Any]:
    """S13.4: Generate byte-identical, deterministic signed compliance audit export for a fixed time window."""
    actions = conn.execute(
        """SELECT id, executed_at, actor_kind, action_type, target_kind, target_id,
                  channel, diff, rationale, revert_path
        FROM action
        WHERE brand_id=%s
          AND executed_at >= %s
          AND executed_at <= %s
        ORDER BY executed_at ASC, id ASC""",
        (brand_id, start_time, end_time),
    ).fetchall()

    decisions = conn.execute(
        """SELECT id, finding_id, kind, target_id, channel, params, state, rejection_reason, executed_at
        FROM autonomous_decision
        WHERE brand_id=%s
          AND created_at >= %s
          AND created_at <= %s
        ORDER BY created_at ASC, id ASC""",
        (brand_id, start_time, end_time),
    ).fetchall()

    export_payload = {
        "export_version": "1.0",
        "brand_id": str(brand_id),
        "window_start": start_time.astimezone(UTC).isoformat(),
        "window_end": end_time.astimezone(UTC).isoformat(),
        "actions_count": len(actions),
        "actions": [
            {
                "id": str(a["id"]),
                "executed_at": a["executed_at"].astimezone(UTC).isoformat(),
                "actor_kind": a["actor_kind"],
                "action_type": a["action_type"],
                "target_kind": a["target_kind"],
                "target_id": str(a["target_id"]) if a.get("target_id") else None,
                "channel": a.get("channel"),
                "diff": a["diff"],
                "rationale": a["rationale"],
                "revert_path": a.get("revert_path"),
            }
            for a in actions
        ],
        "decisions": [
            {
                "id": str(d["id"]),
                "kind": d["kind"],
                "target_id": str(d["target_id"]) if d.get("target_id") else None,
                "channel": d.get("channel"),
                "state": d["state"],
                "rejection_reason": d.get("rejection_reason"),
            }
            for d in decisions
        ],
    }

    # Deterministic canonical serialization for byte-identical reproducibility
    canonical_json = json.dumps(export_payload, sort_keys=True, separators=(",", ":"))
    payload_sha256 = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    signature = None
    if signer:
        signature = signer.sign({
            "brand_id": str(brand_id),
            "payload_sha256": payload_sha256,
            "window_start": export_payload["window_start"],
            "window_end": export_payload["window_end"],
        })

    return {
        "manifest": export_payload,
        "canonical_sha256": payload_sha256,
        "signature": signature,
    }
