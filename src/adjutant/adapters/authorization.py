"""Provider OAuth exchanges and remote advertising-account discovery."""

import base64
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from adjutant.errors import DomainError


@dataclass(frozen=True)
class OAuthProvider:
    channel: str
    authorization_url: str
    token_url: str
    scopes: str
    documentation: str
    extra_fields: tuple[str, ...] = ()


PROVIDERS = (
    OAuthProvider(
        "meta",
        "https://www.facebook.com/v26.0/dialog/oauth",
        "https://graph.facebook.com/v26.0/oauth/access_token",
        "ads_management,ads_read,pages_show_list,pages_read_engagement,pages_manage_ads,business_management",
        "https://developers.facebook.com/docs/marketing-api/get-started/authorization/",
    ),
    OAuthProvider(
        "google_ads",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://www.googleapis.com/auth/adwords",
        "https://developers.google.com/google-ads/api/docs/oauth/overview",
        ("developer_token",),
    ),
    OAuthProvider(
        "youtube",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://www.googleapis.com/auth/adwords https://www.googleapis.com/auth/youtube.upload",
        "https://developers.google.com/google-ads/api/docs/oauth/overview",
        ("developer_token",),
    ),
    OAuthProvider(
        "tiktok",
        "https://ads.tiktok.com/marketing_api/auth",
        "https://business-api.tiktok.com/open_api/v1.3/oauth2/access_token/",
        "",
        "https://business-api.tiktok.com/portal/docs?id=100025",
    ),
    OAuthProvider(
        "linkedin",
        "https://www.linkedin.com/oauth/v2/authorization",
        "https://www.linkedin.com/oauth/v2/accessToken",
        "r_ads rw_ads r_ads_reporting",
        "https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow",
    ),
    OAuthProvider(
        "microsoft",
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "https://ads.microsoft.com/msads.manage offline_access",
        "https://learn.microsoft.com/en-us/advertising/guides/authentication-oauth",
        ("developer_token",),
    ),
    OAuthProvider(
        "reddit",
        "https://www.reddit.com/api/v1/authorize",
        "https://www.reddit.com/api/v1/access_token",
        "adsread adsedit identity",
        "https://ads-api.reddit.com/docs/v3/guides/quick-start/",
    ),
    OAuthProvider(
        "pinterest",
        "https://www.pinterest.com/oauth/",
        "https://api.pinterest.com/v5/oauth/token",
        "ads:read,ads:write,boards:read,pins:read,pins:write",
        "https://developers.pinterest.com/docs/getting-started/set-up-authentication-and-authorization/",
    ),
    OAuthProvider(
        "snapchat",
        "https://accounts.snapchat.com/login/oauth2/authorize",
        "https://accounts.snapchat.com/login/oauth2/access_token",
        "snapchat-marketing-api",
        "https://developers.snap.com/marketing-api/Ads-API/authentication",
    ),
    OAuthProvider(
        "amazon_ads",
        "https://www.amazon.com/ap/oa",
        "https://api.amazon.com/auth/o2/token",
        "advertising::campaign_management",
        "https://advertising.amazon.com/API/docs/en-us/guides/onboarding/overview",
        ("region",),
    ),
)


def provider_for(channel: str) -> OAuthProvider:
    for provider in PROVIDERS:
        if provider.channel == channel:
            return provider
    raise DomainError("UnknownChannel", "Unknown advertising channel.", 422)


