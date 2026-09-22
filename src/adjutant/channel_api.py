"""Tenant-scoped OAuth consent, encrypted credentials, and verified account selection."""

import hashlib
import secrets
from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from psycopg.types.json import Jsonb
from pydantic import Field, SecretStr

from adjutant.adapters.authorization import (
    PROVIDERS,
    authorization_url,
    discover,
    exchange,
    provider_for,
    revoke,
)
from adjutant.channel_credentials import (
    authorization_for as channel_authorization,
)
from adjutant.channel_credentials import (
    credential_name,
    read_credential,
    write_credential,
)
from adjutant.config import Settings
from adjutant.credentials import CredentialStore
from adjutant.db import Database, Principal, one, require_role
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.models import Input
from adjutant.service import audit, locked_brand


class ApplicationInput(Input):
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    developer_token: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    region: Literal["NA", "EU", "FE"] = "NA"


class SelectAccount(Input):
    account_id: str = Field(min_length=1, max_length=200)


def channel_router(
    db: Database,
    config: Settings,
    events: EventRegistry,
    authenticate: Callable[[Request], Principal],
) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}/channels", tags=["channels"])
    actor_type = Annotated[Principal, Depends(authenticate)]
    store = CredentialStore(config.credential_master_key_path)

    def redirect_uri(brand_id: UUID, channel: str) -> str:
        return f"{config.public_origin}/api/brands/{brand_id}/channels/{channel}/callback"

    def read(conn, brand_id: UUID, channel: str, kind: str) -> dict:
        return read_credential(conn, store, brand_id, channel, kind)

    def write(conn, brand_id: UUID, channel: str, kind: str, value: dict) -> None:
        write_credential(conn, store, brand_id, channel, kind, value)

    def authorization_for(conn, brand_id: UUID, channel: str) -> tuple[dict, dict]:
        return channel_authorization(conn, config, brand_id, channel)

    def record_error(actor: Principal, brand_id: UUID, channel: str, error: DomainError) -> None:
        with db.transaction(actor) as conn:
            conn.execute(
                "INSERT INTO channel_authorization(brand_id,channel,last_error) VALUES(%s,%s,%s) "
                "ON CONFLICT(brand_id,channel) DO UPDATE SET last_error=excluded.last_error",
                (brand_id, channel, error.message),
            )
            conn.execute(
                "UPDATE channel_connection SET health='error',health_detail=%s WHERE "
                "brand_id=%s AND channel=%s",
                (error.message, brand_id, channel),
            )

    @router.get("")
    def status(brand_id: UUID, actor: actor_type) -> list[dict]:
        with db.transaction(actor) as conn:
            one(conn, "SELECT id FROM brand WHERE id=%s", (brand_id,))
            authorizations = conn.execute(
                "SELECT * FROM channel_authorization WHERE brand_id=%s", (brand_id,)
            ).fetchall()
            connections = conn.execute(
                "SELECT channel,external_ad_account_id,external_account_name,health,health_detail,"
                "verified_at,selected,token_expires_at FROM channel_connection WHERE brand_id=%s",
                (brand_id,),
            ).fetchall()
            names = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM tenant_secret WHERE brand_id=%s", (brand_id,)
                ).fetchall()
            }
            return [
                {
                    "channel": p.channel,
                    "documentation": p.documentation,
                    "required_fields": ["client_id", "client_secret", *p.extra_fields],
                    "application_saved": credential_name(p.channel, "app") in names,
                    "token_saved": credential_name(p.channel, "token") in names,
                    "redirect_uri": redirect_uri(brand_id, p.channel),
                    "authorization": next(
                        (a for a in authorizations if a["channel"] == p.channel), None
                    ),
                    "connections": [c for c in connections if c["channel"] == p.channel],
                }
                for p in PROVIDERS
            ]

    @router.put("/{channel}/application")
    def configure(brand_id: UUID, channel: str, data: ApplicationInput, actor: actor_type) -> dict:
        provider = provider_for(channel)
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, {"owner", "admin"})
            name = credential_name(channel, "app")
            exists = conn.execute(
                "SELECT 1 FROM tenant_secret WHERE brand_id=%s AND name=%s", (brand_id, name)
            ).fetchone()
            prior = read(conn, brand_id, channel, "app") if exists else {}
            app = {"client_id": data.client_id, "region": data.region}
            for key in ("client_secret", "developer_token"):
                value = getattr(data, key)
                app[key] = value.get_secret_value() if value else prior.get(key, "")
            if not app["client_secret"] or (
                "developer_token" in provider.extra_fields and not app["developer_token"]
            ):
                raise DomainError(
                    "ApplicationCredentialsRequired",
                    "Enter the client secret and any required developer token.",
                    422,
                )
            conn.execute(
                "UPDATE channel_oauth_state SET consumed_at=now() "
                "WHERE brand_id=%s AND channel=%s AND consumed_at IS NULL",
                (brand_id, channel),
            )
            if prior and (prior.get("client_id"), prior.get("region")) != (
                app["client_id"],
                app["region"],
            ):
                conn.execute(
                    "DELETE FROM tenant_secret WHERE brand_id=%s AND name=%s",
                    (brand_id, credential_name(channel, "token")),
                )
                conn.execute(
                    "UPDATE channel_connection SET health='revoked',selected=false WHERE "
                    "brand_id=%s AND channel=%s",
                    (brand_id, channel),
                )
            write(conn, brand_id, channel, "app", app)
            audit(
                conn,
                events,
                brand_id,
                "connection_change",
                "channel_application",
                brand_id,
                {"channel": channel},
                "Saved encrypted developer application",
            )
        return {"application_saved": True, "redirect_uri": redirect_uri(brand_id, channel)}

    @router.post("/{channel}/authorize")
    def authorize(
        brand_id: UUID, channel: str, request: Request, response: Response, actor: actor_type
    ) -> dict:
        provider = provider_for(channel)
        state = secrets.token_urlsafe(32)
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, {"owner", "admin"})
            app = read(conn, brand_id, channel, "app")
            conn.execute(
                "INSERT INTO channel_oauth_state(state_hash,brand_id,channel,actor_id) "
                "VALUES(%s,%s,%s,%s)",
                (hashlib.sha256(state.encode()).hexdigest(), brand_id, channel, actor.user_id),
            )
        response.set_cookie(
            "adjutant_session",
            request.cookies["adjutant_session"],
            max_age=43200,
            httponly=True,
            secure=config.secure_cookies,
            samesite="lax",
            path="/",
        )
        return {
            "authorization_url": authorization_url(
                provider, app, redirect_uri(brand_id, channel), state
            )
        }

    @router.get("/{channel}/callback")
    def callback(
        brand_id: UUID,
        channel: str,
        actor: actor_type,
        state: str = "",
        code: str = "",
        auth_code: str = "",
        error: str = "",
    ) -> RedirectResponse:
        provider = provider_for(channel)
        with db.transaction(actor) as conn:
            require_role(conn, brand_id, {"owner", "admin"})
            consumed = conn.execute(
                "UPDATE channel_oauth_state SET consumed_at=now() WHERE state_hash=%s "
                "AND brand_id=%s "
                "AND channel=%s AND actor_id=%s AND consumed_at IS NULL AND "
                "expires_at>now() RETURNING state_hash",
                (hashlib.sha256(state.encode()).hexdigest(), brand_id, channel, actor.user_id),
            ).fetchone()
            if not consumed:
                raise DomainError(
                    "InvalidOAuthState",
                    "Authorization expired or was already used. Start connection again.",
                    400,
                )
        try:
            if error or not (code or auth_code):
                raise DomainError(
                    "AuthorizationDenied",
                    "Platform authorization was not granted. Start connection again when ready.",
                    403,
                )
            with db.transaction(actor) as conn:
                locked_brand(conn, brand_id)
                token = exchange(
                    provider,
                    read(conn, brand_id, channel, "app"),
                    redirect_uri(brand_id, channel),
                    code=code or auth_code,
                )
                write(conn, brand_id, channel, "token", token)
                conn.execute(
                    "INSERT INTO channel_authorization(brand_id,channel,authorized_at) "
                    "VALUES(%s,%s,now()) "
                    "ON CONFLICT(brand_id,channel) DO UPDATE SET "
                    "authorized_at=now(),last_error=NULL,disconnected_at=NULL",
                    (brand_id, channel),
                )
                audit(
                    conn,
                    events,
                    brand_id,
                    "connection_change",
                    "channel_authorization",
                    brand_id,
                    {"channel": channel},
                    "Platform consent received; account discovery required",
                )
        except DomainError as exc:
            record_error(actor, brand_id, channel, exc)
        return RedirectResponse(
            config.public_origin + f"/?channels=1&brand={brand_id}",
            status_code=303,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @router.post("/{channel}/discover")
    def discovery(brand_id: UUID, channel: str, actor: actor_type) -> dict:
        provider_for(channel)
        with db.transaction(actor) as conn:
            one(conn, "SELECT id FROM brand WHERE id=%s", (brand_id,))
            require_role(conn, brand_id, {"owner", "admin"})
        try:
            with db.transaction(actor) as conn:
                locked_brand(conn, brand_id)
                require_role(conn, brand_id, {"owner", "admin"})
                app, token = authorization_for(conn, brand_id, channel)
            accounts = discover(provider_for(channel), app, token)
            with db.transaction(actor) as conn:
                locked_brand(conn, brand_id)
                require_role(conn, brand_id, {"owner", "admin"})
                if read(conn, brand_id, channel, "token") != token:
                    raise DomainError(
                        "AuthorizationChanged",
                        "Account authorization changed during discovery. Retry discovery.",
                        409,
                    )
                conn.execute(
                    "UPDATE channel_authorization SET "
                    "discovered_at=now(),accounts=%s,last_error=NULL WHERE brand_id=%s AND "
                    "channel=%s",
                    (Jsonb(accounts), brand_id, channel),
                )
                conn.execute(
                    "UPDATE channel_connection SET health='degraded',health_detail='Account"
                    " access needs verification' WHERE brand_id=%s AND channel=%s",
                    (brand_id, channel),
                )
                for account in accounts:
                    conn.execute(
                        "INSERT INTO "
                        "channel_connection(brand_id,channel,external_ad_account_id,external_account_name,health,verified_at,token_expires_at,provider_metadata)"
                        " "
                        "VALUES(%s,%s,%s,%s,'healthy',now(),to_timestamp(%s),%s) ON "
                        "CONFLICT(brand_id,channel,external_ad_account_id) "
                        "DO UPDATE SET "
                        "external_account_name=excluded.external_account_name,health='healthy',health_detail=NULL,verified_at=now(),"
                        "token_expires_at=excluded.token_expires_at,provider_metadata=excluded.provider_metadata",
                        (
                            brand_id,
                            channel,
                            account["id"],
                            account["name"],
                            token.get("expires_at"),
                            Jsonb(account),
                        ),
                    )
                audit(
                    conn,
                    events,
                    brand_id,
                    "connection_change",
                    "channel_discovery",
                    brand_id,
                    {"channel": channel, "accounts": len(accounts)},
                    "Verified available advertising accounts",
                )
                return {"accounts": accounts}
        except DomainError as exc:
            if exc.code != "Forbidden":
                record_error(actor, brand_id, channel, exc)
            raise

    @router.post("/{channel}/select")
    def select(brand_id: UUID, channel: str, data: SelectAccount, actor: actor_type) -> dict:
        result = discovery(brand_id, channel, actor)
        if data.account_id not in {item["id"] for item in result["accounts"]}:
            raise DomainError(
                "AccountAccessDenied", "The authorized platform did not return this account.", 403
            )
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            conn.execute(
                "UPDATE channel_connection SET selected=false WHERE brand_id=%s AND channel=%s",
                (brand_id, channel),
            )
            account = one(
                conn,
                "UPDATE channel_connection SET selected=true WHERE brand_id=%s AND "
                "channel=%s AND external_ad_account_id=%s "
                "RETURNING external_ad_account_id,external_account_name,verified_at",
                (brand_id, channel, data.account_id),
            )
            audit(
                conn,
                events,
                brand_id,
                "connection_change",
                "channel_selection",
                brand_id,
                {"channel": channel, "account_id": data.account_id},
                "Selected verified advertising account",
            )
        return account

    @router.delete("/{channel}/authorization")
    def disconnect(brand_id: UUID, channel: str, actor: actor_type) -> dict:
        provider = provider_for(channel)
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, {"owner", "admin"})
            exists = conn.execute(
                "SELECT 1 FROM tenant_secret WHERE brand_id=%s AND name=%s",
                (brand_id, credential_name(channel, "token")),
            ).fetchone()
            result = {
                "remote_revoked": False,
                "revocation_url": provider.documentation,
                "message": "No token is stored; remote consent status is unknown.",
            }
            if exists:
                try:
                    result = revoke(
                        provider,
                        read(conn, brand_id, channel, "app"),
                        read(conn, brand_id, channel, "token"),
                    )
                except DomainError as exc:
                    result = {
                        "remote_revoked": False,
                        "revocation_url": provider.documentation,
                        "message": f"Remote revocation was not confirmed: {exc.message}",
                    }
            conn.execute(
                "DELETE FROM tenant_secret WHERE brand_id=%s AND name=%s",
                (brand_id, credential_name(channel, "token")),
            )
            conn.execute(
                "UPDATE channel_oauth_state SET consumed_at=now() WHERE brand_id=%s AND"
                " channel=%s AND consumed_at IS NULL",
                (brand_id, channel),
            )
            conn.execute(
                "UPDATE channel_connection SET "
                "selected=false,health='revoked',health_detail='Authorization removed "
                "from Adjutant' WHERE brand_id=%s AND channel=%s",
                (brand_id, channel),
            )
            conn.execute(
                "UPDATE channel_authorization SET "
                "disconnected_at=now(),accounts='[]',last_error=NULL WHERE brand_id=%s "
                "AND channel=%s",
                (brand_id, channel),
            )
            audit(
                conn,
                events,
                brand_id,
                "connection_change",
                "channel_authorization",
                brand_id,
                {"channel": channel, "remote_revoked": result["remote_revoked"]},
                "Removed stored platform authorization",
            )
        return {
            "disconnected": True,
            **result,
            "message": "Stored tokens were deleted. " + result["message"],
            "documentation": provider.documentation,
        }

    return router
