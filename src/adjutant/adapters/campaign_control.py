"""Read, pause, and independently verify advertising campaigns through provider APIs."""

import asyncio
import json
import re
from dataclasses import dataclass
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
        elif target.channel == "microsoft":
            numeric(self.account)
            numeric(self.identity)
            self.headers.update(
                DeveloperToken=app["developer_token"], CustomerAccountId=self.account
            )
            self.headers["CustomerId"] = numeric(str(target.metadata["customer_id"]))
        elif target.channel == "tiktok":
            self.headers = {"Access-Token": token["access_token"]}
        elif target.channel == "linkedin":
            self.headers.update(
                {"LinkedIn-Version": "202608", "X-Restli-Protocol-Version": "2.0.0"}
            )
        elif target.channel == "amazon_ads":
            self.headers.update(
                {
                    "Amazon-Advertising-API-ClientId": app["client_id"],
                    "Amazon-Advertising-API-Scope": self.account,
                }
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

    def amazon_endpoint(self) -> tuple[str, str]:
        origins = {
            "NA": "https://advertising-api.amazon.com",
            "EU": "https://advertising-api-eu.amazon.com",
            "FE": "https://advertising-api-fe.amazon.com",
        }
        family = self.target.metadata.get("ad_product")
        versions = {
            "SPONSORED_PRODUCTS": (
                "/sp/campaigns",
                "application/vnd.spCampaign.v3+json",
            ),
            "SPONSORED_BRANDS": (
                "/sb/v4/campaigns",
                "application/vnd.sbcampaignresource.v4+json",
            ),
        }
        if family not in versions:
            raise DomainError(
                "CampaignProductRequired",
                "Record the campaign's Sponsored Products or Sponsored Brands product before "
                "controlling it.",
                422,
            )
        path, media = versions[family]
        return origins[self.app["region"]] + path, media

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
            elif channel == "tiktok":
                data = await self.request(
                    "GET",
                    "https://business-api.tiktok.com/open_api/v1.3/campaign/get/",
                    params={
                        "advertiser_id": account,
                        "filtering": json.dumps({"campaign_ids": [identity]}),
                        "page_size": 1,
                    },
                )
                row = data["data"]["list"][0]
                row = {**row, "id": row["campaign_id"]}
                status = row["operation_status"]
            elif channel == "linkedin":
                row = await self.request(
                    "GET",
                    f"https://api.linkedin.com/rest/adAccounts/{account}/adCampaigns/{identity}",
                )
                status = row["status"]
            elif channel == "microsoft":
                data = await self.request(
                    "POST",
                    "https://campaign.api.bingads.microsoft.com/CampaignManagement/v13/Campaigns/QueryByIds",
                    json={
                        "AccountId": int(account),
                        "CampaignIds": [int(identity)],
                        "CampaignType": "Search Shopping DynamicSearchAds Audience PerformanceMax",
                    },
                )
                value = data["Campaigns"][0]
                row = {**value, "id": value["Id"]}
                status = row["Status"]
            elif channel == "reddit":
                data = await self.request(
                    "GET", f"https://ads-api.reddit.com/api/v3/campaigns/{identity}"
                )
                row = data["data"]
                if str(row["account_id"]) != account:
                    raise ValueError("Wrong account")
                status = row["configured_status"]
            elif channel == "pinterest":
                row = await self.request(
                    "GET",
                    f"https://api.pinterest.com/v5/ad_accounts/{account}/campaigns/{identity}",
                )
                status = row["status"]
            elif channel == "snapchat":
                data = await self.request(
                    "GET", f"https://adsapi.snapchat.com/v1/campaigns/{identity}"
                )
                entry = data["campaigns"][0]
                if entry["sub_request_status"] != "SUCCESS":
                    raise ValueError("Subrequest failed")
                row = entry["campaign"]
                if str(row["ad_account_id"]) != account:
                    raise ValueError("Wrong account")
                status = row["status"]
            else:
                url, media = self.amazon_endpoint()
                data = await self.request(
                    "POST",
                    url + "/list",
                    headers={"Accept": media, "Content-Type": media},
                    json={"campaignIdFilter": {"include": [identity]}, "maxResults": 1},
                )
                row = data["campaigns"][0]
                row = {**row, "id": row["campaignId"]}
                status = row["state"]
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
        elif channel == "tiktok":
            await self.request(
                "POST",
                "https://business-api.tiktok.com/open_api/v1.3/campaign/status/update/",
                json={
                    "advertiser_id": account,
                    "campaign_ids": [identity],
                    "operation_status": "DISABLE",
                },
            )
        elif channel == "linkedin":
            await self.request(
                "POST",
                f"https://api.linkedin.com/rest/adAccounts/{account}/adCampaigns/{identity}",
                headers={"X-RestLi-Method": "PARTIAL_UPDATE"},
                json={"patch": {"$set": {"status": "PAUSED"}}},
            )
        elif channel == "microsoft":
            await self.request(
                "PUT",
                "https://campaign.api.bingads.microsoft.com/CampaignManagement/v13/Campaigns",
                json={
                    "AccountId": int(account),
                    "Campaigns": [{"Id": int(identity), "Status": "Paused"}],
                },
            )
        elif channel == "reddit":
            await self.request(
                "PATCH",
                f"https://ads-api.reddit.com/api/v3/campaigns/{identity}",
                json={"data": {"configured_status": "PAUSED"}},
            )
        elif channel == "pinterest":
            data = await self.request(
                "PATCH",
                f"https://api.pinterest.com/v5/ad_accounts/{account}/campaigns",
                json=[{"id": identity, "status": "PAUSED"}],
            )
            if any(item.get("exceptions") for item in data.get("items", [])):
                raise DomainError(
                    "PlatformRequestRejected",
                    "Pinterest rejected the campaign pause.",
                    502,
                )
        elif channel == "snapchat":
            await self.request(
                "PATCH",
                f"https://adsapi.snapchat.com/v1/adaccounts/{account}/campaigns/{identity}",
                headers={"Content-Type": "application/json-patch+json"},
                json=[{"op": "replace", "path": "/status", "value": "PAUSED"}],
            )
        else:
            url, media = self.amazon_endpoint()
            data = await self.request(
                "PUT",
                url,
                headers={"Accept": media, "Content-Type": media},
                json={"campaigns": [{"campaignId": identity, "state": "PAUSED"}]},
            )
            if data.get("campaigns", {}).get("error"):
                raise DomainError(
                    "PlatformRequestRejected",
                    "Amazon rejected the campaign pause.",
                    502,
                )
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
        elif channel == "tiktok":
            await self.request(
                "POST",
                "https://business-api.tiktok.com/open_api/v1.3/campaign/status/update/",
                json={
                    "advertiser_id": account,
                    "campaign_ids": [identity],
                    "operation_status": "ENABLE",
                },
            )
        elif channel == "linkedin":
            await self.request(
                "POST",
                f"https://api.linkedin.com/rest/adAccounts/{account}/adCampaigns/{identity}",
                headers={"X-RestLi-Method": "PARTIAL_UPDATE"},
                json={"patch": {"$set": {"status": "ACTIVE"}}},
            )
        elif channel == "microsoft":
            await self.request(
                "PUT",
                "https://campaign.api.bingads.microsoft.com/CampaignManagement/v13/Campaigns",
                json={
                    "AccountId": int(account),
                    "Campaigns": [{"Id": int(identity), "Status": "Active"}],
                },
            )
        elif channel == "reddit":
            await self.request(
                "PATCH",
                f"https://ads-api.reddit.com/api/v3/campaigns/{identity}",
                json={"data": {"configured_status": "ACTIVE"}},
            )
        elif channel == "pinterest":
            data = await self.request(
                "PATCH",
                f"https://api.pinterest.com/v5/ad_accounts/{account}/campaigns",
                json=[{"id": identity, "status": "ACTIVE"}],
            )
            if any(item.get("exceptions") for item in data.get("items", [])):
                raise DomainError(
                    "PlatformRequestRejected",
                    "Pinterest rejected the campaign resume.",
                    502,
                )
        elif channel == "snapchat":
            await self.request(
                "PATCH",
                f"https://adsapi.snapchat.com/v1/adaccounts/{account}/campaigns/{identity}",
                headers={"Content-Type": "application/json-patch+json"},
                json=[{"op": "replace", "path": "/status", "value": "ACTIVE"}],
            )
        else:
            url, media = self.amazon_endpoint()
            data = await self.request(
                "PUT",
                url,
                headers={"Accept": media, "Content-Type": media},
                json={"campaigns": [{"campaignId": identity, "state": "ENABLED"}]},
            )
            if data.get("campaigns", {}).get("error"):
                raise DomainError(
                    "PlatformRequestRejected",
                    "Amazon rejected the campaign resume.",
                    502,
                )
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
