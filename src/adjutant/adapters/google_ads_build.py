"""Google Ads campaign hierarchy builder, settings schema, and preflight validation."""

import asyncio
import base64
import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field, HttpUrl, model_validator

from adjutant.adapters.errors import ProviderRejection
from adjutant.errors import DomainError
from adjutant.models import Input

GOOGLE_ADS_API_ORIGIN = "https://googleads.googleapis.com/v25"


def numeric(value: str) -> str:
    cleaned = re.sub(r"[^0-9]", "", value)
    if not cleaned:
        raise DomainError("InvalidRemoteIdentity", "The platform requires a numeric identity.", 422)
    return cleaned


class GoogleAdsBuildSettings(Input):
    destination_url: HttpUrl
    countries: list[str] = Field(min_length=1, max_length=50)
    end_time: datetime
    conversion_goal: str = Field(default="LEAD", pattern=r"^(LEAD|PURCHASE|PAGE_VIEW|SIGNUP)$")
    bidding_strategy: str = Field(
        default="MAXIMIZE_CONVERSIONS",
        pattern=r"^(MAXIMIZE_CONVERSIONS|TARGET_CPA|MANUAL_CPC)$",
    )
    target_cpa_usd: Decimal | None = Field(default=None, decimal_places=2, ge=Decimal("0.01"))
    page_id: str = Field(default="", max_length=100)
    pixel_id: str | None = Field(default=None)
    conversion_event: str | None = Field(default=None)

    @model_validator(mode="after")
    def validate_settings(self):
        self.countries = sorted(set(country.strip().upper() for country in self.countries))
        if any(
            len(country) != 2 or not country.isascii() or not country.isalpha()
            for country in self.countries
        ):
            raise ValueError("Countries must use two-letter ISO country codes")
        if self.end_time.tzinfo is None:
            raise ValueError("Campaign end time must include a timezone")
        return self


def preflight(document: dict, text_limits: dict[str, int] | None = None) -> list[str]:
    """Validate Google Ads campaign requirements before remote writes.

    - Validates objective compatibility.
    - Validates settings schema.
    - Validates creative presence.
    - Returns specific violation messages naming the field, character count, and allowed limit.
    """
    failures: list[str] = []
    if document.get("objective") not in {"awareness", "traffic", "leads", "sales"}:
        failures.append("This Google Ads campaign requires awareness, traffic, leads, or sales.")

    settings_data = document.get("settings")
    if not isinstance(settings_data, dict):
        failures.append("Missing required campaign settings dictionary.")
        return failures

    try:
        GoogleAdsBuildSettings.model_validate(settings_data)
    except Exception as exc:
        failures.append(f"Invalid Google Ads settings: {exc}")

    creatives = document.get("creatives")
    if not creatives or not isinstance(creatives, list):
        failures.append("Attach at least one validated creative to the plan.")
        return failures

    limits = text_limits or {"headline": 30, "description": 90}
    for creative in creatives:
        cid = creative.get("id", "unknown")
        copy = creative.get("copy", {}).get("google_ads", {})
        for field, maximum in limits.items():
            val = copy.get(field)
            if not isinstance(val, str) or len(val.strip()) == 0:
                failures.append(f"Creative {cid} is missing required Google Ads field '{field}'.")
            elif len(val) > maximum:
                failures.append(
                    f"Creative {cid} field '{field}' length {len(val)} "
                    f"exceeds allowed limit of {maximum} characters."
                )
    return failures


