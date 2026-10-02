"""Live channel metric fetchers for Meta, Google Ads, YouTube, LinkedIn, and Reddit."""

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from adjutant.errors import DomainError

logger = logging.getLogger(__name__)


def numeric(value: str) -> str:
    cleaned = re.sub(r"[^0-9]", "", str(value))
    if not cleaned:
        raise DomainError("InvalidRemoteIdentity", "The platform requires a numeric identity.", 422)
    return cleaned


def identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", str(value))
    if not cleaned:
        raise DomainError("InvalidRemoteIdentity", "The platform requires a valid identity.", 422)
    return quote(cleaned, safe="")


async def fetch_meta_metrics(
    client: httpx.AsyncClient,
    account_id: str,
    native_id: str,
    window_start: datetime,
    window_end: datetime,
    token: str,
) -> dict[str, Any]:
    """Fetch hourly/daily insights from Meta Graph API v26.0."""
    clean_id = numeric(native_id)
    url = f"https://graph.facebook.com/v26.0/{clean_id}/insights"
    since_str = window_start.strftime("%Y-%m-%d")
    until_str = window_end.strftime("%Y-%m-%d")

    params = {
        "access_token": token,
        "time_range": f'{{"since":"{since_str}","until":"{until_str}"}}',
        "fields": (
            "impressions,clicks,spend,actions,action_values,reach,frequency,"
            "video_p100_watched_actions"
        ),
    }

    try:
        response = await client.get(url, params=params, timeout=15.0)
    except httpx.HTTPError as exc:
        raise DomainError(
            "PlatformUnavailable",
            "Meta Graph API did not respond during metrics fetch.",
            503,
        ) from exc

    if response.status_code in {401, 403}:
        raise DomainError(
            "PlatformAuthorization",
            "Meta authorization expired or lacks insights permissions.",
            403,
        )
    if response.status_code == 429:
        raise DomainError(
            "PlatformRateLimited",
            "Meta insights rate limit reached.",
            429,
        )
    if not 200 <= response.status_code < 300:
        raise DomainError(
            "PlatformRequestRejected",
            f"Meta insights returned HTTP {response.status_code}.",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise DomainError(
            "PlatformResponseInvalid", "Meta returned invalid JSON insights.", 502
        ) from exc

    results = data.get("data", [])
    if not results:
        return {
            "impressions": 0,
            "clicks": 0,
            "spend_usd": Decimal("0.00"),
            "conversions": Decimal("0.00"),
            "conversion_value_usd": Decimal("0.00"),
            "frequency": Decimal("1.00"),
            "reach": 0,
            "video_completions": 0,
            "attribution_window": "7d_click",
            "attribution_model": "last_click",
            "conversion_event": "purchase",
            "view_through_policy": "none",
            "native_metrics": {},
        }

    row = results[0]
    impressions = int(row.get("impressions", 0))
    clicks = int(row.get("clicks", 0))
    spend = Decimal(str(row.get("spend", "0.00")))
    frequency = Decimal(str(row.get("frequency", "1.00")))
    reach = int(row.get("reach", 0))

    # Parse conversions from actions array
    conversions = Decimal("0.00")
    video_completions = 0
    actions = row.get("actions", [])
    for action in actions:
        action_type = action.get("action_type", "")
        if action_type in {
            "purchase",
            "lead",
            "omni_purchase",
            "offsite_conversion.fb_pixel_purchase",
        }:
            conversions += Decimal(str(action.get("value", 0)))
        if action_type == "video_view":
            video_completions += int(action.get("value", 0))

    # Parse conversion value
    conversion_value = Decimal("0.00")
    action_values = row.get("action_values", [])
    for val in action_values:
        if val.get("action_type") in {
            "purchase",
            "omni_purchase",
            "offsite_conversion.fb_pixel_purchase",
        }:
            conversion_value += Decimal(str(val.get("value", "0.00")))

    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend_usd": spend,
        "conversions": conversions,
        "conversion_value_usd": conversion_value,
        "frequency": frequency,
        "reach": reach,
        "video_completions": video_completions,
        "attribution_window": "7d_click",
        "attribution_model": "last_click",
        "conversion_event": "purchase",
        "view_through_policy": "none",
        "native_metrics": row,
    }


