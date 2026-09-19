import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from adjutant.audit_export import verify_export
from adjutant.errors import DomainError
from adjutant.security import ApprovalSigner


def test_audit_export_is_reproducible_complete_and_signed(client, brand, admin):
    window = {
        "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "end": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    first = client.post(f"/api/brands/{brand}/audit-export", json=window)
    assert first.status_code == 200, first.text
    assert first.headers["Cache-Control"] == "no-store"
    assert first.content == client.post(f"/api/brands/{brand}/audit-export", json=window).content
    bundle = first.json()
    count = admin.execute(
        "SELECT count(*) AS n FROM action WHERE brand_id=%s", (brand,)
    ).fetchone()["n"]
    assert bundle["manifest"]["entry_count"] == count > 0
    assert {entry["brand_id"] for entry in bundle["document"]["entries"]} == {brand}
    signer = ApprovalSigner(Path(".local/approval.key"))
    keys = {signer.key_id: signer.public_bytes}
    verify_export(bundle, keys)
    for key, changed in (("brand_id", str(uuid4())), ("entries", []), ("start", "changed")):
        altered = copy.deepcopy(bundle)
        altered["document"][key] = changed
        with pytest.raises(DomainError, match="verification failed"):
            verify_export(altered, keys)
    with pytest.raises(DomainError):
        verify_export(bundle, {})


def test_audit_export_enforces_brand_isolation_and_window(client, brand):
    window = {"start": "2026-09-01T00:00:00Z", "end": "2026-09-30T00:00:00Z"}
    denied = client.post(f"/api/brands/{uuid4()}/audit-export", json=window)
    assert denied.status_code == 404
    for invalid in (
        {**window, "end": window["start"]},
        {**window, "start": "2020-01-01T00:00:00Z"},
        {**window, "start": "2026-09-01T00:00:00"},
        {**window, "signing_key": "attacker"},
    ):
        assert client.post(f"/api/brands/{brand}/audit-export", json=invalid).status_code == 422


def test_empty_export_uses_exclusive_end(client, brand, admin):
    earliest = admin.execute(
        "SELECT min(executed_at) AS first FROM action WHERE brand_id=%s", (brand,)
    ).fetchone()["first"]
    response = client.post(
        f"/api/brands/{brand}/audit-export",
        json={
            "start": (earliest - timedelta(hours=1)).isoformat(),
            "end": earliest.isoformat(),
        },
    )
    assert response.status_code == 200
    assert response.json()["document"]["entries"] == []