class GoogleAdsBuilder:
    """Builds Google Ads campaign hierarchies with journaled idempotency."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        account_id: str,
        token: dict,
        begin: Callable[[str, dict], dict],
        finish: Callable[[str, str, dict], None],
        load_image: Callable[[str], bytes],
        checkpoint: Callable[[], None],
        developer_token: str = "devtok",
    ) -> None:
        self.client = client
        self.customer_id = numeric(account_id)
        self.token = token.get("access_token", "")
        self.developer_token = developer_token
        self.begin = begin
        self.finish = finish
        self.load_image = load_image
        self.checkpoint = checkpoint

    async def request(self, method: str, path: str, **kwargs: Any) -> dict:
        await asyncio.to_thread(self.checkpoint)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "developer-token": self.developer_token,
            "Content-Type": "application/json",
            **kwargs.pop("headers", {}),
        }
        url = f"{GOOGLE_ADS_API_ORIGIN}/customers/{self.customer_id}/{path.lstrip('/')}"
        try:
            response = await self.client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise DomainError(
                "ProviderStateUncertain",
                "Google Ads did not respond. Reconciliation is required before another create.",
                503,
            ) from exc

        try:
            body = response.json() if response.content else {}
        except ValueError as exc:
            raise DomainError(
                "ProviderResponseInvalid", "Google Ads returned invalid JSON.", 502
            ) from exc

        if not isinstance(body, dict):
            raise DomainError(
                "ProviderResponseInvalid",
                "Google Ads returned an invalid response shape.",
                502,
            )

        error = body.get("error")
        if error or not 200 <= response.status_code < 300:
            detail = error if isinstance(error, dict) else {}
            raw_msg = str(detail.get("message") or f"HTTP {response.status_code}")
            scrubbed_msg = raw_msg.replace(self.token, "[redacted]")
            code = (
                "PlatformAuthorization"
                if response.status_code in {401, 403}
                else "PlatformRequestRejected"
            )
            raise ProviderRejection(
                code,
                str(detail.get("code", response.status_code)),
                scrubbed_msg,
                "GoogleAds",
            )
        return body

    async def image_asset(self, key: str, storage_key: str) -> str:
        content = await asyncio.to_thread(self.load_image, storage_key)
        asset_name = f"adjutant-asset-{hashlib.sha256(content).hexdigest()[:16]}"
        step = await asyncio.to_thread(
            self.begin, key, {"asset_hash": storage_key, "name": asset_name}
        )
        identity = step.get("native_id")
        if identity:
            return str(identity)

        b64_content = base64.b64encode(content).decode("ascii")
        res = await self.request(
            "POST",
            "assets:mutate",
            json={
                "operations": [
                    {
                        "create": {
                            "name": asset_name,
                            "type": "IMAGE",
                            "imageAsset": {
                                "data": b64_content,
                                "fileSize": len(content),
                            },
                        }
                    }
                ]
            },
        )
        results = res.get("results", [])
        if not results or not results[0].get("resourceName"):
            raise DomainError(
                "ProviderResponseInvalid",
                "Google Ads did not return asset resourceName.",
                502,
            )
        resource_name = results[0]["resourceName"]
        asset_id = resource_name.split("/")[-1]
        await asyncio.to_thread(self.finish, key, asset_id, {"resource_name": resource_name})
        return str(asset_id)

    async def build(self, document: dict, idem_key: str) -> list[dict]:
        violations = preflight(document)
        if violations:
            raise DomainError("DeploymentInvalid", " ".join(violations), 422)

        settings = GoogleAdsBuildSettings.model_validate(document["settings"])

        # Verify customer account status
        acc_data = await self.request(
            "POST",
            "googleAds:search",
            json={
                "query": (
                    "SELECT customer.id, customer.status, customer.currency_code "
                    "FROM customer LIMIT 1"
                )
            },
        )
        acc_results = acc_data.get("results", [])
        if not acc_results:
            raise DomainError(
                "AccountUnavailable",
                "Google Ads account information is unavailable.",
                409,
            )
        customer = acc_results[0].get("customer", {})
        if customer.get("status") not in {"ENABLED", "1"}:
            raise DomainError("AccountUnavailable", "Google Ads account is not enabled.", 409)
        if customer.get("currencyCode") != "USD":
            raise DomainError(
                "CurrencyMismatch",
                "This USD plan requires a USD Google Ads account.",
                409,
            )

        prefix = f"Adjutant {idem_key}"

        # 1. Campaign Budget
        budget_key = "campaign_budget"
        step_budget = await asyncio.to_thread(self.begin, budget_key, {"name": f"{prefix} budget"})
        budget_id = step_budget.get("native_id")
        if not budget_id:
            daily_amount_micros = int(Decimal(document["daily_budget_usd"]) * Decimal("1000000"))
            res_budget = await self.request(
                "POST",
                "campaignBudgets:mutate",
                json={
                    "operations": [
                        {
                            "create": {
                                "name": f"{prefix} budget",
                                "amountMicros": str(daily_amount_micros),
                                "deliveryMethod": "STANDARD",
                                "explicitlyShared": False,
                            }
                        }
                    ]
                },
            )
            b_results = res_budget.get("results", [])
            if not b_results or not b_results[0].get("resourceName"):
                raise DomainError(
                    "ProviderResponseInvalid", "Google Ads budget creation failed.", 502
                )
            budget_res_name = b_results[0]["resourceName"]
            budget_id = budget_res_name.split("/")[-1]
            await asyncio.to_thread(
                self.finish, budget_key, budget_id, {"resource_name": budget_res_name}
            )
        else:
            budget_res_name = f"customers/{self.customer_id}/campaignBudgets/{budget_id}"

        # 2. Campaign
        campaign_key = "campaign"
        step_campaign = await asyncio.to_thread(
            self.begin, campaign_key, {"name": f"{prefix} campaign"}
        )
        campaign_id = step_campaign.get("native_id")
        if not campaign_id:
            res_camp = await self.request(
                "POST",
                "campaigns:mutate",
                json={
                    "operations": [
                        {
                            "create": {
                                "name": f"{prefix} campaign",
                                "status": "PAUSED",
                                "advertisingChannelType": "SEARCH",
                                "campaignBudget": budget_res_name,
                                "networkSettings": {
                                    "targetGoogleSearch": True,
                                    "targetSearchNetwork": True,
                                },
                                "endDate": settings.end_time.strftime("%Y-%m-%d"),
                            }
                        }
                    ]
                },
            )
            c_results = res_camp.get("results", [])
            if not c_results or not c_results[0].get("resourceName"):
                raise DomainError(
                    "ProviderResponseInvalid",
                    "Google Ads campaign creation failed.",
                    502,
                )
            camp_res_name = c_results[0]["resourceName"]
            campaign_id = camp_res_name.split("/")[-1]
            campaign_remote = {
                "id": str(campaign_id),
                "resource_name": camp_res_name,
                "name": f"{prefix} campaign",
                "status": "PAUSED",
            }
            await asyncio.to_thread(self.finish, campaign_key, str(campaign_id), campaign_remote)
        else:
            camp_res_name = f"customers/{self.customer_id}/campaigns/{campaign_id}"
            campaign_remote = {
                "id": str(campaign_id),
                "resource_name": camp_res_name,
                "name": f"{prefix} campaign",
                "status": "PAUSED",
            }

        # 3. Ad Group
        group_key = "ad_group"
        step_group = await asyncio.to_thread(self.begin, group_key, {"name": f"{prefix} ad set"})
        group_id = step_group.get("native_id")
        if not group_id:
            res_group = await self.request(
                "POST",
                "adGroups:mutate",
                json={
                    "operations": [
                        {
                            "create": {
                                "name": f"{prefix} ad set",
                                "campaign": camp_res_name,
                                "status": "PAUSED",
                                "type": "SEARCH_STANDARD",
                            }
                        }
                    ]
                },
            )
            g_results = res_group.get("results", [])
            if not g_results or not g_results[0].get("resourceName"):
                raise DomainError(
                    "ProviderResponseInvalid",
                    "Google Ads ad group creation failed.",
                    502,
                )
            group_res_name = g_results[0]["resourceName"]
            group_id = group_res_name.split("/")[-1]
            group_remote = {
                "id": str(group_id),
                "resource_name": group_res_name,
                "name": f"{prefix} ad set",
                "status": "PAUSED",
                "campaign_id": str(campaign_id),
            }
            await asyncio.to_thread(self.finish, group_key, str(group_id), group_remote)
        else:
            group_res_name = f"customers/{self.customer_id}/adGroups/{group_id}"
            group_remote = {
                "id": str(group_id),
                "resource_name": group_res_name,
                "name": f"{prefix} ad set",
                "status": "PAUSED",
                "campaign_id": str(campaign_id),
            }

        objects: list[dict[str, Any]] = [
            {
                "level": "campaign",
                "key": "campaign",
                "remote": campaign_remote,
                "parent_key": None,
                "creative_id": None,
            },
            {
                "level": "ad_group",
                "key": "ad_group",
                "remote": group_remote,
                "parent_key": "campaign",
                "creative_id": None,
            },
        ]

        # 4. Creatives & Ads
        for creative in document["creatives"]:
            cid = creative["id"]
            ad_key = f"ad:{cid}"
            copy = creative.get("copy", {}).get("google_ads", {})
            step_ad = await asyncio.to_thread(self.begin, ad_key, {"name": f"{prefix} ad {cid}"})
            ad_id = step_ad.get("native_id")
            if not ad_id:
                # Upload image asset if provided
                if creative.get("storage_key"):
                    await self.image_asset(f"image:{cid}", creative["storage_key"])

                res_ad = await self.request(
                    "POST",
                    "adGroupAds:mutate",
                    json={
                        "operations": [
                            {
                                "create": {
                                    "adGroup": group_res_name,
                                    "status": "PAUSED",
                                    "ad": {
                                        "name": f"{prefix} ad {cid}",
                                        "finalUrls": [str(settings.destination_url)],
                                        "responsiveSearchAd": {
                                            "headlines": [
                                                {"text": copy.get("headline", "Headline")}
                                            ],
                                            "descriptions": [
                                                {"text": copy.get("description", "Description")}
                                            ],
                                        },
                                    },
                                }
                            }
                        ]
                    },
                )
                a_results = res_ad.get("results", [])
                if not a_results or not a_results[0].get("resourceName"):
                    raise DomainError(
                        "ProviderResponseInvalid", "Google Ads ad creation failed.", 502
                    )
                ad_res_name = a_results[0]["resourceName"]
                ad_id = (
                    ad_res_name.split("~")[-1] if "~" in ad_res_name else ad_res_name.split("/")[-1]
                )
                ad_remote = {
                    "id": str(ad_id),
                    "resource_name": ad_res_name,
                    "name": f"{prefix} ad {cid}",
                    "status": "PAUSED",
                    "ad_group_id": str(group_id),
                }
                await asyncio.to_thread(self.finish, ad_key, str(ad_id), ad_remote)
            else:
                ad_remote = {
                    "id": str(ad_id),
                    "resource_name": f"{group_res_name}~{ad_id}",
                    "name": f"{prefix} ad {cid}",
                    "status": "PAUSED",
                    "ad_group_id": str(group_id),
                }

            objects.append(
                {
                    "level": "ad",
                    "key": ad_key,
                    "remote": ad_remote,
                    "parent_key": "ad_group",
                    "creative_id": cid,
                }
            )

        return objects
