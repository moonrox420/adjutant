"""Account lifecycle with single-use credentials and durable delivery."""

import secrets
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from adjutant.config import Settings
from adjutant.db import Database, one
from adjutant.errors import DomainError
from adjutant.security import password_hash, session_digest


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=15, max_length=128)
    confirm_password: str = Field(min_length=15, max_length=128)

    @model_validator(mode="after")
    def matching_password(self) -> "PasswordInput":
        if self.password != self.confirm_password:
            raise ValueError("Passwords must match.")
        if len(self.password.strip()) < 15:
            raise ValueError("Use at least 15 characters excluding surrounding spaces.")
        return self


class Registration(EmailInput, PasswordInput):
    full_name: str = Field(min_length=1, max_length=120)
    workspace_name: str = Field(min_length=1, max_length=120)
    account_type: Literal["business", "agency"]

    @field_validator("full_name", "workspace_name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a name.")
        return value.strip()


class TokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=40, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ResetInput(TokenInput, PasswordInput):
    """Reset token and matching replacement password; no whitespace normalization."""


def rate_limit(db: Database, key: str) -> None:
    with db.transaction() as conn:
        allowed = one(
            conn, "SELECT check_login_rate(%s) AS allowed", (session_digest(key),)
        )
    if not allowed["allowed"]:
        raise DomainError(
            "RateLimited", "Too many requests. Try again in 15 minutes.", 429
        )


def cancellation_report(
    db: Database, token_hash: str | list[str], run_ids: list | None = None
) -> dict:
    """Wait for the supervising process to acknowledge termination, never infer it from a flag."""
    hashes = [token_hash] if isinstance(token_hash, str) else token_hash
    deadline = time.monotonic() + 8
    while True:
        with db.transaction() as conn:
            jobs = conn.execute(
                "SELECT j.* FROM unnest(%s::text[]) h "
                "CROSS JOIN LATERAL cancelled_session_jobs(h) j "
                "WHERE (%s::uuid[] IS NULL OR j.id=ANY(%s::uuid[]))",
                (hashes, run_ids, run_ids),
            ).fetchall()
        pending = [
            job
            for job in jobs
            if job["finished_at"] is None
            or (
                job["worker_pid"] is not None and job["worker_exit_verified_at"] is None
            )
        ]
        if not pending or time.monotonic() >= deadline:
            return {"cancellation_verified": not pending, "jobs": jobs}
        time.sleep(0.1)


def auth_router(db: Database, config: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    def message(purpose: str, token: str) -> str:
        action = (
            "verify your email address"
            if purpose == "verify"
            else "reset your password"
        )
        return (
            f"Use this link to {action}:\n\n{config.public_origin}/account#{purpose}={token}\n\n"
            "This link expires in one hour and works once. If you did not request it, ignore it."
        )

    def accepted() -> dict:
        return {
            "status": "accepted",
            "delivery": config.mail_transport,
            "message": "If this address is eligible, an account email has been queued.",
            "local_mailbox": (
                str(config.mail_directory) if config.mail_transport == "file" else None
            ),
        }

    @router.get("/options")
    def options() -> dict:
        return {"delivery": config.mail_transport, "minimum_password_length": 15}

    @router.post("/register", status_code=202)
    def register(data: Registration, request: Request) -> dict:
        rate_limit(
            db, f"register-ip:{request.client.host if request.client else 'unknown'}"
        )
        rate_limit(db, f"account:{data.email}")
        token = secrets.token_urlsafe(48)
        encoded = password_hash(data.password)
        with db.transaction() as conn:
            conn.execute(
                "SELECT register_account(%s,%s,%s,%s,%s,%s,%s::account_type)",
                (
                    data.email,
                    data.full_name,
                    data.workspace_name,
                    encoded,
                    session_digest(token),
                    message("verify", token),
                    data.account_type,
                ),
            )
        return accepted()

    @router.post("/request-link", status_code=202)
    def request_link(
        data: EmailInput, purpose: Literal["verify", "reset"], request: Request
    ) -> dict:
        rate_limit(
            db, f"links-ip:{request.client.host if request.client else 'unknown'}"
        )
        rate_limit(db, f"account:{data.email}")
        token = secrets.token_urlsafe(48)
        with db.transaction() as conn:
            conn.execute(
                "SELECT request_account_token(%s,%s,%s,%s)",
                (data.email, purpose, session_digest(token), message(purpose, token)),
            )
        return accepted()

    @router.post("/verify")
    def verify(data: TokenInput) -> dict:
        with db.transaction() as conn:
            valid = one(
                conn,
                "SELECT consume_account_token(%s,'verify',NULL) AS valid",
                (session_digest(data.token),),
            )["valid"]
        if not valid:
            raise DomainError(
                "InvalidLink", "This link is invalid, expired, or already used.", 400
            )
        return {"status": "verified", "message": "Email verified. You can now sign in."}

    @router.post("/reset-password")
    def reset(data: ResetInput) -> dict:
        with db.transaction() as conn:
            valid = one(
                conn,
                "SELECT consume_account_token(%s,'reset',%s) AS valid",
                (session_digest(data.token), password_hash(data.password)),
            )["valid"]
        if not valid:
            raise DomainError(
                "InvalidLink", "This link is invalid, expired, or already used.", 400
            )
        return {
            "status": "password_reset",
            "message": "Password changed. All sessions were revoked.",
        }

    return router
