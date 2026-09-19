import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from adjutant.db import one, require_role
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.models import Decision, PlanInput
from adjutant.security import ApprovalSigner, digest

EDIT_ROLES = {"owner", "admin", "buyer"}
APPROVE_ROLES = {"owner", "admin", "buyer"}
RESTRICTED = {"political", "pharmaceutical", "gambling", "crypto", "financial_income_claims"}


def audit(
    conn: Connection,
    events: EventRegistry,
    brand_id: UUID,
    action: str,
    target: str,
    target_id: UUID,
    detail: dict[str, Any],
    reason: str = "",
) -> UUID:
    row = one(
        conn,
        """INSERT INTO action(brand_id,actor_kind,actor_user_id,action_type,
                      target_kind,target_id,diff,rationale)
                      VALUES(%s,'human',current_actor_id(),%s,%s,%s,%s,%s) RETURNING id""",
        (brand_id, action, target, target_id, Jsonb(detail), reason),
    )
    events.append(
        conn,
        "action.recorded",
        brand_id,
        {
            "brand_id": str(brand_id),
            "action_id": str(row["id"]),
            "action_type": action,
            "actor_kind": "human",
            "target_kind": target,
        },
    )
    return row["id"]


def locked_brand(conn: Connection, brand_id: UUID) -> dict[str, Any]:
    return one(conn, "SELECT * FROM brand WHERE id=%s FOR UPDATE", (brand_id,))


def generation_gate(conn: Connection, brand: dict[str, Any]) -> None:
    if brand["restricted_flags"] or not brand["campaigns_enabled"]:
        raise DomainError("VerticalBlocked", "Campaign creation is blocked for this vertical.")
    if not brand["brand_graph_confirmed_at"]:
        raise DomainError("GraphUnconfirmed", "Confirm the brand's sourced facts before planning.")
    if conn.execute(
        "SELECT 1 FROM brand_kill_switch WHERE brand_id=%s AND released_at IS NULL", (brand["id"],)
    ).fetchone():
        raise DomainError(
            "KillSwitchActive", "This brand is stopped. Resolve the stop before planning."
        )


def validate_plan(conn: Connection, brand_id: UUID, plan: PlanInput) -> None:
    ceiling = one(
        conn, "SELECT * FROM budget_ceiling WHERE brand_id=%s AND scope_kind='brand'", (brand_id,)
    )
    daily = sum(a.daily_budget_usd for a in plan.allocations)
    if (
        plan.monthly_budget_usd > ceiling["monthly_usd_max"]
        or ceiling["daily_usd_max"] is None
        or daily > ceiling["daily_usd_max"]
    ):
        raise DomainError(
            "BudgetCeilingExceeded", "The plan exceeds the brand's current budget ceiling."
        )
    for allocation in plan.allocations:
        capability = one(
            conn,
            """SELECT * FROM channel_capability WHERE channel=%s
                                ORDER BY registry_version DESC LIMIT 1""",
            (allocation.channel,),
        )
        if plan.objective not in capability["objectives"]:
            raise DomainError(
                "UnsupportedObjective",
                f"{allocation.channel} does not support {plan.objective} in the registry.",
            )
        limits = conn.execute(
            """SELECT * FROM budget_ceiling WHERE brand_id=%s
                               AND scope_kind='channel' AND scope_ref=%s""",
            (brand_id, allocation.channel),
        ).fetchall()
        for limit in limits:
            if (
                allocation.monthly_budget_usd > limit["monthly_usd_max"]
                or limit["daily_usd_max"] is None
                or allocation.daily_budget_usd > limit["daily_usd_max"]
            ):
                raise DomainError(
                    "BudgetCeilingExceeded", "A channel allocation exceeds its ceiling."
                )


