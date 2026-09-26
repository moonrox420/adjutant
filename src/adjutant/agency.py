"""Agency platform: Multi-brand management, client approver scoping,
atomic bulk operations, and cross-client rollups.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection

from adjutant.errors import DomainError


@dataclass(frozen=True)
class BulkOperationResult:
    total_brands: int
    succeeded_count: int
    failed_count: int
    successful_brand_ids: list[str]
    failed_brands: list[dict[str, Any]]


def verify_client_approver_brand_access(
    conn: Connection,
    user_id: UUID,
    target_brand_id: UUID,
) -> bool:
    """S12.1: A client approver can approve only their own brand and read nothing else."""
    seats = conn.execute(
        """SELECT role, brand_id, account_id FROM seat
        WHERE user_id=%s AND revoked_at IS NULL""",
        (user_id,),
    ).fetchall()
    if not seats:
        raise DomainError("Unauthorized", "User is not a member of any account.", 403)

    for s in seats:
        role = s["role"]
        if role in {"owner", "admin", "buyer"}:
            brand_match = conn.execute(
                "SELECT 1 FROM brand WHERE id=%s AND account_id=%s",
                (target_brand_id, s["account_id"]),
            ).fetchone()
            if brand_match:
                return True
        elif role == "client_approver":
            if s["brand_id"] == target_brand_id:
                return True

    client_approver_seats = [s for s in seats if s["role"] == "client_approver"]
    if client_approver_seats:
        assigned_brand = client_approver_seats[0]["brand_id"]
        raise DomainError(
            "AccessDenied",
            f"Client approver is authorized exclusively for brand {assigned_brand}, "
            f"cannot access {target_brand_id}.",
            403,
        )
    return False


def execute_bulk_brand_operation(
    conn: Connection,
    account_id: UUID,
    brand_ids: list[UUID],
    operation_kind: str,
    params: dict[str, Any],
) -> BulkOperationResult:
    """S12.3: Bulk operation across multiple brands is atomic per brand.

    Partial failure affects only the brands that failed and reports which.
    """
    succeeded = []
    failed = []

    for brand_id in brand_ids:
        # Verify brand belongs to account
        brand = conn.execute(
            "SELECT id, status FROM brand WHERE id=%s AND account_id=%s",
            (brand_id, account_id),
        ).fetchone()
        if not brand:
            failed.append(
                {
                    "brand_id": str(brand_id),
                    "error": "BrandNotFoundInAccount",
                    "message": "Brand does not belong to the agency account.",
                }
            )
            continue

        try:
            # Use savepoint so failure on one brand rolls back ONLY that brand's mutation
            with conn.savepoint():
                if operation_kind == "set_monthly_ceiling":
                    new_monthly = Decimal(str(params["monthly_usd_max"]))
                    new_daily = Decimal(
                        str(params.get("daily_usd_max", new_monthly / Decimal("28.0")))
                    )
                    conn.execute(
                        """UPDATE budget_ceiling SET monthly_usd_max=%s, daily_usd_max=%s
                        WHERE brand_id=%s AND scope_kind='brand'""",
                        (new_monthly, new_daily, brand_id),
                    )
                    conn.execute(
                        """UPDATE guardrail SET monthly_spend_cap_usd=%s, daily_spend_cap_usd=%s
                        WHERE brand_id=%s""",
                        (new_monthly, new_daily, brand_id),
                    )
                elif operation_kind == "pause_all_campaigns":
                    conn.execute(
                        "UPDATE brand SET campaigns_enabled=false WHERE id=%s",
                        (brand_id,),
                    )
                    conn.execute(
                        "UPDATE campaign_object SET state='paused' "
                        "WHERE brand_id=%s AND state='active'",
                        (brand_id,),
                    )
                elif operation_kind == "add_blocked_claim":
                    claim = str(params["claim"]).strip()
                    conn.execute(
                        """UPDATE guardrail SET blocked_claims = array_append(blocked_claims, %s)
                        WHERE brand_id=%s AND NOT (%s = ANY(blocked_claims))""",
                        (claim, brand_id, claim),
                    )
                else:
                    raise DomainError(
                        "UnsupportedBulkOperation",
                        f"Operation '{operation_kind}' is not supported.",
                        400,
                    )

                succeeded.append(str(brand_id))

        except Exception as exc:
            failed.append(
                {
                    "brand_id": str(brand_id),
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )

    return BulkOperationResult(
        total_brands=len(brand_ids),
        succeeded_count=len(succeeded),
        failed_count=len(failed),
        successful_brand_ids=succeeded,
        failed_brands=failed,
    )


def compute_cross_client_rollup(
    conn: Connection,
    account_id: UUID,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """S12.2: Cross-client rollups never mix comparability classes without explicit annotation."""
    facts = conn.execute(
        """SELECT
            mn.brand_id,
            b.display_name AS brand_name,
            mn.channel,
            mn.metric_key,
            mn.metric_value,
            mn.comparability,
            mn.methodology_note
        FROM metric_normalized mn
        JOIN brand b ON b.id = mn.brand_id
        WHERE b.account_id=%s
          AND mn.date_hour >= %s::timestamptz
          AND mn.date_hour <= %s::timestamptz""",
        (account_id, start_date, end_date),
    ).fetchall()

    grouped_by_comparability: dict[str, list[dict[str, Any]]] = {}
    brand_summaries: dict[str, dict[str, Any]] = {}

    for f in facts:
        comp_class = f["comparability"]
        grouped_by_comparability.setdefault(comp_class, []).append(f)

        bid = str(f["brand_id"])
        brand_summaries.setdefault(
            bid,
            {
                "brand_name": f["brand_name"],
                "spend": Decimal("0.00"),
                "conversions": Decimal("0.00"),
            },
        )
        if f["metric_key"] == "spend":
            brand_summaries[bid]["spend"] += Decimal(str(f["metric_value"]))
        elif f["metric_key"] == "conversions":
            brand_summaries[bid]["conversions"] += Decimal(str(f["metric_value"]))

    rollup_by_class = {}
    for comp_class, group in grouped_by_comparability.items():
        spend_facts = [item for item in group if item["metric_key"] == "spend"]
        conv_facts = [item for item in group if item["metric_key"] == "conversions"]

        total_spend = sum(Decimal(str(x["metric_value"])) for x in spend_facts)
        total_conv = sum(Decimal(str(x["metric_value"])) for x in conv_facts)
        blended_cpa = (total_spend / total_conv) if total_conv > 0 else Decimal("0.00")

        rollup_by_class[comp_class] = {
            "comparability_class": comp_class,
            "annotation": (
                "Direct last-click methodology"
                if comp_class == "direct"
                else (
                    "Caveated view-through or multi-touch attribution; "
                    "cannot be merged into direct CPA"
                )
            ),
            "total_spend_usd": str(total_spend),
            "total_conversions": str(total_conv),
            "blended_cpa_usd": str(blended_cpa.quantize(Decimal("0.01"))),
            "fact_count": len(group),
        }

    return {
        "account_id": str(account_id),
        "start_date": start_date,
        "end_date": end_date,
        "rollups_by_comparability_class": rollup_by_class,
        "brand_count": len(brand_summaries),
        "brand_summaries": {
            k: {
                "brand_name": v["brand_name"],
                "spend_usd": str(v["spend"]),
                "conversions": str(v["conversions"]),
            }
            for k, v in brand_summaries.items()
        },
    }
