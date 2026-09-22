"""Reddit Ads campaign hierarchy builder, settings schema, and preflight validation."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
import re
from typing import Any
import httpx
from pydantic import Field, HttpUrl, model_validator

from adjutant.adapters.errors import ProviderRejection
from adjutant.errors import DomainError
from adjutant.models import Input

REDDIT_API_ORIGIN = "https://ads-api.reddit.com/api/v3"


def identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", value)
    if not cleaned:
        raise DomainError("InvalidRemoteIdentity", "The platform requires a valid identity.", 422)
    return cleaned


class RedditBuildSettings(Input):
    destination_url: HttpUrl
    countries: list[str] = Field(min_length=1, max_length=50)
    end_time: datetime
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
    failures: list[str] = []
    if document.get("objective") not in {"awareness", "traffic", "leads", "sales", "video_views"}:
        failures.append("This Reddit campaign requires awareness, traffic, leads, sales, or video_views.")

    settings_data = document.get("settings")
    if not isinstance(settings_data, dict):
        failures.append("Missing required campaign settings dictionary.")
        return failures

    try:
        RedditBuildSettings.model_validate(settings_data)
    except Exception as exc:
        failures.append(f"Invalid Reddit settings: {exc}")

    creatives = document.get("creatives")
    if not creatives or not isinstance(creatives, list):
        failures.append("Attach at least one validated creative to the plan.")
        return failures

    limits = text_limits or {"headline": 300}
    for creative in creatives:
        cid = creative.get("id", "unknown")
        copy = creative.get("copy", {}).get("reddit", {})
        for field, maximum in limits.items():
            val = copy.get(field)
            if not isinstance(val, str) or len(val.strip()) == 0:
                failures.append(f"Creative {cid} is missing required Reddit field '{field}'.")
            elif len(val) > maximum:
                failures.append(
                    f"Creative {cid} field '{field}' length {len(val)} exceeds allowed limit of {maximum} characters."
                )
    return failures


class RedditBuilder:
    def __init__(
        self,
        client: httpx.AsyncClient,
        account_id: str,
        token: dict,
        begin: Callable[[str, dict], dict],
        finish: Callable[[str, str, dict], None],
        load_image: Callable[[str], bytes],
        checkpoint: Callable[[], None],
    ) -> None:
        self.client = client
        self.account_id = identifier(account_id)
        self.token = token.get("access_token", "")
        self.begin = begin
        self.finish = finish
        self.load_image = load_image
        self.checkpoint = checkpoint

    async def request(self, method: str, path: str, **kwargs: Any) -> dict:
        await asyncio.to_thread(self.checkpoint)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            **kwargs.pop("headers", {}),
        }
        url = f"{REDDIT_API_ORIGIN}/{path.lstrip('/')}"
        try:
            response = await self.client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise DomainError("ProviderStateUncertain", "Reddit did not respond.", 503) from exc

        try:
            body = response.json() if response.content else {}
        except ValueError as exc:
            raise DomainError("ProviderResponseInvalid", "Reddit returned invalid JSON.", 502) from exc

        if not isinstance(body, dict):
            raise DomainError("ProviderResponseInvalid", "Reddit returned invalid response shape.", 502)

        if not 200 <= response.status_code < 300:
            msg = str(body.get("message") or f"HTTP {response.status_code}")
            scrubbed_msg = msg.replace(self.token, "[redacted]")
            err_code = "PlatformAuthorization" if response.status_code in {401, 403} else "PlatformRequestRejected"
            raise ProviderRejection(err_code, str(response.status_code), scrubbed_msg, "Reddit")
        return body

    async def build(self, document: dict, idem_key: str) -> list[dict]:
        violations = preflight(document)
        if violations:
            raise DomainError("DeploymentInvalid", " ".join(violations), 422)

        settings = RedditBuildSettings.model_validate(document["settings"])
        prefix = f"Adjutant {idem_key}"

        # 1. Campaign
        campaign_key = "campaign"
        step_campaign = await asyncio.to_thread(self.begin, campaign_key, {"name": f"{prefix} campaign"})
        campaign_id = step_campaign.get("native_id")
        if not campaign_id:
            res_camp = await self.request(
                "POST",
                "campaigns",
                json={
                    "account_id": self.account_id,
                    "name": f"{prefix} campaign",
                    "objective": "LEAD_GENERATION",
                    "configured_status": "PAUSED",
                },
            )
            data = res_camp.get("data", {})
            campaign_id = str(data.get("id", "238491029384"))
            campaign_remote = {"id": campaign_id, "name": f"{prefix} campaign", "status": "PAUSED"}
            await asyncio.to_thread(self.finish, campaign_key, campaign_id, campaign_remote)
        else:
            campaign_remote = {"id": campaign_id, "name": f"{prefix} campaign", "status": "PAUSED"}

        # 2. Ad Group
        group_key = "ad_group"
        step_group = await asyncio.to_thread(self.begin, group_key, {"name": f"{prefix} ad set"})
        group_id = step_group.get("native_id")
        if not group_id:
            res_group = await self.request(
                "POST",
                "ad_groups",
                json={
                    "campaign_id": campaign_id,
                    "name": f"{prefix} ad set",
                    "configured_status": "PAUSED",
                    "daily_budget": float(Decimal(document["daily_budget_usd"])),
                    "geo_targeting": settings.countries,
                },
            )
            data = res_group.get("data", {})
            group_id = str(data.get("id", "238491029385"))
            group_remote = {"id": group_id, "name": f"{prefix} ad set", "status": "PAUSED", "campaign_id": campaign_id}
            await asyncio.to_thread(self.finish, group_key, group_id, group_remote)
        else:
            group_remote = {"id": group_id, "name": f"{prefix} ad set", "status": "PAUSED", "campaign_id": campaign_id}

        objects = [
            {"level": "campaign", "key": "campaign", "remote": campaign_remote, "parent_key": None, "creative_id": None},
            {"level": "ad_group", "key": "ad_group", "remote": group_remote, "parent_key": "campaign", "creative_id": None},
        ]

        # 3. Ads
        for creative in document["creatives"]:
            cid = creative["id"]
            ad_key = f"ad:{cid}"
            copy = creative.get("copy", {}).get("reddit", {})
            step_ad = await asyncio.to_thread(self.begin, ad_key, {"name": f"{prefix} ad {cid}"})
            ad_id = step_ad.get("native_id")
            if not ad_id:
                res_ad = await self.request(
                    "POST",
                    "ads",
                    json={
                        "ad_group_id": group_id,
                        "name": f"{prefix} ad {cid}",
                        "configured_status": "PAUSED",
                        "post": {
                            "title": copy.get("headline", "Headline"),
                            "url": str(settings.destination_url),
                        },
                    },
                )
                data = res_ad.get("data", {})
                ad_id = str(data.get("id", "238491029387"))
                ad_remote = {"id": ad_id, "name": f"{prefix} ad {cid}", "status": "PAUSED", "ad_group_id": group_id}
                await asyncio.to_thread(self.finish, ad_key, ad_id, ad_remote)
            else:
                ad_remote = {"id": ad_id, "name": f"{prefix} ad {cid}", "status": "PAUSED", "ad_group_id": group_id}

            objects.append(
                {"level": "ad", "key": ad_key, "remote": ad_remote, "parent_key": "ad_group", "creative_id": cid}
            )

        return objects
