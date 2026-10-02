"""Read, pause, and independently verify advertising campaigns through provider APIs."""

import asyncio
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from adjutant.adapters.authorization import provider_for
from adjutant.errors import DomainError


@dataclass(frozen=True)
class CampaignTarget:
    channel: str
    account_id: str
    native_id: str
    metadata: dict[str, Any]


def identifier(value: str) -> str:
    if not value or len(value) > 200 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise DomainError("InvalidRemoteIdentity", "The stored platform identity is invalid.", 422)
    return quote(value, safe="")


def numeric(value: str) -> str:
    if not re.fullmatch(r"[0-9]+", value):
        raise DomainError("InvalidRemoteIdentity", "The platform requires a numeric identity.", 422)
    return value


class CampaignControl:
    """Only pause is exposed here; resuming must pass the spend gateway independently."""

    def __init__(self, client: httpx.AsyncClient, target: CampaignTarget, app: dict, token: dict):
        provider_for(target.channel)
        self.client = client
        self.target = target
        self.app = app
        self.account = identifier(target.account_id)
        self.identity = identifier(target.native_id)
        self.headers = {
            "Authorization": "Bearer " + token["access_token"],
            "User-Agent": "Adjutant/2.0",
        }
        if target.channel in {"google_ads", "youtube"}:
            numeric(self.account)
            numeric(self.identity)
            self.headers["developer-token"] = app["developer_token"]
            if target.metadata.get("login_customer_id"):
                self.headers["login-customer-id"] = numeric(
                    str(target.metadata["login_customer_id"])
                )
        elif target.channel == "linkedin":
            self.headers.update(
                {"LinkedIn-Version": "202608", "X-Restli-Protocol-Version": "2.0.0"}
            )

    async def request(self, method: str, url: str, **kwargs: Any) -> dict:
        """Reject redirects and partial failures, and never include credentials in errors."""
        headers = {**self.headers, **kwargs.pop("headers", {})}
        try:
            response = await self.client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise DomainError(
                "PlatformUnavailable",
                "The platform did not respond; remote state is unverified.",
                503,
            ) from exc
        if response.status_code in {401, 403}:
            raise DomainError(
                "PlatformAuthorization",
                "Reauthorize this account and check application permissions.",
                403,
            )
        if response.status_code == 429:
            raise DomainError(
                "PlatformRateLimited",
                "The platform rate limit prevented verification. Retry pause.",
                429,
            )
        if not 200 <= response.status_code < 300:
            raise DomainError(
                "PlatformRequestRejected",
                f"Platform HTTP {response.status_code}; remote state is unverified.",
                502,
            )
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise DomainError(
                "PlatformResponseInvalid", "The platform returned invalid JSON.", 502
            ) from exc
        if not isinstance(data, dict):
            raise DomainError(
                "PlatformResponseInvalid",
                "The platform returned an invalid response shape.",
                502,
            )
        if (
            data.get("error")
            or data.get("partialFailureError")
            or data.get("PartialErrors")
            or data.get("OperationErrors")
            or data.get("code", 0) not in (0, "0")
            or str(data.get("request_status", "SUCCESS")).upper() != "SUCCESS"
        ):
            raise DomainError(
                "PlatformRequestRejected",
                "The platform rejected the operation; inspect its account console.",
                502,
            )
        return data

    async def read(self) -> dict:
        """Read one exact identity within its account; missing rows never mean paused."""
        channel = self.target.channel
        account, identity = self.account, self.identity
        try:
            if channel == "meta":
                row = await self.request(
                    "GET",
                    f"https://graph.facebook.com/v26.0/{identity}",
                    params={"fields": "id,account_id,status"},
                )
                if str(row["account_id"]) != account.removeprefix("act_"):
                    raise ValueError("Wrong account")
                status = row["status"]
            elif channel in {"google_ads", "youtube"}:
                data = await self.request(
                    "POST",
                    f"https://googleads.googleapis.com/v25/customers/{account}/googleAds:search",
                    json={
                        "query": "SELECT campaign.id,campaign.status FROM campaign "
                        f"WHERE campaign.id = {identity}"
                    },
                )
                row = data["results"][0]["campaign"]
                status = row["status"]
            elif channel == "linkedin":
                row = await self.request(
                    "GET",
                    f"https://api.linkedin.com/rest/adAccounts/{account}/adCampaigns/{identity}",
                )
                status = row["status"]
            elif channel == "reddit":
                data = await self.request(
                    "GET", f"https://ads-api.reddit.com/api/v3/campaigns/{identity}"
                )
                row = data["data"]
                if str(row["account_id"]) != account:
                    raise ValueError("Wrong account")
                status = row["configured_status"]
            else:
                raise DomainError("UnknownChannel", f"Unsupported channel {channel}", 422)

            if str(row["id"]) != self.target.native_id or not isinstance(status, str):
                raise ValueError("Wrong identity or status")
            state = status.upper()
            if state in {"PAUSED", "DISABLE", "CAMPAIGN_STATUS_DISABLE"}:
                normalized = "paused"
            elif state in {"DELETED", "REMOVED", "ARCHIVED"}:
                normalized = "archived"
            elif state in {"ACTIVE", "ENABLED", "ENABLE", "CAMPAIGN_STATUS_ENABLE"}:
                normalized = "active"
            elif state == "DRAFT":
                normalized = "draft"
            else:
                normalized = "unknown"
            return {
                "native_id": self.target.native_id,
                "state": normalized,
                "provider_status": status,
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DomainError(
                "PlatformResponseInvalid",
                "The requested campaign identity or account could not be verified.",
                502,
            ) from exc

    async def pause(self) -> dict:
        """A mutation acknowledgment is insufficient; independently read back paused state."""
        before = await self.read()
        if before["state"] in {"paused", "archived", "draft"}:
            return {**before, "changed": False}
        channel = self.target.channel
        account, identity = self.account, self.identity
        if channel == "meta":
            await self.request(
                "POST",
                f"https://graph.facebook.com/v26.0/{identity}",
                data={"status": "PAUSED"},
            )
        elif channel in {"google_ads", "youtube"}:
            await self.request(
                "POST",
                f"https://googleads.googleapis.com/v25/customers/{account}/campaigns:mutate",
                json={
                    "operations": [
                        {
                            "update": {
                                "resourceName": f"customers/{account}/campaigns/{identity}",
                                "status": "PAUSED",
                            },
                            "updateMask": "status",
                        }
                    ],
                    "partialFailure": False,
                },
            )
        elif channel == "linkedin":
            await self.request(
                "POST",
                f"https://api.linkedin.com/rest/adAccounts/{account}/adCampaigns/{identity}",
                headers={"X-RestLi-Method": "PARTIAL_UPDATE"},
                json={"patch": {"$set": {"status": "PAUSED"}}},
            )
        elif channel == "reddit":
            await self.request(
                "PATCH",
                f"https://ads-api.reddit.com/api/v3/campaigns/{identity}",
                json={"data": {"configured_status": "PAUSED"}},
            )
        else:
            raise DomainError("UnknownChannel", f"Unsupported channel {channel}", 422)

        for delay in (0, 0.5, 1):
            if delay:
                await asyncio.sleep(delay)
            after = await self.read()
            if after["state"] in {"paused", "archived"}:
                return {**after, "changed": True}
        raise DomainError(
            "PauseUnverified",
            "The platform has not confirmed a paused campaign. Retry pause and inspect the "
            "platform console.",
            502,
        )

    async def resume(self) -> dict:
        """A mutation acknowledgment is insufficient; independently read back active state."""
        before = await self.read()
        if before["state"] == "active":
            return {**before, "changed": False}
        channel = self.target.channel
        account, identity = self.account, self.identity
        if channel == "meta":
            await self.request(
                "POST",
                f"https://graph.facebook.com/v26.0/{identity}",
                data={"status": "ACTIVE"},
            )
        elif channel in {"google_ads", "youtube"}:
            await self.request(
                "POST",
                f"https://googleads.googleapis.com/v25/customers/{account}/campaigns:mutate",
                json={
                    "operations": [
                        {
                            "update": {
                                "resourceName": f"customers/{account}/campaigns/{identity}",
                                "status": "ENABLED",
                            },
                            "updateMask": "status",
                        }
                    ],
                    "partialFailure": False,
                },
            )
        elif channel == "linkedin":
            await self.request(
                "POST",
                f"https://api.linkedin.com/rest/adAccounts/{account}/adCampaigns/{identity}",
                headers={"X-RestLi-Method": "PARTIAL_UPDATE"},
                json={"patch": {"$set": {"status": "ACTIVE"}}},
            )
        elif channel == "reddit":
            await self.request(
                "PATCH",
                f"https://ads-api.reddit.com/api/v3/campaigns/{identity}",
                json={"data": {"configured_status": "ACTIVE"}},
            )
        else:
            raise DomainError("UnknownChannel", f"Unsupported channel {channel}", 422)

        for delay in (0, 0.5, 1):
            if delay:
                await asyncio.sleep(delay)
            after = await self.read()
            if after["state"] == "active":
                return {**after, "changed": True}
        raise DomainError(
            "ResumeUnverified",
            "The platform has not confirmed an active campaign. Retry resume and inspect the "
            "platform console.",
            502,
        )

    async def set_daily_budget(self, daily_budget_usd: Decimal) -> dict:
        """Update campaign daily budget remotely across provider APIs."""
        channel = self.target.channel
        account, identity = self.account, self.identity
        if daily_budget_usd <= Decimal("0.00"):
            raise DomainError("InvalidBudget", "Daily budget must be greater than zero.", 422)

        if channel == "meta":
            cents = int((daily_budget_usd * Decimal("100.00")).quantize(Decimal("1")))
            await self.request(
                "POST",
                f"https://graph.facebook.com/v26.0/{identity}",
                data={"daily_budget": cents},
            )
        elif channel in {"google_ads", "youtube"}:
            search_res = await self.request(
                "POST",
                f"https://googleads.googleapis.com/v25/customers/{account}/googleAds:search",
                json={
                    "query": (
                        "SELECT campaign.campaign_budget FROM campaign "
                        f"WHERE campaign.id = {identity}"
                    )
                },
            )
            results = search_res.get("results", [])
            if not results or not results[0].get("campaign", {}).get("campaignBudget"):
                raise DomainError(
                    "BudgetUpdateFailed",
                    "Could not find campaign budget resource in Google Ads.",
                    502,
                )
            budget_res = results[0]["campaign"]["campaignBudget"]
            amount_micros = str(
                int((daily_budget_usd * Decimal("1000000.00")).quantize(Decimal("1")))
            )
            await self.request(
                "POST",
                f"https://googleads.googleapis.com/v25/customers/{account}/campaignBudgets:mutate",
                json={
                    "operations": [
                        {
                            "update": {
                                "resourceName": budget_res,
                                "amountMicros": amount_micros,
                            },
                            "updateMask": "amountMicros",
                        }
                    ],
                    "partialFailure": False,
                },
            )
        elif channel == "linkedin":
            await self.request(
                "POST",
                f"https://api.linkedin.com/rest/adAccounts/{account}/adCampaigns/{identity}",
                headers={"X-RestLi-Method": "PARTIAL_UPDATE"},
                json={
                    "patch": {
                        "$set": {
                            "dailyBudget": {
                                "amount": str(daily_budget_usd.quantize(Decimal("0.01"))),
                                "currencyCode": "USD",
                            }
                        }
                    }
                },
            )
        elif channel == "reddit":
            cents = int((daily_budget_usd * Decimal("100.00")).quantize(Decimal("1")))
            await self.request(
                "PATCH",
                f"https://ads-api.reddit.com/api/v3/campaigns/{identity}",
                json={"data": {"daily_budget": cents}},
            )
        else:
            raise DomainError("UnknownChannel", f"Unsupported channel {channel}", 422)

        return {
            "native_id": self.target.native_id,
            "daily_budget_usd": str(daily_budget_usd),
            "updated": True,
        }