def persist_plan(
    conn: Connection,
    events: EventRegistry,
    brand_id: UUID,
    data: PlanInput,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_plan(conn, brand_id, data)
    document = data.model_dump(mode="json")
    document["revision"] = existing["plan_document"].get("revision", 1) + 1 if existing else 1
    plan_hash = digest(document)
    params = (
        data.name,
        data.objective,
        data.goal_kind,
        data.goal_value,
        plan_hash,
        Jsonb(document),
        data.rationale,
        data.monthly_budget_usd,
    )
    if existing:
        plan = one(
            conn,
            """UPDATE plan SET name=%s,objective=%s,goal_kind=%s,goal_value=%s,
                          plan_hash=%s,plan_document=%s,rationale=%s,monthly_budget_usd=%s,
                          state='draft' WHERE id=%s RETURNING *""",
            (*params, existing["id"]),
        )
        conn.execute("DELETE FROM plan_allocation WHERE plan_id=%s", (plan["id"],))
    else:
        plan = one(
            conn,
            """INSERT INTO plan(name,objective,goal_kind,goal_value,plan_hash,
                           plan_document,rationale,monthly_budget_usd,brand_id,created_by_actor)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'human') RETURNING *""",
            (*params, brand_id),
        )
    for allocation in data.allocations:
        conn.execute(
            """INSERT INTO plan_allocation(plan_id,brand_id,channel,monthly_budget_usd,
                     daily_budget_usd) VALUES(%s,%s,%s,%s,%s)""",
            (
                plan["id"],
                brand_id,
                allocation.channel,
                allocation.monthly_budget_usd,
                allocation.daily_budget_usd,
            ),
        )
    audit(
        conn,
        events,
        brand_id,
        "plan_edit" if existing else "plan_create",
        "plan",
        plan["id"],
        {"before": existing["plan_document"] if existing else None, "after": document},
        data.rationale,
    )
    events.append(
        conn,
        "plan.drafted",
        brand_id,
        {
            "brand_id": str(brand_id),
            "plan_id": str(plan["id"]),
            "plan_hash": plan_hash,
            "channels": [a.channel for a in data.allocations],
            "objective": data.objective,
            "monthly_budget_usd": float(data.monthly_budget_usd),
        },
    )
    return plan


def check_plan_integrity(plan: dict[str, Any]) -> PlanInput:
    document = dict(plan["plan_document"])
    if digest(document) != plan["plan_hash"]:
        raise DomainError(
            "SubjectHashMismatch", "The plan document no longer matches its approved hash."
        )
    document.pop("revision", None)
    data = PlanInput.model_validate(document)
    if any(
        getattr(data, name) != plan[name]
        for name in (
            "name",
            "objective",
            "goal_kind",
            "goal_value",
            "monthly_budget_usd",
            "rationale",
        )
    ):
        raise DomainError("SubjectHashMismatch", "Plan fields no longer match the signed document.")
    return data


def request_approval(
    conn: Connection,
    events: EventRegistry,
    brand_id: UUID,
    plan_id: UUID,
    expected_hash: str,
    client_approval: bool,
) -> dict[str, Any]:
    brand = locked_brand(conn, brand_id)
    require_role(conn, brand_id, EDIT_ROLES)
    generation_gate(conn, brand)
    plan = one(
        conn, "SELECT * FROM plan WHERE id=%s AND brand_id=%s FOR UPDATE", (plan_id, brand_id)
    )
    if plan["plan_hash"] != expected_hash:
        raise DomainError("SubjectChanged", "The plan changed. Reload it before submitting.")
    if plan["state"] != "draft":
        raise DomainError("InvalidTransition", "Only a draft plan can enter review.")
    if conn.execute(
        """SELECT 1 FROM approval_request WHERE subject_id=%s
                       AND subject_hash=%s""",
        (plan_id, expected_hash),
    ).fetchone():
        raise DomainError(
            "NewRevisionRequired",
            "This revision was already reviewed or voided. Save a new revision first.",
        )
    data = check_plan_integrity(plan)
    validate_plan(conn, brand_id, data)
    expires = datetime.now(UTC) + timedelta(hours=72)
    daily = sum(a.daily_budget_usd for a in data.allocations)
    request = one(
        conn,
        """INSERT INTO approval_request(brand_id,subject_type,subject_id,subject_hash,
                         requested_daily_usd,requested_total_usd,rationale,requires_client_approval,
                         expires_at) VALUES(%s,'plan',%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (
            brand_id,
            plan_id,
            expected_hash,
            daily,
            data.monthly_budget_usd,
            data.rationale,
            client_approval,
            expires,
        ),
    )
    conn.execute("UPDATE plan SET state='pending_approval' WHERE id=%s", (plan_id,))
    audit(
        conn,
        events,
        brand_id,
        "plan_submit",
        "plan",
        plan_id,
        {"approval_request_id": str(request["id"]), "subject_hash": expected_hash},
    )
    events.append(
        conn,
        "approval.requested",
        brand_id,
        {
            "brand_id": str(brand_id),
            "approval_request_id": str(request["id"]),
            "subject_type": "plan",
            "subject_id": str(plan_id),
            "subject_hash": expected_hash,
            "stage": "pending_internal",
            "expires_at": expires.isoformat(),
            "requested_usd_daily": float(daily),
            "requested_usd_total": float(data.monthly_budget_usd),
        },
    )
    return request


def decide(
    conn: Connection,
    events: EventRegistry,
    signer: ApprovalSigner,
    brand_id: UUID,
    approval_id: UUID,
    actor: UUID,
    decision: Decision,
) -> dict[str, Any]:
    brand = locked_brand(conn, brand_id)
    request = one(
        conn,
        "SELECT * FROM approval_request WHERE id=%s AND brand_id=%s FOR UPDATE",
        (approval_id, brand_id),
    )
    stage = request["state"]
    if stage not in {"pending_internal", "pending_client"}:
        raise DomainError("InvalidTransition", "This approval is no longer awaiting a decision.")
    if request["subject_type"] != "plan":
        raise DomainError("InvalidSubject", "This endpoint reviews campaign plans only.")
    roles = {"client_approver"} if stage == "pending_client" else APPROVE_ROLES
    seat = require_role(conn, brand_id, roles)
    now = datetime.now(UTC)
    if request["expires_at"] <= now:
        raise DomainError("ApprovalExpired", "This request expired. Edit and resubmit the plan.")
    plan = one(conn, "SELECT * FROM plan WHERE id=%s FOR UPDATE", (request["subject_id"],))
    if not plan["plan_hash"] == request["subject_hash"] == decision.expected_hash:
        raise DomainError("SubjectChanged", "The plan changed. Review its current revision.")
    if decision.decision == "approved":
        generation_gate(conn, brand)
        data = check_plan_integrity(plan)
        validate_plan(conn, brand_id, data)
        if (
            seat["approval_daily_usd_cap"] is None
            or seat["approval_total_usd_cap"] is None
            or request["requested_daily_usd"] > seat["approval_daily_usd_cap"]
            or request["requested_total_usd"] > seat["approval_total_usd_cap"]
        ):
            raise DomainError(
                "SpendAuthorityExceeded", "This plan exceeds your approval authority.", 403
            )
        if stage == "pending_client" and request["internal_approver_id"] == actor:
            raise DomainError(
                "IndependentApprovalRequired", "A separate client reviewer must approve.", 403
            )
        next_state = (
            "pending_client"
            if (stage == "pending_internal" and request["requires_client_approval"])
            else "approved"
        )
        if stage == "pending_internal":
            conn.execute(
                """UPDATE approval_request SET internal_approver_id=%s,internal_approved_at=%s,
                         state=%s WHERE id=%s""",
                (actor, now, next_state, approval_id),
            )
        else:
            conn.execute(
                """UPDATE approval_request SET client_approver_id=%s,client_approved_at=%s,
                         state=%s WHERE id=%s""",
                (actor, now, next_state, approval_id),
            )
        token_id = None
        if next_state == "approved":
            token_id = issue_token(conn, events, signer, request, data, actor, now)
            conn.execute("UPDATE plan SET state='approved' WHERE id=%s", (plan["id"],))
        action = "plan_approve"
    else:
        next_state = decision.decision
        token_id = None
        conn.execute(
            """UPDATE approval_request SET state=%s,rejection_reason='other',rejection_detail=%s
                     WHERE id=%s""",
            (next_state, decision.reason, approval_id),
        )
        conn.execute(
            "UPDATE plan SET state=%s WHERE id=%s",
            ("rejected" if next_state == "rejected" else "draft", plan["id"]),
        )
        action = "approval_reject" if next_state == "rejected" else "approval_request_changes"
    audit(
        conn,
        events,
        brand_id,
        action,
        "approval_request",
        approval_id,
        {"before": stage, "after": next_state, "subject_hash": request["subject_hash"]},
        decision.reason,
    )
    events.append(
        conn,
        "approval.decided",
        brand_id,
        {
            "brand_id": str(brand_id),
            "approval_request_id": str(approval_id),
            "stage": stage,
            "decision": decision.decision,
            "decided_by": str(actor),
            "decided_at": now.isoformat(),
        },
    )
    return {"state": next_state, "token_id": token_id}


def issue_token(
    conn: Connection,
    events: EventRegistry,
    signer: ApprovalSigner,
    request: dict[str, Any],
    data: PlanInput,
    actor: UUID,
    now: datetime,
) -> UUID:
    token_id = uuid4()
    brand_id = request["brand_id"]
    expires = min(request["expires_at"], now + timedelta(hours=72)).replace(microsecond=0)
    chain = [request["internal_approver_id"], actor] if request["internal_approver_id"] else [actor]
    scopes = [f"channel:{a.channel}" for a in data.allocations] + ["op:create"]
    claims = {
        "tok": str(token_id),
        "sub_type": "plan",
        "sub_id": str(request["subject_id"]),
        "sub_hash": request["subject_hash"],
        "brand_id": str(brand_id),
        "scopes": scopes,
        "usd_daily_cap": str(request["requested_daily_usd"]),
        "usd_total_cap": str(request["requested_total_usd"]),
        "approver_id": str(actor),
        "approval_chain": list(map(str, chain)),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "nonce": secrets.token_hex(24),
    }
    conn.execute(
        """INSERT INTO approval_token(id,approval_request_id,brand_id,subject_type,subject_id,
                 subject_hash,scopes,usd_daily_cap,usd_total_cap,approver_id,approval_chain,
                 signing_key_id,signature,nonce,issued_at,expires_at,signed_claims)
                 VALUES(%s,%s,%s,'plan',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            token_id,
            request["id"],
            brand_id,
            request["subject_id"],
            request["subject_hash"],
            scopes,
            Decimal(claims["usd_daily_cap"]),
            Decimal(claims["usd_total_cap"]),
            actor,
            chain,
            signer.key_id,
            signer.sign(claims),
            claims["nonce"],
            now,
            expires,
            Jsonb(claims),
        ),
    )
    payload = {
        "brand_id": str(brand_id),
        "token_id": str(token_id),
        "approval_request_id": str(request["id"]),
        "subject_type": "plan",
        "subject_id": str(request["subject_id"]),
        "subject_hash": request["subject_hash"],
        "scopes": scopes,
        "usd_daily_cap": float(request["requested_daily_usd"]),
        "usd_total_cap": float(request["requested_total_usd"]),
        "expires_at": expires.isoformat(),
    }
    events.append(conn, "approval.token.issued", brand_id, payload)
    events.append(
        conn,
        "plan.approved",
        brand_id,
        {
            "brand_id": str(brand_id),
            "plan_id": str(request["subject_id"]),
            "plan_hash": request["subject_hash"],
            "approval_request_id": str(request["id"]),
            "token_id": str(token_id),
        },
    )
    return token_id