def authorization_url(provider: OAuthProvider, app: dict, redirect: str, state: str) -> str:
    params = {
        "client_id": app["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "state": state,
        "scope": provider.scopes,
    }
    if provider.channel in {"google_ads", "youtube"}:
        params.update(access_type="offline", prompt="consent")
    elif provider.channel == "reddit":
        params["duration"] = "permanent"
    elif provider.channel == "tiktok":
        params = {"app_id": app["client_id"], "redirect_uri": redirect, "state": state}
    return provider.authorization_url + "?" + urlencode(params)


def request_json(method: str, url: str, *, allow_empty: bool = False, **kwargs: Any) -> Any:
    """No automatic mutation retries or redirects; provider secrets never enter error messages."""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(30, connect=5),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.request(method, url, **kwargs)
        if response.status_code in {401, 403}:
            raise DomainError(
                "PlatformAuthorization",
                "The platform denied access. Reauthorize the account and check "
                "application permissions.",
                403,
            )
        if response.status_code == 429:
            raise DomainError(
                "PlatformRateLimited",
                "The platform rate limit was reached. Retry after the provider quota "
                "window resets.",
                429,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise DomainError(
                "PlatformRequestRejected",
                f"The platform returned HTTP {response.status_code}. "
                "Check the account's permissions and developer configuration.",
                502,
            )
        if allow_empty and not response.content:
            return {}
        data = response.json()
        if not isinstance(data, (dict, list)):
            raise ValueError("Unexpected JSON shape")
        if isinstance(data, dict) and (
            data.get("error")
            or data.get("Errors")
            or data.get("OperationErrors")
            or data.get("code", 0) not in (0, "0")
            or str(data.get("request_status", "success")).lower() == "error"
        ):
            raise DomainError(
                "PlatformRequestRejected",
                "The platform rejected the request. Check authorization, account "
                "access, and application settings.",
                502,
            )
        return data
    except httpx.HTTPError as exc:
        raise DomainError(
            "PlatformUnavailable", "The advertising platform could not be reached.", 503
        ) from exc
    except ValueError as exc:
        raise DomainError(
            "PlatformResponseInvalid",
            "The platform returned an unexpected response.",
            502,
        ) from exc


def exchange(
    provider: OAuthProvider,
    app: dict,
    redirect: str,
    *,
    code: str = "",
    previous: dict | None = None,
    timeout: float = 30,
) -> dict:
    refresh = not code
    form = {
        "client_id": app["client_id"],
        "client_secret": app["client_secret"],
        "redirect_uri": redirect,
        "grant_type": "refresh_token" if refresh else "authorization_code",
    }
    if refresh:
        if not previous or not previous.get("refresh_token"):
            raise DomainError(
                "ReauthorizationRequired",
                "This platform requires renewed user authorization.",
                403,
            )
        form["refresh_token"] = previous["refresh_token"]
    else:
        form["code"] = code
    headers = {"User-Agent": "Adjutant/2.0"}
    if provider.channel in {"reddit", "pinterest"}:
        credential = base64.b64encode(
            f"{app['client_id']}:{app['client_secret']}".encode()
        ).decode()
        headers["Authorization"] = "Basic " + credential
        del form["client_id"], form["client_secret"]
    if provider.channel == "pinterest":
        form["continuous_refresh"] = "true"
    if provider.channel == "microsoft":
        form["scope"] = provider.scopes
    if provider.channel == "tiktok":
        if refresh:
            raise DomainError(
                "ReauthorizationRequired", "Renew TikTok advertiser authorization.", 403
            )
        result = request_json(
            "POST",
            provider.token_url,
            json={
                "app_id": app["client_id"],
                "secret": app["client_secret"],
                "auth_code": code,
            },
            headers=headers,
            timeout=timeout,
        )
        data = result.get("data") if isinstance(result, dict) else None
    else:
        data = request_json("POST", provider.token_url, data=form, headers=headers, timeout=timeout)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("access_token"), str)
        or not data["access_token"]
    ):
        raise DomainError(
            "PlatformResponseInvalid",
            "The platform did not return an access token.",
            502,
        )
    token = {**(previous or {}), **data}
    try:
        token["expires_at"] = (
            time.time() + int(data["expires_in"]) if data.get("expires_in") else None
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise DomainError(
            "PlatformResponseInvalid",
            "The token expiry returned by the platform is invalid.",
            502,
        ) from exc
    return token


def revoke(provider: OAuthProvider, app: dict, token: dict) -> dict:
    """Use revocation APIs; otherwise return the provider's consent-removal page."""
    channel = provider.channel
    access = token["access_token"]
    credential = token.get("refresh_token") or access
    headers = {"User-Agent": "Adjutant/2.0"}
    if channel == "meta":
        result = request_json(
            "DELETE",
            "https://graph.facebook.com/v26.0/me/permissions",
            headers={**headers, "Authorization": "Bearer " + access},
        )
        if result.get("success") is not True:
            raise DomainError("RevocationUnverified", "Meta did not confirm revocation.", 502)
    elif channel in {"google_ads", "youtube"}:
        request_json(
            "POST",
            "https://oauth2.googleapis.com/revoke",
            data={"token": credential},
            headers=headers,
            allow_empty=True,
        )
    elif channel in {"reddit", "pinterest"}:
        basic = base64.b64encode(f"{app['client_id']}:{app['client_secret']}".encode()).decode()
        headers["Authorization"] = "Basic " + basic
        endpoint = (
            "https://www.reddit.com/api/v1/revoke_token"
            if channel == "reddit"
            else "https://api.pinterest.com/v5/oauth/token/revoke"
        )
        request_json(
            "POST",
            endpoint,
            headers=headers,
            allow_empty=True,
            data={
                "token": credential,
                "token_type_hint": (
                    "refresh_token" if token.get("refresh_token") else "access_token"
                ),
            },
        )
    elif channel == "tiktok":
        request_json(
            "POST",
            "https://business-api.tiktok.com/open_api/v1.3/oauth2/revoke_token/",
            headers={**headers, "Access-Token": access},
            json={
                "app_id": app["client_id"],
                "secret": app["client_secret"],
                "access_token": access,
            },
        )
    else:
        pages = {
            "microsoft": "https://account.live.com/consent/Manage",
            "linkedin": "https://www.linkedin.com/psettings/permitted-services",
            "snapchat": "https://accounts.snapchat.com/accounts/oauth2/apps",
            "amazon_ads": "https://www.amazon.com/ap/adam",
        }
        return {
            "remote_revoked": False,
            "revocation_url": pages[channel],
            "message": "Remove application consent on the platform to revoke the remote grant.",
        }
    return {
        "remote_revoked": True,
        "revocation_url": None,
        "message": "The platform acknowledged token revocation.",
    }


def discover(provider: OAuthProvider, app: dict, token: dict) -> list[dict]:
    """Validate provider responses before account identities enter persistence."""
    try:
        return _discover(provider, app, token)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise DomainError(
            "PlatformResponseInvalid",
            "Account discovery returned an invalid response.",
            502,
        ) from exc


def reddit_pages(method: str, path: str, headers: dict) -> list[dict]:
    """Follow documented pagination URLs without forwarding credentials to another origin."""
    base = "https://ads-api.reddit.com"
    url = base + "/api/v3" + path
    expected_path = urlsplit(url).path
    seen: set[str] = set()
    records: list[dict] = []
    for _ in range(100):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "ads-api.reddit.com"
            or parsed.path != expected_path
            or parsed.fragment
            or url in seen
        ):
            raise DomainError(
                "PlatformResponseInvalid",
                "Reddit returned an invalid pagination URL.",
                502,
            )
        seen.add(url)
        if method == "POST":
            result = request_json(method, url, headers=headers, json={"data": {}})
        else:
            result = request_json(method, url, headers=headers)
        records.extend(result["data"])
        url = result.get("pagination", {}).get("next_url")
        if not url:
            return records
    raise DomainError("AccountDiscoveryLimit", "Reddit account pagination exceeded 100 pages.", 502)


