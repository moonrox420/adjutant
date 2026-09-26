"""Deterministic, signed audit records with independently verifiable content."""

import base64
from datetime import UTC, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.encoders import jsonable_encoder
from psycopg import Connection
from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from adjutant.db import one
from adjutant.errors import DomainError
from adjutant.security import ApprovalSigner, canonical_bytes, digest


class AuditWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> "AuditWindow":
        self.start = self.start.astimezone(UTC)
        self.end = self.end.astimezone(UTC)
        if not timedelta(0) < self.end - self.start <= timedelta(days=366):
            raise ValueError("Choose an increasing audit window of at most 366 days")
        return self


def signed_export(
    conn: Connection, signer: ApprovalSigner, brand_id: UUID, window: AuditWindow
) -> dict[str, Any]:
    """Sign the exact ledger snapshot; fixed windows and unchanged rows produce identical bytes."""
    one(conn, "SELECT id FROM brand WHERE id=%s", (brand_id,))
    rows = conn.execute(
        """SELECT * FROM action WHERE brand_id=%s AND executed_at>=%s AND executed_at<%s
        ORDER BY executed_at,id LIMIT 10001""",
        (brand_id, window.start, window.end),
    ).fetchall()
    if len(rows) > 10000:
        raise DomainError(
            "ExportTooLarge", "Choose a smaller window of at most 10,000 actions.", 413
        )
    document = {
        "format": "adjutant.audit.v1",
        "brand_id": str(brand_id),
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "entries": jsonable_encoder(rows, custom_encoder={Decimal: str}),
    }
    manifest = {
        "purpose": "adjutant.audit-export.v1",
        "sha256": digest(document),
        "entry_count": len(rows),
        "signing_key_id": signer.key_id,
    }
    return {
        "document": document,
        "manifest": manifest,
        "signature": base64.b64encode(signer.sign(manifest)).decode("ascii"),
    }


def verify_export(bundle: dict[str, Any], trusted_keys: dict[str, bytes]) -> None:
    """Verify against an externally trusted key ring, never a key supplied by the export itself."""
    try:
        if set(bundle) != {"document", "manifest", "signature"}:
            raise ValueError("Unexpected export fields")
        manifest, document = bundle["manifest"], bundle["document"]
        if (
            manifest["purpose"] != "adjutant.audit-export.v1"
            or document["format"] != "adjutant.audit.v1"
            or manifest["entry_count"] != len(document["entries"])
            or manifest["sha256"] != digest(document)
        ):
            raise ValueError("Export digest or format mismatch")
        key = trusted_keys[manifest["signing_key_id"]]
        Ed25519PublicKey.from_public_bytes(key).verify(
            base64.b64decode(bundle["signature"], validate=True),
            canonical_bytes(manifest),
        )
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise DomainError(
            "InvalidAuditExport", "Audit export verification failed.", 422
        ) from exc
