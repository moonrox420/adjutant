"""Real database authorization boundary tests; provider calls are isolated contract tests."""

import time
from urllib.parse import parse_qs, urlsplit

import pytest

from adjutant.adapters.authorization import PROVIDERS
from adjutant.errors import DomainError


def setup_app(client, brand, channel):
    response = client.put(
        f"/api/brands/{brand}/channels/{channel}/application",
        json={
            "client_id": "developer-client",
            "client_secret": "private-client-secret",
            "developer_token": "private-developer-token",
            "region": "NA",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def consent(client, brand, channel):
    response = client.post(f"/api/brands/{brand}/channels/{channel}/authorize")
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert query["redirect_uri"][0].endswith(f"/channels/{channel}/callback")
    return query["state"][0]


@pytest.mark.parametrize("channel", [p.channel for p in PROVIDERS])
def test_channel_authorize_discover_select_disconnect(
    client, brand, channel, monkeypatch, admin
):
    setup_app(client, brand, channel)
    calls = []

    def exchange(provider, app, redirect, **kwargs):
        calls.append(("exchange", provider.channel, kwargs["code"]))
        assert app["client_secret"] == "private-client-secret"
        return {
            "access_token": "private-access-token",
            "refresh_token": "private-refresh",
            "expires_at": time.time() + 3600,
        }

    def discover(provider, app, token):
        calls.append(("discover", provider.channel))
        assert token["access_token"] == "private-access-token"
        return [{"id": "1234", "name": "Authorized account"}]

    monkeypatch.setattr("adjutant.channel_api.exchange", exchange)
    monkeypatch.setattr("adjutant.channel_credentials.exchange", exchange)
    monkeypatch.setattr("adjutant.channel_api.discover", discover)
    monkeypatch.setattr(
        "adjutant.channel_api.revoke",
        lambda *args: {
            "remote_revoked": True,
            "revocation_url": None,
            "message": "Revocation acknowledged.",
        },
    )
    prefix = f"/api/brands/{brand}/channels/{channel}"
    state = consent(client, brand, channel)
    callback = client.get(
        prefix + "/callback",
        params={"state": state, "code": "consent-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303, callback.text
    assert parse_qs(urlsplit(callback.headers["location"]).query)["brand"] == [brand]
    assert (
        client.get(
            prefix + "/callback", params={"state": state, "code": "consent-code"}
        ).status_code
        == 400
    )
    assert client.post(prefix + "/discover").json()["accounts"][0]["id"] == "1234"
    assert (
        client.post(
            prefix + "/select", json={"account_id": "not-authorized"}
        ).status_code
        == 403
    )
    selected = client.post(prefix + "/select", json={"account_id": "1234"})
    assert selected.status_code == 200, selected.text
    status = client.get(f"/api/brands/{brand}/channels")
    assert "private-" not in status.text
    channel_state = next(x for x in status.json() if x["channel"] == channel)
    assert channel_state["connections"][0]["selected"]
    assert channel_state["connections"][0]["verified_at"]
    ciphertext = admin.execute(
        "SELECT ciphertext FROM tenant_secret WHERE brand_id=%s", (brand,)
    ).fetchall()
    assert all(b"private-" not in bytes(x["ciphertext"]) for x in ciphertext)
    assert len([c for c in calls if c[0] == "exchange"]) == 1
    assert client.delete(prefix + "/authorization").status_code == 200
    assert client.post(prefix + "/discover").status_code == 422
    channel_state = next(
        x
        for x in client.get(f"/api/brands/{brand}/channels").json()
        if x["channel"] == channel
    )
    assert not channel_state["token_saved"]
    assert not channel_state["connections"][0]["selected"]


def test_channel_refresh_is_durable_even_if_discovery_fails(client, brand, monkeypatch):
    setup_app(client, brand, "google_ads")
    refresh_calls = []

    def exchange(provider, app, redirect, **kwargs):
        if kwargs.get("code"):
            return {
                "access_token": "expired",
                "refresh_token": "initial",
                "expires_at": time.time() - 1,
            }
        refresh_calls.append(kwargs["previous"]["refresh_token"])
        return {
            "access_token": "renewed",
            "refresh_token": "rotated",
            "expires_at": time.time() + 3600,
        }

    def fail(*args):
        raise DomainError("PlatformUnavailable", "Provider unavailable.", 503)

    monkeypatch.setattr("adjutant.channel_api.exchange", exchange)
    monkeypatch.setattr("adjutant.channel_credentials.exchange", exchange)
    monkeypatch.setattr("adjutant.channel_api.discover", fail)
    state = consent(client, brand, "google_ads")
    prefix = f"/api/brands/{brand}/channels/google_ads"
    assert (
        client.get(
            prefix + "/callback",
            params={"state": state, "code": "ok"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert client.post(prefix + "/discover").status_code == 503
    assert client.post(prefix + "/discover").status_code == 503
    assert refresh_calls == ["initial"]
    status = client.get(f"/api/brands/{brand}/channels").json()
    assert (
        next(x for x in status if x["channel"] == "google_ads")["authorization"][
            "last_error"
        ]
        == "Provider unavailable."
    )


def test_channel_state_is_tenant_scoped_and_expires(client, brand, admin):
    setup_app(client, brand, "meta")
    state = consent(client, brand, "meta")
    prefix = f"/api/brands/{brand}/channels/meta"
    admin.execute(
        "UPDATE channel_oauth_state SET expires_at=now()-interval '1 second' WHERE brand_id=%s",
        (brand,),
    )
    assert (
        client.get(
            prefix + "/callback", params={"state": state, "code": "unused"}
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/api/brands/00000000-0000-0000-0000-000000000001/channels"
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/brands/00000000-0000-0000-0000-000000000001/channels/meta/discover"
        ).status_code
        == 404
    )


def test_channel_app_replacement_invalidates_pending_consent(client, brand):
    setup_app(client, brand, "meta")
    state = consent(client, brand, "meta")
    setup_app(client, brand, "meta")
    response = client.get(
        f"/api/brands/{brand}/channels/meta/callback",
        params={"state": state, "code": "stale"},
    )
    assert response.status_code == 400
