"""Provider protocol regression coverage; these tests do not establish live account access."""

import logging

import httpx
import pytest

from adjutant.adapters import authorization
from adjutant.errors import DomainError
from adjutant.telemetry import CredentialQueryFilter


def test_reddit_discovery_queries_all_business_pages_and_deduplicates_shared_accounts(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if url.endswith("/me/businesses"):
            return {
                "data": [{"id": "business-1"}],
                "pagination": {"next_url": url + "?page.token=second"},
            }
        if "/me/businesses?" in url:
            return {"data": [{"id": "business-2"}]}
        assert method == "POST" and kwargs["json"] == {"data": {}}
        assert "/ad_accounts/query" in url
        if "/business-1/" in url and "?" not in url:
            return {
                "data": [{"id": "a2_owned", "name": "Owned"}],
                "pagination": {"next_url": url + "?page.token=second"},
            }
        return {"data": [{"id": "a2_shared", "name": "Shared"}]}

    monkeypatch.setattr(authorization, "request_json", request)
    result = authorization.discover(
        authorization.provider_for("reddit"), {}, {"access_token": "private"}
    )
    assert [item["id"] for item in result] == ["a2_owned", "a2_shared"]
    assert len(calls) == 5


@pytest.mark.parametrize(
    "next_url",
    [
        "https://attacker.test/api/v3/me/businesses",
        "https://ads-api.reddit.com/api/v3/me/businesses",
    ],
)
def test_reddit_pagination_rejects_credential_exfiltration_and_cycles(monkeypatch, next_url):
    calls = []

    def request(method, url, **kwargs):
        calls.append(url)
        return {"data": [], "pagination": {"next_url": next_url}}

    monkeypatch.setattr(authorization, "request_json", request)
    with pytest.raises(DomainError, match="pagination"):
        authorization.discover(
            authorization.provider_for("reddit"), {}, {"access_token": "private"}
        )
    assert len(calls) == 1


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_provider_errors_never_include_response_credentials(monkeypatch, status):
    original = httpx.Client

    def client(**kwargs):
        return original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    status, json={"error": "private-token-secret"}, request=request
                )
            ),
        )

    monkeypatch.setattr(authorization.httpx, "Client", client)
    with pytest.raises(DomainError) as error:
        authorization.request_json("GET", "https://api.example.test/accounts")
    assert "private-token-secret" not in error.value.message
    assert error.value.status == (403 if status in {401, 403} else 429 if status == 429 else 502)


def test_google_discovery_walks_manager_hierarchy_and_pages(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("listAccessibleCustomers"):
            return {"resourceNames": ["customers/123"]}
        if "/123/" in url:
            if kwargs["json"].get("pageToken"):
                return {"results": [{"customerClient": {"id": "789", "manager": True}}]}
            return {
                "results": [
                    {"customerClient": {"id": "123", "manager": True}},
                    {
                        "customerClient": {
                            "id": "456",
                            "descriptiveName": "First advertiser",
                            "currencyCode": "USD",
                            "timeZone": "America/Chicago",
                        }
                    },
                ],
                "nextPageToken": "page-two",
            }
        assert "/789/" in url
        return {
            "results": [
                {"customerClient": {"id": "789", "manager": True}},
                {"customerClient": {"id": "987", "descriptiveName": "Nested advertiser"}},
            ]
        }

    monkeypatch.setattr(authorization, "request_json", request)
    accounts = authorization.discover(
        authorization.provider_for("google_ads"),
        {"developer_token": "developer-secret"},
        {"access_token": "access-secret"},
    )
    assert [account["id"] for account in accounts] == ["456", "987"]
    assert all(account["login_customer_id"] == "123" for account in accounts)
    assert len(calls) == 4


@pytest.mark.parametrize(
    "channel", ["google_ads", "youtube", "reddit", "pinterest", "meta", "tiktok"]
)
def test_remote_revocation_uses_provider_endpoint(monkeypatch, channel):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"success": True}

    monkeypatch.setattr(authorization, "request_json", request)
    result = authorization.revoke(
        authorization.provider_for(channel),
        {"client_id": "client", "client_secret": "private-secret"},
        {"access_token": "private-access", "refresh_token": "private-refresh"},
    )
    assert result["remote_revoked"]
    assert len(calls) == 1
    method, url, kwargs = calls[0]
    assert method in {"POST", "DELETE"}
    assert "private-" not in url
    assert kwargs


def test_empty_success_is_only_permitted_for_revocation(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(
        authorization.httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(lambda request: httpx.Response(204, request=request)),
        ),
    )
    assert (
        authorization.request_json("POST", "https://api.example.test/revoke", allow_empty=True)
        == {}
    )
    with pytest.raises(DomainError, match="unexpected response"):
        authorization.request_json("GET", "https://api.example.test/accounts")


def test_access_logs_strip_oauth_and_provider_query_secrets():
    filter = CredentialQueryFilter()
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        "",
        1,
        "HTTP %s %s",
        ("GET", httpx.URL("https://api.example.test/?secret=private-token")),
        None,
    )
    filter.filter(record)
    assert "private-token" not in record.getMessage()
    callback = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        "%s %s %s %s %s",
        ("127.0.0.1", "GET", "/callback?code=private-code&state=secret", "1.1", 303),
        None,
    )
    filter.filter(callback)
    assert "private-code" not in callback.getMessage()
