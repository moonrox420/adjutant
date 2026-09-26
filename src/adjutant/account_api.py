"""Account-type conversion with preserved brands, seats, and agency configuration."""

from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from adjutant.db import Database, Principal, one
from adjutant.errors import DomainError
from adjutant.models import Input


class AccountConversion(Input):
    expected_type: Literal["business", "agency"]
    account_type: Literal["business", "agency"]


def account_router(db: Database, authenticate: Callable[[Request], Principal]) -> APIRouter:
    router = APIRouter(prefix="/api/accounts", tags=["accounts"])

    @router.put("/{account_id}/type")
    def convert(
        account_id: UUID, data: AccountConversion, actor: Principal = Depends(authenticate)
    ) -> dict:
        with db.transaction(actor) as conn:
            one(conn, "SELECT id FROM account WHERE id=%s", (account_id,))
            allowed = conn.execute(
                "SELECT 1 FROM seat WHERE account_id=%s AND user_id=%s AND brand_id IS NULL "
                "AND role IN ('owner','admin') AND revoked_at IS NULL AND accepted_at IS NOT NULL",
                (account_id, actor.user_id),
            ).fetchone()
            if not allowed:
                raise DomainError(
                    "Forbidden",
                    "An account owner or administrator can change its type.",
                    403,
                )
            row = one(
                conn,
                "SELECT id,display_name,account_type FROM convert_account_type(%s,%s,%s)",
                (account_id, data.expected_type, data.account_type),
            )
            row["brand_count"] = one(
                conn,
                "SELECT count(*) AS n FROM brand WHERE account_id=%s",
                (account_id,),
            )["n"]
            return row

    @router.get("/{account_id}/type-history")
    def history(account_id: UUID, actor: Principal = Depends(authenticate)) -> list[dict]:
        with db.transaction(actor) as conn:
            one(conn, "SELECT id FROM account WHERE id=%s", (account_id,))
            return conn.execute(
                "SELECT id,from_type,to_type,changed_by,changed_at FROM account_type_change "
                "WHERE account_id=%s ORDER BY changed_at DESC,id",
                (account_id,),
            ).fetchall()

    return router
