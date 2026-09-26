"""S0 HTTP operations: encrypted credential writes and a durable checkpoint workflow."""

import json
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field, SecretStr

from adjutant.credentials import CredentialStore
from adjutant.db import Database, Principal, one, require_role
from adjutant.errors import DomainError
from adjutant.storage import ObjectStore
from adjutant.telemetry import trace_id


class WorkflowInput(BaseModel):
    request_key: UUID
    delay_seconds: int = Field(default=5, ge=0, le=300, strict=True)


class CredentialInput(BaseModel):
    value: SecretStr = Field(min_length=1, max_length=16384)


def foundation_router(
    db: Database,
    credentials: CredentialStore,
    storage: ObjectStore,
    authenticate: Callable[[Request], Principal],
) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}", tags=["foundation"])

    actor_type = Annotated[Principal, Depends(authenticate)]

    @router.put("/credentials/{name}", status_code=204)
    def write_credential(
        brand_id: UUID,
        name: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,63}$")],
        data: CredentialInput,
        actor: actor_type,
    ) -> None:
        with db.transaction(actor) as conn:
            require_role(conn, brand_id, {"owner", "admin"})
            credentials.write(conn, brand_id, name, data.value.get_secret_value())

    @router.post("/workflows", status_code=202)
    def start_workflow(brand_id: UUID, data: WorkflowInput, actor: actor_type) -> dict:
        with db.transaction(actor) as conn:
            require_role(conn, brand_id, {"owner", "admin", "buyer"})
            conn.execute(
                "INSERT INTO workflow_run(brand_id,request_key,delay_seconds,trace_id) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT (brand_id,request_key) DO NOTHING",
                (brand_id, data.request_key, data.delay_seconds, trace_id.get()),
            )
            row = one(
                conn,
                "SELECT * FROM workflow_run WHERE brand_id=%s AND request_key=%s",
                (brand_id, data.request_key),
            )
            if row["delay_seconds"] != data.delay_seconds:
                raise DomainError(
                    "IdempotencyConflict",
                    "Request key already has different input.",
                    409,
                )
            return row

    @router.get("/workflows/{workflow_id}")
    def get_workflow(brand_id: UUID, workflow_id: UUID, actor: actor_type) -> dict:
        with db.transaction(actor) as conn:
            return one(
                conn,
                "SELECT * FROM workflow_run WHERE brand_id=%s AND id=%s",
                (brand_id, workflow_id),
            )

    @router.get("/workflows/{workflow_id}/result")
    def get_result(brand_id: UUID, workflow_id: UUID, actor: actor_type) -> dict:
        with db.transaction(actor) as conn:
            row = one(
                conn,
                "SELECT * FROM workflow_run WHERE brand_id=%s AND id=%s",
                (brand_id, workflow_id),
            )
        if row["state"] != "completed":
            raise DomainError("WorkflowPending", "Workflow has not completed yet.", 409)
        return json.loads(storage.read(brand_id, row["result_key"]))

    return router
