from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from adjutant.errors import DomainError


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    full_name: str
    brand_ids: tuple[UUID, ...]


class Database:
    """Transactions use SET LOCAL so tenant state cannot survive pool check-in."""

    def __init__(self, url: str) -> None:
        self.pool = ConnectionPool(
            url,
            min_size=1,
            max_size=8,
            open=False,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )

    def open(self) -> None:
        self.pool.open(wait=True, timeout=15)
        with self.pool.connection() as conn:
            role = conn.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if role is None or role["rolsuper"] or role["rolbypassrls"]:
                self.pool.close()
                raise RuntimeError(
                    "Runtime database role must not bypass row-level security"
                )

    @contextmanager
    def transaction(
        self, principal: Principal | None = None, extra_brand: UUID | None = None
    ) -> Iterator[Connection[dict[str, Any]]]:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SET LOCAL search_path = adjutant, public")
            brands = list(principal.brand_ids) if principal else []
            if extra_brand:
                brands.append(extra_brand)
            conn.execute(
                "SELECT set_config('app.current_brand_ids', %s, true)",
                (",".join(map(str, brands)),),
            )
            conn.execute(
                "SELECT set_config('app.current_actor_id', %s, true)",
                (str(principal.user_id) if principal else "",),
            )
            conn.execute("SET LOCAL statement_timeout = '10s'")
            yield conn

    def authenticate(self, token_hash: str) -> Principal:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM authenticate_session(%s)", (token_hash,)
            ).fetchone()
        if not row:
            raise DomainError("Unauthorized", "Sign in to continue.", 401)
        return Principal(
            row["user_id"], row["email"], row["full_name"], tuple(row["brand_ids"])
        )


def one(conn: Connection, sql: str, params: tuple = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise DomainError("NotFound", "The requested record is not available.", 404)
    return row


def require_role(conn: Connection, brand_id: UUID, roles: set[str]) -> dict[str, Any]:
    seats = conn.execute(
        """SELECT s.* FROM seat s JOIN brand b ON b.account_id=s.account_id
           WHERE b.id=%s AND s.user_id=current_actor_id() AND s.revoked_at IS NULL
             AND s.accepted_at IS NOT NULL AND (s.brand_id IS NULL OR s.brand_id=b.id)""",
        (brand_id,),
    ).fetchall()
    eligible = [seat for seat in seats if seat["role"] in roles]
    if not eligible:
        raise DomainError("Forbidden", "Your role does not permit this action.", 403)
    return max(eligible, key=lambda seat: seat["approval_daily_usd_cap"] or 0)