async def fetch_google_ads_metrics(
    client: httpx.AsyncClient,
    customer_id: str,
    campaign_id: str,
    window_start: datetime,
    window_end: datetime,
    token: str,
    developer_token: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Fetch campaign performance from Google Ads API v25 via GAQL."""
    clean_cust = numeric(customer_id)
    clean_camp = numeric(campaign_id)
    url = f"https://googleads.googleapis.com/v25/customers/{clean_cust}/googleAds:search"
    start_date = window_start.strftime("%Y-%m-%d")
    end_date = window_end.strftime("%Y-%m-%d")

    headers = {
        "Authorization": f"Bearer {token}",
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }
    if login_customer_id:
        headers["login-customer-id"] = numeric(login_customer_id)

    query = (
        "SELECT metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.conversions, metrics.conversions_value, metrics.video_views "
        "FROM campaign "
        f"WHERE campaign.id = {clean_camp} "
        f"AND segments.date BETWEEN '{start_date}' AND '{end_date}'"
    )

    try:
        response = await client.post(url, headers=headers, json={"query": query}, timeout=15.0)
    except httpx.HTTPError as exc:
        raise DomainError(
            "PlatformUnavailable",
            "Google Ads API did not respond during metrics search.",
            503,
        ) from exc

    if response.status_code in {401, 403}:
        raise DomainError(
            "PlatformAuthorization",
            "Google Ads developer token or OAuth credential rejected.",
            403,
        )
    if response.status_code == 429:
        raise DomainError("PlatformRateLimited", "Google Ads rate limit reached.", 429)
    if not 200 <= response.status_code < 300:
        raise DomainError(
            "PlatformRequestRejected",
            f"Google Ads metrics query returned HTTP {response.status_code}.",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise DomainError(
            "PlatformResponseInvalid", "Google Ads returned invalid JSON.", 502
        ) from exc

    results = data.get("results", [])
    if not results:
        return {
            "impressions": 0,
            "clicks": 0,
            "spend_usd": Decimal("0.00"),
            "conversions": Decimal("0.00"),
            "conversion_value_usd": Decimal("0.00"),
            "frequency": Decimal("1.00"),
            "reach": 0,
            "video_views": 0,
            "attribution_window": "30d_click",
            "attribution_model": "last_click",
            "conversion_event": "purchase",
            "view_through_policy": "none",
            "native_metrics": {},
        }

    total_imp = 0
    total_clicks = 0
    total_micros = 0
    total_conv = Decimal("0.00")
    total_val = Decimal("0.00")
    total_views = 0

    for row in results:
        m = row.get("metrics", {})
        total_imp += int(m.get("impressions", 0))
        total_clicks += int(m.get("clicks", 0))
        total_micros += int(m.get("costMicros", 0))
        total_conv += Decimal(str(m.get("conversions", 0)))
        total_val += Decimal(str(m.get("conversionsValue", 0)))
        total_views += int(m.get("videoViews", 0))

    spend_usd = (Decimal(total_micros) / Decimal("1000000.00")).quantize(Decimal("0.01"))

    return {
        "impressions": total_imp,
        "clicks": total_clicks,
        "spend_usd": spend_usd,
        "conversions": total_conv,
        "conversion_value_usd": total_val,
        "frequency": Decimal("1.00"),
        "reach": total_imp,
        "video_views": total_views,
        "attribution_window": "30d_click",
        "attribution_model": "last_click",
        "conversion_event": "purchase",
        "view_through_policy": "none",
        "native_metrics": {"results_count": len(results)},
    }


async def fetch_youtube_metrics(
    client: httpx.AsyncClient,
    customer_id: str,
    campaign_id: str,
    window_start: datetime,
    window_end: datetime,
    token: str,
    developer_token: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Fetch YouTube Video & Shorts metrics from Google Ads API v25."""
    clean_cust = numeric(customer_id)
    clean_camp = numeric(campaign_id)
    url = f"https://googleads.googleapis.com/v25/customers/{clean_cust}/googleAds:search"
    start_date = window_start.strftime("%Y-%m-%d")
    end_date = window_end.strftime("%Y-%m-%d")

    headers = {
        "Authorization": f"Bearer {token}",
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }
    if login_customer_id:
        headers["login-customer-id"] = numeric(login_customer_id)

    query = (
        "SELECT metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.conversions, metrics.conversions_value, metrics.video_views, "
        "metrics.video_quartile_p100_rate "
        "FROM campaign "
        f"WHERE campaign.id = {clean_camp} "
        f"AND segments.date BETWEEN '{start_date}' AND '{end_date}'"
    )

    try:
        response = await client.post(url, headers=headers, json={"query": query}, timeout=15.0)
    except httpx.HTTPError as exc:
        raise DomainError(
            "PlatformUnavailable",
            "Google Ads API did not respond during YouTube metrics search.",
            503,
        ) from exc

    if response.status_code in {401, 403}:
        raise DomainError(
            "PlatformAuthorization",
            "Google Ads credentials rejected for YouTube metrics.",
            403,
        )
    if response.status_code == 429:
        raise DomainError("PlatformRateLimited", "Google Ads rate limit reached.", 429)
    if not 200 <= response.status_code < 300:
        raise DomainError(
            "PlatformRequestRejected",
            f"YouTube metrics query returned HTTP {response.status_code}.",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise DomainError(
            "PlatformResponseInvalid", "YouTube API returned invalid JSON.", 502
        ) from exc

    results = data.get("results", [])
    if not results:
        return {
            "impressions": 0,
            "clicks": 0,
            "spend_usd": Decimal("0.00"),
            "conversions": Decimal("0.00"),
            "conversion_value_usd": Decimal("0.00"),
            "frequency": Decimal("1.00"),
            "reach": 0,
            "video_views": 0,
            "video_completions": 0,
            "attribution_window": "30d_click",
            "attribution_model": "last_click",
            "conversion_event": "purchase",
            "view_through_policy": "none",
            "native_metrics": {},
        }

    total_imp = 0
    total_clicks = 0
    total_micros = 0
    total_conv = Decimal("0.00")
    total_val = Decimal("0.00")
    total_views = 0
    total_completions = 0

    for row in results:
        m = row.get("metrics", {})
        imp = int(m.get("impressions", 0))
        views = int(m.get("videoViews", 0))
        comp_rate = float(m.get("videoQuartileP100Rate", 0.0))
        total_imp += imp
        total_clicks += int(m.get("clicks", 0))
        total_micros += int(m.get("costMicros", 0))
        total_conv += Decimal(str(m.get("conversions", 0)))
        total_val += Decimal(str(m.get("conversionsValue", 0)))
        total_views += views
        total_completions += int(views * comp_rate)

    spend_usd = (Decimal(total_micros) / Decimal("1000000.00")).quantize(Decimal("0.01"))

    return {
        "impressions": total_imp,
        "clicks": total_clicks,
        "spend_usd": spend_usd,
        "conversions": total_conv,
        "conversion_value_usd": total_val,
        "frequency": Decimal("1.00"),
        "reach": total_imp,
        "video_views": total_views,
        "video_completions": total_completions,
        "attribution_window": "30d_click",
        "attribution_model": "last_click",
        "conversion_event": "purchase",
        "view_through_policy": "none",
        "native_metrics": {"results_count": len(results)},
    }


async def fetch_linkedin_metrics(
    client: httpx.AsyncClient,
    account_id: str,
    campaign_id: str,
    window_start: datetime,
    window_end: datetime,
    token: str,
) -> dict[str, Any]:
    """Fetch LinkedIn campaign performance via adAnalyticsV2."""
    clean_id = numeric(campaign_id)
    url = "https://api.linkedin.com/rest/adAnalyticsV2"

    headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": "202608",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    params: dict[str, Any] = {
        "q": "analytics",
        "pivot": "CAMPAIGN",
        "dateRange.start.year": window_start.year,
        "dateRange.start.month": window_start.month,
        "dateRange.start.day": window_start.day,
        "dateRange.end.year": window_end.year,
        "dateRange.end.month": window_end.month,
        "dateRange.end.day": window_end.day,
        "timeGranularity": "DAILY",
        "campaigns[0]": f"urn:li:sponsoredCampaign:{clean_id}",
        "fields": (
            "impressions,clicks,costInLocalCurrency,externalWebsiteConversions,"
            "conversionValueInLocalCurrency,videoCompletions"
        ),
    }

    try:
        response = await client.get(url, headers=headers, params=params, timeout=15.0)
    except httpx.HTTPError as exc:
        raise DomainError(
            "PlatformUnavailable",
            "LinkedIn Marketing API did not respond during analytics fetch.",
            503,
        ) from exc

    if response.status_code in {401, 403}:
        raise DomainError(
            "PlatformAuthorization",
            "LinkedIn token expired or lacks r_ads_reporting permission.",
            403,
        )
    if response.status_code == 429:
        raise DomainError("PlatformRateLimited", "LinkedIn API rate limit reached.", 429)
    if not 200 <= response.status_code < 300:
        raise DomainError(
            "PlatformRequestRejected",
            f"LinkedIn analytics returned HTTP {response.status_code}.",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise DomainError(
            "PlatformResponseInvalid", "LinkedIn returned invalid JSON analytics.", 502
        ) from exc

    elements = data.get("elements", [])
    if not elements:
        return {
            "impressions": 0,
            "clicks": 0,
            "spend_usd": Decimal("0.00"),
            "conversions": Decimal("0.00"),
            "conversion_value_usd": Decimal("0.00"),
            "frequency": Decimal("1.00"),
            "reach": 0,
            "video_completions": 0,
            "attribution_window": "30d_click",
            "attribution_model": "last_click",
            "conversion_event": "lead",
            "view_through_policy": "none",
            "native_metrics": {},
        }

    total_imp = 0
    total_clicks = 0
    total_cost = Decimal("0.00")
    total_conv = Decimal("0.00")
    total_val = Decimal("0.00")
    total_video_comp = 0

    for el in elements:
        total_imp += int(el.get("impressions", 0))
        total_clicks += int(el.get("clicks", 0))
        total_cost += Decimal(str(el.get("costInLocalCurrency", "0.00")))
        total_conv += Decimal(str(el.get("externalWebsiteConversions", 0)))
        total_val += Decimal(str(el.get("conversionValueInLocalCurrency", "0.00")))
        total_video_comp += int(el.get("videoCompletions", 0))

    return {
        "impressions": total_imp,
        "clicks": total_clicks,
        "spend_usd": total_cost,
        "conversions": total_conv,
        "conversion_value_usd": total_val,
        "frequency": Decimal("1.00"),
        "reach": total_imp,
        "video_completions": total_video_comp,
        "attribution_window": "30d_click",
        "attribution_model": "last_click",
        "conversion_event": "lead",
        "view_through_policy": "none",
        "native_metrics": {"elements_count": len(elements)},
    }


async def fetch_reddit_metrics(
    client: httpx.AsyncClient,
    account_id: str,
    campaign_id: str,
    window_start: datetime,
    window_end: datetime,
    token: str,
) -> dict[str, Any]:
    """Fetch Reddit Ads v3 campaign report."""
    clean_acc = identifier(account_id)
    clean_camp = identifier(campaign_id)
    url = "https://ads-api.reddit.com/api/v3/reports"

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Adjutant/2.0",
    }

    params = {
        "account_id": clean_acc,
        "starts_at": window_start.strftime("%Y-%m-%dT%H:00:00Z"),
        "ends_at": window_end.strftime("%Y-%m-%dT%H:00:00Z"),
        "group_by": "campaign_id",
        "fields": "impressions,clicks,spend,conversions,conversion_value",
    }

    try:
        response = await client.get(url, headers=headers, params=params, timeout=15.0)
    except httpx.HTTPError as exc:
        raise DomainError(
            "PlatformUnavailable",
            "Reddit Ads API did not respond during report fetch.",
            503,
        ) from exc

    if response.status_code in {401, 403}:
        raise DomainError(
            "PlatformAuthorization",
            "Reddit Ads token expired or lacks adsread scope.",
            403,
        )
    if response.status_code == 429:
        raise DomainError("PlatformRateLimited", "Reddit Ads rate limit reached.", 429)
    if not 200 <= response.status_code < 300:
        raise DomainError(
            "PlatformRequestRejected",
            f"Reddit reports returned HTTP {response.status_code}.",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise DomainError(
            "PlatformResponseInvalid", "Reddit returned invalid JSON report.", 502
        ) from exc

    rows = data.get("data", [])
    matching = [r for r in rows if str(r.get("campaign_id")) == clean_camp]
    if not matching:
        return {
            "impressions": 0,
            "clicks": 0,
            "spend_usd": Decimal("0.00"),
            "conversions": Decimal("0.00"),
            "conversion_value_usd": Decimal("0.00"),
            "frequency": Decimal("1.00"),
            "reach": 0,
            "attribution_window": "7d_click",
            "attribution_model": "last_click",
            "conversion_event": "purchase",
            "view_through_policy": "none",
            "native_metrics": {},
        }

    row = matching[0]
    raw_spend_cents = int(row.get("spend", 0))
    spend_usd = (Decimal(raw_spend_cents) / Decimal("100.00")).quantize(Decimal("0.01"))
    raw_val_cents = int(row.get("conversion_value", 0))
    val_usd = (Decimal(raw_val_cents) / Decimal("100.00")).quantize(Decimal("0.01"))

    return {
        "impressions": int(row.get("impressions", 0)),
        "clicks": int(row.get("clicks", 0)),
        "spend_usd": spend_usd,
        "conversions": Decimal(str(row.get("conversions", 0))),
        "conversion_value_usd": val_usd,
        "frequency": Decimal("1.00"),
        "reach": int(row.get("impressions", 0)),
        "attribution_window": "7d_click",
        "attribution_model": "last_click",
        "conversion_event": "purchase",
        "view_through_policy": "none",
        "native_metrics": row,
    }


async def fetch_live_channel_metrics(
    channel: str,
    client: httpx.AsyncClient,
    app: dict[str, Any],
    token: dict[str, Any],
    account_id: str,
    native_id: str,
    window_start: datetime,
    window_end: datetime,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Universal dispatcher for live metric collection across all 5 active channels."""
    access_token = token.get("access_token", "")
    meta = metadata or {}

    if channel == "meta":
        return await fetch_meta_metrics(
            client, account_id, native_id, window_start, window_end, access_token
        )
    elif channel == "google_ads":
        return await fetch_google_ads_metrics(
            client,
            account_id,
            native_id,
            window_start,
            window_end,
            access_token,
            app.get("developer_token", ""),
            meta.get("login_customer_id"),
        )
    elif channel == "youtube":
        return await fetch_youtube_metrics(
            client,
            account_id,
            native_id,
            window_start,
            window_end,
            access_token,
            app.get("developer_token", ""),
            meta.get("login_customer_id"),
        )
    elif channel == "linkedin":
        return await fetch_linkedin_metrics(
            client, account_id, native_id, window_start, window_end, access_token
        )
    elif channel == "reddit":
        return await fetch_reddit_metrics(
            client, account_id, native_id, window_start, window_end, access_token
        )
    else:
        raise DomainError(
            "UnknownChannel", f"Channel '{channel}' is not supported for metrics.", 422
        )