def _discover(provider: OAuthProvider, app: dict, token: dict) -> list[dict]:
    headers = {
        "Authorization": "Bearer " + token["access_token"],
        "User-Agent": "Adjutant/2.0",
    }
    channel = provider.channel
    accounts: list[dict] = []

    def account(value, identifier="id", name="name", **extra):
        if not isinstance(value[identifier], (str, int)) or isinstance(value[identifier], bool):
            raise ValueError("Invalid account identifier type")
        identity = str(value[identifier])
        if not identity or len(identity) > 200:
            raise DomainError(
                "PlatformResponseInvalid",
                "The platform returned an invalid account ID.",
                502,
            )
        accounts.append({"id": identity, "name": str(value.get(name) or identity), **extra})

    if channel == "meta":
        params = {"fields": "id,name,account_status,currency", "limit": 100}
        for _ in range(100):
            result = request_json(
                "GET",
                "https://graph.facebook.com/v26.0/me/adaccounts",
                params=params,
                headers=headers,
            )
            for item in result["data"]:
                account(
                    item,
                    currency=item.get("currency"),
                    provider_status=item.get("account_status"),
                )
            if not result.get("paging", {}).get("next"):
                return accounts
            params["after"] = result["paging"]["cursors"]["after"]
    elif channel in {"google_ads", "youtube"}:
        headers["developer-token"] = app["developer_token"]
        result = request_json(
            "GET",
            "https://googleads.googleapis.com/v25/customers:listAccessibleCustomers",
            headers=headers,
        )
        pending = []
        for name in result.get("resourceNames", []):
            root = name.split("/")[-1]
            if not re.fullmatch(r"[0-9]+", root):
                raise DomainError(
                    "PlatformResponseInvalid",
                    "Google returned an invalid customer ID.",
                    502,
                )
            pending.append((root, root))
        visited: set[tuple[str, str]] = set()
        clients: set[str] = set()
        while pending:
            customer, root = pending.pop(0)
            if (customer, root) in visited:
                continue
            visited.add((customer, root))
            if len(visited) > 1000:
                raise DomainError(
                    "AccountDiscoveryLimit",
                    "Google account hierarchy exceeded 1000 accounts.",
                    502,
                )
            headers["login-customer-id"] = root
            body = {
                "query": "SELECT customer_client.id,customer_client.manager,"
                "customer_client.level,customer_client.descriptive_name,"
                "customer_client.currency_code,customer_client.time_zone "
                "FROM customer_client WHERE customer_client.level <= 1"
            }
            for _ in range(100):
                result = request_json(
                    "POST",
                    f"https://googleads.googleapis.com/v25/customers/{customer}/googleAds:search",
                    headers=headers,
                    json=body,
                )
                for row in result.get("results", []):
                    value = row["customerClient"]
                    identity = str(value["id"])
                    if not re.fullmatch(r"[0-9]+", identity):
                        raise DomainError(
                            "PlatformResponseInvalid",
                            "Google returned an invalid customer ID.",
                            502,
                        )
                    if value.get("manager"):
                        if identity != customer:
                            pending.append((identity, root))
                    elif identity not in clients:
                        clients.add(identity)
                        account(
                            value,
                            "id",
                            "descriptiveName",
                            login_customer_id=root,
                            currency=value.get("currencyCode"),
                            time_zone=value.get("timeZone"),
                        )
                if not result.get("nextPageToken"):
                    break
                body["pageToken"] = result["nextPageToken"]
            else:
                raise DomainError(
                    "AccountDiscoveryLimit",
                    "Google account discovery exceeded its page limit.",
                    502,
                )
        return accounts
    elif channel == "tiktok":
        headers = {"Access-Token": token["access_token"]}
        result = request_json(
            "GET",
            "https://business-api.tiktok.com/open_api/v1.3/oauth2/advertiser/get/",
            params={"app_id": app["client_id"], "secret": app["client_secret"]},
            headers=headers,
        )
        for item in result["data"]["list"]:
            account(item, "advertiser_id", "advertiser_name")
        return accounts
    elif channel == "linkedin":
        headers.update({"LinkedIn-Version": "202608", "X-Restli-Protocol-Version": "2.0.0"})
        for page in range(100):
            result = request_json(
                "GET",
                "https://api.linkedin.com/rest/adAccounts",
                params={"q": "search", "start": page * 100, "count": 100},
                headers=headers,
            )
            for item in result["elements"]:
                account(item, currency=item.get("currency"))
            if len(result["elements"]) < 100:
                return accounts
    elif channel == "microsoft":
        headers = {
            "Authorization": "Bearer " + token["access_token"],
            "DeveloperToken": app["developer_token"],
        }
        base = "https://clientcenter.api.bingads.microsoft.com/CustomerManagement/v13"
        user = request_json("POST", base + "/User/Query", json={"UserId": None}, headers=headers)
        for page in range(100):
            result = request_json(
                "POST",
                base + "/Accounts/Search",
                headers=headers,
                json={
                    "Predicates": [
                        {
                            "Field": "UserId",
                            "Operator": "Equals",
                            "Value": str(user["User"]["Id"]),
                        }
                    ],
                    "PageInfo": {"Index": page, "Size": 100},
                },
            )
            for item in result.get("Accounts", []):
                account(
                    item,
                    "Id",
                    "Name",
                    customer_id=str(item["ParentCustomerId"]),
                    currency=item.get("CurrencyCode"),
                )
            if len(result.get("Accounts", [])) < 100:
                return accounts
    elif channel == "reddit":
        businesses = reddit_pages("GET", "/me/businesses", headers)
        seen_accounts: set[str] = set()
        for business in businesses:
            identity = quote(str(business["id"]), safe="")
            rows = reddit_pages("POST", f"/businesses/{identity}/ad_accounts/query", headers)
            for item in rows:
                if str(item["id"]) not in seen_accounts:
                    account(item, currency=item.get("currency"), business_id=business["id"])
                    seen_accounts.add(str(item["id"]))
        return accounts
    elif channel == "pinterest":
        params = {"page_size": 100}
        for _ in range(100):
            result = request_json(
                "GET",
                "https://api.pinterest.com/v5/ad_accounts",
                params=params,
                headers=headers,
            )
            for item in result["items"]:
                account(item, currency=item.get("currency"))
            if not result.get("bookmark"):
                return accounts
            params["bookmark"] = result["bookmark"]
    elif channel == "snapchat":
        result = request_json(
            "GET",
            "https://adsapi.snapchat.com/v1/me/organizations",
            params={"with_ad_accounts": "true"},
            headers=headers,
        )
        for item in result["organizations"]:
            organization = item["organization"]
            for entry in organization.get("ad_accounts", []):
                value = entry.get("ad_account", entry)
                account(
                    value,
                    organization_id=organization["id"],
                    currency=value.get("currency"),
                )
        return accounts
    elif channel == "amazon_ads":
        origins = {
            "NA": "https://advertising-api.amazon.com",
            "EU": "https://advertising-api-eu.amazon.com",
            "FE": "https://advertising-api-fe.amazon.com",
        }
        headers["Amazon-Advertising-API-ClientId"] = app["client_id"]
        result = request_json("GET", origins[app["region"]] + "/v2/profiles", headers=headers)
        for item in result:
            account(
                {
                    "id": str(item["profileId"]),
                    "name": item.get("accountInfo", {}).get("name"),
                },
                currency=item.get("currencyCode"),
                country=item.get("countryCode"),
            )
        return accounts
    raise DomainError(
        "AccountDiscoveryLimit",
        "Account discovery exceeded the provider pagination limit.",
        502,
    )
