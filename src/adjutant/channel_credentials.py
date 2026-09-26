"""Shared encrypted provider credentials and serialized token refresh."""

import json
import time
from datetime import UTC, datetime
from uuid import UUID

from psycopg import Connection

from adjutant.adapters.authorization import exchange, provider_for
from adjutant.config import Settings
from adjutant.credentials import CredentialStore
from adjutant.errors import DomainError


def credential_name(channel: str, kind: str) -> str:
    provider_for(channel)
    return f"ad_channel_{channel}_{kind}"


def read_credential(
    conn: Connection, store: CredentialStore, brand_id: UUID, channel: str, kind: str
) -> dict:
    name = credential_name(channel, kind)
    if not conn.execute(
        "SELECT 1 FROM tenant_secret WHERE brand_id=%s AND name=%s", (brand_id, name)
    ).fetchone():
        raise DomainError(
            "ChannelSetupRequired",
            "Save the developer application and authorize account access first.",
            422,
        )
    value = json.loads(store.read(conn, brand_id, name))
    if not isinstance(value, dict):
        raise DomainError(
            "ChannelCredentialInvalid",
            "Reconnect the platform account to replace invalid credentials.",
            422,
        )
    return value


def write_credential(
    conn: Connection,
    store: CredentialStore,
    brand_id: UUID,
    channel: str,
    kind: str,
    value: dict,
) -> None:
    store.write(conn, brand_id, credential_name(channel, kind), json.dumps(value))


def authorization_for(
    conn: Connection,
    config: Settings,
    brand_id: UUID,
    channel: str,
    *,
    refresh_timeout: float = 30,
) -> tuple[dict, dict]:
    """Caller holds the brand lock so refresh and disconnect cannot race."""
    store = CredentialStore(config.credential_master_key_path)
    app = read_credential(conn, store, brand_id, channel, "app")
    token = read_credential(conn, store, brand_id, channel, "token")
    if token.get("expires_at") and token["expires_at"] < time.time() + 120:
        token = exchange(
            provider_for(channel),
            app,
            f"{config.public_origin}/api/brands/{brand_id}/channels/{channel}/callback",
            previous=token,
            timeout=refresh_timeout,
        )
        write_credential(conn, store, brand_id, channel, "token", token)
        expiry = token.get("expires_at")
        conn.execute(
            "UPDATE channel_connection SET token_expires_at=%s WHERE brand_id=%s AND channel=%s",
            (
                datetime.fromtimestamp(expiry, UTC) if expiry else None,
                brand_id,
                channel,
            ),
        )
    return app, token


def invalidate_connection(
    conn: Connection, brand_id: UUID, connection_id: UUID, reason: str
) -> None:
    """A remote authorization failure revokes both displayed access and launch generation."""
    conn.execute(
        "UPDATE channel_connection SET health='revoked',selected=false,verified_at=NULL,"
        "health_detail=%s WHERE id=%s AND brand_id=%s",
        (reason, connection_id, brand_id),
    )
