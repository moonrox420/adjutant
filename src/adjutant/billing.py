"""Self-serve flat-tier billing, dunning state machines, and safe cancellation
preventing stranded ad spend.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection

from adjutant.errors import DomainError
from adjutant.remote_stop import enqueue_stop

# S14 Pricing Constraint: Flat tiers by brand count and spend band, NEVER percentage of ad spend
BILLING_TIERS = {
    "starter": {
        "tier_name": "Starter",
        "monthly_fee_usd": Decimal("199.00"),
        "max_brands": 1,
        "max_monthly_spend_usd": Decimal("5000.00"),
    },
    "growth": {
        "tier_name": "Growth",
        "monthly_fee_usd": Decimal("499.00"),
        "max_brands": 3,
        "max_monthly_spend_usd": Decimal("25000.00"),
    },
    "scale": {
        "tier_name": "Scale",
        "monthly_fee_usd": Decimal("999.00"),
        "max_brands": 10,
        "max_monthly_spend_usd": Decimal("100000.00"),
    },
    "agency_enterprise": {
        "tier_name": "Agency Enterprise",
        "monthly_fee_usd": Decimal("2499.00"),
        "max_brands": 50,
        "max_monthly_spend_usd": Decimal("500000.00"),
    },
}


def create_subscription(
    conn: Connection[Any],
    account_id: UUID,
    tier_id: str,
    payment_method_id: str,
) -> dict[str, Any]:
    """Create or update account subscription with flat-tier pricing."""
    tier = BILLING_TIERS.get(tier_id)
    if not tier:
        raise DomainError("InvalidTier", f"Billing tier '{tier_id}' does not exist.", 400)

    now = datetime.now(UTC)
    period_end = now + timedelta(days=30)

    conn.execute(
        """INSERT INTO account_subscription(
            account_id, tier_id, monthly_fee_usd, status,
            current_period_start, current_period_end, payment_method_id
        ) VALUES (%s, %s, %s, 'active', %s, %s, %s)
        ON CONFLICT (account_id) DO UPDATE SET
            tier_id = EXCLUDED.tier_id,
            monthly_fee_usd = EXCLUDED.monthly_fee_usd,
            status = 'active',
            current_period_start = EXCLUDED.current_period_start,
            current_period_end = EXCLUDED.current_period_end,
            payment_method_id = EXCLUDED.payment_method_id""",
        (
            account_id,
            tier_id,
            tier["monthly_fee_usd"],
            now,
            period_end,
            payment_method_id,
        ),
    )

    conn.execute(
        "UPDATE account SET billing_status='active' WHERE id=%s",
        (account_id,),
    )

    return {
        "account_id": str(account_id),
        "tier": tier["tier_name"],
        "monthly_fee_usd": str(tier["monthly_fee_usd"]),
        "status": "active",
        "current_period_end": period_end.isoformat(),
    }


def handle_billing_failure(
    conn: Connection[Any],
    account_id: UUID,
    failure_reason: str,
) -> dict[str, Any]:
    """S14.3: Handle billing failure and transition to dunning without
    immediately killing campaigns.
    """
    now = datetime.now(UTC)
    grace_period_end = now + timedelta(days=5)

    conn.execute(
        """UPDATE account_subscription SET
            status = 'dunning',
            dunning_started_at = %s,
            grace_period_end = %s,
            last_payment_error = %s
        WHERE account_id=%s""",
        (now, grace_period_end, failure_reason, account_id),
    )
    conn.execute(
        "UPDATE account SET billing_status='dunning' WHERE id=%s",
        (account_id,),
    )

    return {
        "account_id": str(account_id),
        "status": "dunning",
        "grace_period_end": grace_period_end.isoformat(),
        "warning": (
            "Payment failed. Campaigns will remain running for 5 days before pause on cancellation."
        ),
    }


def cancel_subscription(
    conn: Connection[Any],
    account_id: UUID,
    actor_id: UUID | None = None,
    immediate_pause_campaigns: bool = True,
) -> dict[str, Any]:
    """S14.3: Cancel subscription safely without stranding live campaigns
    spending unmonitored money.
    """
    conn.execute(
        """UPDATE account_subscription SET
            status = 'cancelled',
            cancelled_at = now()
        WHERE account_id=%s""",
        (account_id,),
    )
    conn.execute(
        "UPDATE account SET billing_status='cancelled' WHERE id=%s",
        (account_id,),
    )

    paused_brands = []
    stop_run_ids = []
    if immediate_pause_campaigns:
        # Enqueue verified remote stops across all brands in the account so spend does not run wild
        brands: Any = conn.execute(
            "SELECT id FROM brand WHERE account_id=%s", (account_id,)
        ).fetchall()

        owner_row: Any = conn.execute(
            "SELECT user_id FROM seat WHERE account_id=%s AND role='owner' LIMIT 1",
            (account_id,),
        ).fetchone()
        effective_actor = actor_id or (owner_row["user_id"] if owner_row else account_id)

        for b in brands:
            brand_id = b["id"]
            conn.execute(
                "UPDATE brand SET campaigns_enabled=false, status='paused' WHERE id=%s",
                (brand_id,),
            )
            stop_id = enqueue_stop(conn, brand_id, effective_actor)
            stop_run_ids.append(str(stop_id))
            paused_brands.append(str(brand_id))

    return {
        "account_id": str(account_id),
        "status": "cancelled",
        "campaigns_safely_paused": immediate_pause_campaigns,
        "paused_brand_ids": paused_brands,
        "stop_run_ids": stop_run_ids,
    }
