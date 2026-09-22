"""Meta paused campaign construction with journaled writes and independent reads."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field, HttpUrl, model_validator

from adjutant.adapters.campaign_control import numeric
from adjutant.adapters.errors import ProviderRejection
from adjutant.errors import DomainError
from adjutant.models import Input

GRAPH = "https://graph.facebook.com/v26.0"


class MetaBuildSettings(Input):
    page_id: str = Field(pattern=r"^[0-9]+$")
    destination_url: HttpUrl
    countries: list[str] = Field(min_length=1, max_length=50)
    age_min: int = Field(default=18, ge=18, le=65)
    age_max: int = Field(default=65, ge=18, le=65)
    pixel_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    conversion_event: str = Field(default="LEAD", pattern=r"^(LEAD|PURCHASE)$")
    end_time: datetime
    beneficiary: str = Field(default="", max_length=200)
    payer: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_targeting(self):
        self.countries = sorted(set(country.strip().upper() for country in self.countries))
        if any(
            len(country) != 2 or not country.isascii() or not country.isalpha()
            for country in self.countries
        ):
            raise ValueError("Countries must use two-letter ISO country codes")
        if self.age_max < self.age_min:
            raise ValueError("Maximum age cannot be below minimum age")
        if self.end_time.tzinfo is None:
            raise ValueError("Campaign end time must include a timezone")
        return self


def load_placement_spec_limits(
    conn: Any, channel: str = "meta", placement_key: str = "meta.facebook_feed.square"
) -> dict[str, int]:
    """S4.4: Load placement text limits dynamically from the placement_spec data registry."""
    row = conn.execute(
        "SELECT text_limits FROM placement_spec WHERE channel=%s AND placement_key=%s",
        (channel, placement_key),
    ).fetchone()
    if row and row.get("text_limits"):
        limits = row["text_limits"]
        return {
            "headline": int(limits.get("headline", 40)),
            "primary_text": int(limits.get("primary", limits.get("primary_text", 125))),
            "description": int(limits.get("description", 30)),
        }
    return {"headline": 40, "primary_text": 125, "description": 30}


def preflight(document: dict, text_limits: dict[str, int] | None = None) -> list[str]:
    """Validate the supported construction path without making a remote mutation.

    S4.3: Returns specific violation messages naming the field, character count, and allowed limit.
    S4.4: Uses dynamic limits passed from the capability/placement data registry.
    """
    settings = MetaBuildSettings.model_validate(document["settings"])
    failures = []
    if document["objective"] not in {"awareness", "traffic", "leads", "sales"}:
        failures.append("This Meta image campaign requires awareness, traffic, leads, or sales.")
    if document["objective"] in {"leads", "sales"} and not settings.pixel_id:
        failures.append("Website conversion campaigns require an authorized Meta Pixel ID.")
    if document["objective"] == "sales" and settings.conversion_event != "PURCHASE":
        failures.append("Sales campaigns require the Purchase conversion event.")
    if document["objective"] == "leads" and settings.conversion_event != "LEAD":
        failures.append("Lead campaigns require the Lead conversion event.")
    if not document["creatives"]:
        failures.append("Attach at least one validated square image creative to the plan.")
    limits = text_limits or {"headline": 40, "primary_text": 125, "description": 30}
    for creative in document["creatives"]:
        copy = creative["copy"].get("meta", {})
        for field, maximum in limits.items():
            val = copy.get(field)
            if not isinstance(val, str) or len(val) == 0:
                failures.append(f"Creative {creative['id']} is missing required Meta field '{field}'.")
            elif len(val) > maximum:
                failures.append(
                    f"Creative {creative['id']} field '{field}' length {len(val)} exceeds allowed limit of {maximum} characters."
                )
    return failures


class MetaBuilder:
    """Every create is preceded by a committed journal entry supplied by the worker."""

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
        self.account = numeric(account_id.removeprefix("act_"))
        self.token = token["access_token"]
        self.begin = begin
        self.finish = finish
        self.load_image = load_image
        self.checkpoint = checkpoint

    async def request(self, method: str, path: str, **kwargs: Any) -> dict:
        await asyncio.to_thread(self.checkpoint)
        try:
            response = await self.client.request(
                method,
                f"{GRAPH}/{path}",
                headers={"Authorization": f"Bearer {self.token}"},
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise DomainError(
                "ProviderStateUncertain",
                "Meta did not respond. Reconciliation is required before another create.",
                503,
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise DomainError(
                "ProviderResponseInvalid",
                "Meta returned invalid JSON; remote state is unverified.",
                502,
            ) from exc
        if not isinstance(body, dict):
            raise DomainError(
                "ProviderResponseInvalid", "Meta returned an invalid response shape.", 502
            )
        error = body.get("error")
        if error or not 200 <= response.status_code < 300:
            detail = error if isinstance(error, dict) else {}
            message = str(
                detail.get("message")
                or detail.get("error_user_msg")
                or f"HTTP {response.status_code}"
            )
            message = message.replace(self.token, "[redacted]")
            code = (
                "PlatformAuthorization"
                if response.status_code in {401, 403} or detail.get("code") == 190
                else "PlatformRequestRejected"
            )
            raise ProviderRejection(
                code, str(detail.get("code", response.status_code)), message, "Meta"
            )
        return body

    async def collection(self, edge: str, fields: str, **params: Any) -> list[dict]:
        """Follow opaque cursors on the same endpoint; never follow a provider-supplied URL."""
        rows = []
        cursors: set[str] = set()
        for _ in range(100):
            body = await self.request(
                "GET",
                f"act_{self.account}/{edge}",
                params={"fields": fields, "limit": 100, **params},
            )
            batch = body.get("data")
            if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                raise DomainError(
                    "ProviderResponseInvalid", "Meta returned an invalid object list.", 502
                )
            rows.extend(batch)
            paging = body.get("paging", {})
            if not paging.get("next"):
                return rows
            cursor = paging.get("cursors", {}).get("after")
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise DomainError(
                    "ProviderResponseInvalid", "Meta pagination cannot be reconciled safely.", 502
                )
            cursors.add(cursor)
            params["after"] = cursor
        raise DomainError(
            "ProviderInventoryLimit", "Meta inventory exceeded the reconciliation page limit.", 409
        )

    async def create(self, key: str, edge: str, payload: dict, fields: str) -> dict:
        step = await asyncio.to_thread(self.begin, key, payload)
        identity = step.get("native_id")
        if not identity and not step["fresh"]:
            matches = [
                row
                for row in await self.collection(edge, "id,name")
                if row.get("name") == payload["name"]
            ]
            if len(matches) != 1:
                raise DomainError(
                    "ProviderStateUncertain",
                    "A previous Meta create has no unique remote match. The "
                    "request will not be repeated.",
                    409,
                )
            identity = str(matches[0]["id"])
        if not identity:
            encoded = {
                name: json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
                for name, value in payload.items()
            }
            result = await self.request("POST", f"act_{self.account}/{edge}", data=encoded)
            identity = str(result.get("id", ""))
            numeric(identity)
            await asyncio.to_thread(self.finish, key, identity, {"create_response": result})
        row = await self.request("GET", numeric(identity), params={"fields": fields})
        if (
            str(row.get("id")) != identity
            or str(row.get("account_id")) != self.account
            or row.get("name") != payload["name"]
        ):
            raise DomainError(
                "RemoteIdentityMismatch",
                "Meta returned an object outside the requested account or operation.",
                409,
            )
        for field in (
            "status",
            "campaign_id",
            "adset_id",
            "objective",
            "daily_budget",
            "spend_cap",
        ):
            if field in payload and str(row.get(field)) != str(payload[field]):
                raise DomainError(
                    "RemoteVerificationFailed", f"Meta did not preserve the requested {field}.", 409
                )
        if "creative" in payload and str(row.get("creative", {}).get("id")) != str(
            payload["creative"]["creative_id"]
        ):
            raise DomainError(
                "RemoteVerificationFailed", "Meta associated a different creative with the ad.", 409
            )
        if "object_story_spec" in payload:
            expected = payload["object_story_spec"]
            observed = row.get("object_story_spec", {})
            if str(observed.get("page_id")) != str(expected["page_id"]) or any(
                observed.get("link_data", {}).get(field) != value
                for field, value in expected["link_data"].items()
            ):
                raise DomainError(
                    "RemoteVerificationFailed",
                    "Meta creative image, destination, or copy differs from the requested ad.",
                    409,
                )
        if "targeting" in payload:
            expected, observed = payload["targeting"], row.get("targeting", {})
            if any(observed.get(field) != value for field, value in expected.items()):
                raise DomainError(
                    "RemoteVerificationFailed",
                    "Meta targeting differs from the configured audience or placements.",
                    409,
                )
        await asyncio.to_thread(self.finish, key, identity, row)
        return row

    async def image(self, key: str, storage_key: str) -> str:
        content = await asyncio.to_thread(self.load_image, storage_key)
        filename = f"adjutant-{hashlib.sha256(content).hexdigest()}.png"
        step = await asyncio.to_thread(
            self.begin, key, {"asset_hash": storage_key, "name": filename}
        )
        identity = step.get("native_id")
        if not identity and not step["fresh"]:
            matches = await self.collection("adimages", "hash,name", name=filename)
            matches = [row for row in matches if row.get("name") == filename]
            if len(matches) != 1:
                raise DomainError(
                    "ProviderStateUncertain",
                    "The interrupted image upload has no unique remote match.",
                    409,
                )
            identity = str(matches[0]["hash"])
        if not identity:
            result = await self.request(
                "POST",
                f"act_{self.account}/adimages",
                files={"filename": (filename, content, "image/png")},
            )
            images = result.get("images", {})
            row = images.get(filename) if isinstance(images, dict) else None
            if not isinstance(row, dict) or not isinstance(row.get("hash"), str):
                raise DomainError(
                    "ProviderResponseInvalid",
                    "Meta did not return the uploaded image identity.",
                    502,
                )
            identity = row["hash"]
            await asyncio.to_thread(self.finish, key, identity, {"create_response": row})
        images = await self.collection(
            "adimages", "hash,name,width,height", hashes=json.dumps([identity])
        )
        if len(images) != 1 or images[0].get("hash") != identity:
            raise DomainError(
                "RemoteVerificationFailed", "The uploaded Meta image could not be read back.", 409
            )
        await asyncio.to_thread(self.finish, key, identity, images[0])
        return identity

    async def build(self, document: dict, idem_key: str) -> list[dict]:
        violations = preflight(document)
        if violations:
            raise DomainError("DeploymentInvalid", " ".join(violations), 422)
        settings = MetaBuildSettings.model_validate(document["settings"])
        account = await self.request(
            "GET", f"act_{self.account}", params={"fields": "account_id,account_status,currency"}
        )
        if str(account.get("account_id")) != self.account or account.get("account_status") != 1:
            raise DomainError(
                "AccountUnavailable", "Meta account is not authorized for advertising.", 409
            )
        if account.get("currency") != "USD":
            raise DomainError(
                "CurrencyMismatch", "This USD plan requires a USD advertising account.", 409
            )
        objective, optimization = {
            "awareness": ("OUTCOME_AWARENESS", "REACH"),
            "traffic": ("OUTCOME_TRAFFIC", "LINK_CLICKS"),
            "leads": ("OUTCOME_LEADS", "OFFSITE_CONVERSIONS"),
            "sales": ("OUTCOME_SALES", "OFFSITE_CONVERSIONS"),
        }[document["objective"]]
        prefix = f"Adjutant {idem_key}"
        campaign = await self.create(
            "campaign",
            "campaigns",
            {
                "name": f"{prefix} campaign",
                "objective": objective,
                "status": "PAUSED",
                "special_ad_categories": [],
                "buying_type": "AUCTION",
                "is_adset_budget_sharing_enabled": False,
                "spend_cap": int(Decimal(document["monthly_budget_usd"]) * 100),
            },
            "id,account_id,name,status,objective,spend_cap",
        )
        targeting = {
            "geo_locations": {"countries": settings.countries},
            "age_min": settings.age_min,
            "age_max": settings.age_max,
            "publisher_platforms": ["facebook"],
            "facebook_positions": ["feed"],
        }
        adset_payload = {
            "name": f"{prefix} ad set",
            "campaign_id": campaign["id"],
            "status": "PAUSED",
            "daily_budget": int(Decimal(document["daily_budget_usd"]) * 100),
            "billing_event": "IMPRESSIONS",
            "optimization_goal": optimization,
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "targeting": targeting,
            "end_time": settings.end_time.isoformat(),
        }
        if settings.pixel_id:
            adset_payload["promoted_object"] = {
                "pixel_id": settings.pixel_id,
                "custom_event_type": settings.conversion_event,
            }
        if settings.beneficiary:
            adset_payload["dsa_beneficiary"] = settings.beneficiary
        if settings.payer:
            adset_payload["dsa_payor"] = settings.payer
        adset = await self.create(
            "ad_group",
            "adsets",
            adset_payload,
            "id,account_id,name,status,campaign_id,daily_budget,targeting",
        )
        objects = [
            {
                "level": "campaign",
                "key": "campaign",
                "remote": campaign,
                "parent_key": None,
                "creative_id": None,
            },
            {
                "level": "ad_group",
                "key": "ad_group",
                "remote": adset,
                "parent_key": "campaign",
                "creative_id": None,
            },
        ]
        for item in document["creatives"]:
            key = item["id"]
            image_hash = await self.image(f"image:{key}", item["storage_key"])
            copy = item["copy"]["meta"]
            creative = await self.create(
                f"creative:{key}",
                "adcreatives",
                {
                    "name": f"{prefix} creative {key}",
                    "object_story_spec": {
                        "page_id": settings.page_id,
                        "link_data": {
                            "image_hash": image_hash,
                            "link": str(settings.destination_url),
                            "message": copy["primary_text"],
                            "name": copy["headline"],
                            "description": copy["description"],
                        },
                    },
                },
                "id,account_id,name,object_story_spec",
            )
            ad = await self.create(
                f"ad:{key}",
                "ads",
                {
                    "name": f"{prefix} ad {key}",
                    "adset_id": adset["id"],
                    "creative": {"creative_id": creative["id"]},
                    "status": "PAUSED",
                },
                "id,account_id,name,status,adset_id,creative",
            )
            objects.append(
                {
                    "level": "ad",
                    "key": f"ad:{key}",
                    "remote": ad,
                    "parent_key": "ad_group",
                    "creative_id": key,
                }
            )
        return objects
