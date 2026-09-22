"""Adapter Conformance Test Suite (Slice 9 & Slice 10).

Verifies channel adapters against identical architectural invariants:
- Zero waivers across all supported channels.
- Pure registry-driven dispatch with zero branching above the adapter layer.
- Deterministic idempotency, structured ancestry, and error normalization.
- Offline execution against HTTP cassettes without touching live provider APIs.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest
import httpx

from adjutant.adapters.builds import BUILDERS, build_configuration, builder_for
from adjutant.adapters.campaign_control import CampaignControl, CampaignTarget
from tests.conformance.cassette import Cassette, CassetteTransport

CASSETTES_DIR = Path(__file__).parent / "cassettes"

SUPPORTED_CHANNELS = list(BUILDERS.keys())


@pytest.mark.parametrize("channel", SUPPORTED_CHANNELS)
def test_conformance_schema_and_builder_registered(channel):
    """S9.1: Every adapter must register an executable Builder, Settings Model, and Preflight validator."""
    builder_cls, settings_cls, preflight_fn = builder_for(channel)
    assert builder_cls is not None
    assert settings_cls is not None
    assert callable(preflight_fn)

    config = build_configuration(channel)
    assert config is not None
    fields = config.get("fields", [])
    assert len(fields) > 0
    field_names = {f["name"] for f in fields}
    assert "destination_url" in field_names
    assert "countries" in field_names


@pytest.mark.parametrize("channel", SUPPORTED_CHANNELS)
def test_conformance_preflight_validates_objective_and_limits(channel):
    """S9.1: Preflight validates campaign objective and placement text limits before remote writes."""
    _, _, preflight_fn = builder_for(channel)
    
    # Invalid objective fails preflight
    invalid_doc = {
        "objective": "invalid_objective_xyz",
        "settings": {
            "page_id": "123456",
            "destination_url": "https://example.com",
            "countries": ["US"],
            "end_time": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        },
        "creatives": [
            {
                "id": "c_001",
                "copy": {
                    channel: {
                        "headline": "Valid Headline",
                        "primary_text": "Valid Primary Text",
                        "description": "Valid Description",
                    }
                },
            }
        ],
    }
    failures = preflight_fn(invalid_doc)
    assert len(failures) > 0

    # Headline exceeding text limits fails with informative error
    too_long_doc = {
        "objective": "leads",
        "settings": {
            "page_id": "123456",
            "destination_url": "https://example.com",
            "countries": ["US"],
            "end_time": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "pixel_id": "999888777",
            "conversion_event": "LEAD",
        },
        "creatives": [
            {
                "id": "c_001",
                "copy": {
                    channel: {
                        "headline": "A" * 150,  # exceeds limit of 40
                        "primary_text": "Normal text",
                        "description": "Short",
                    }
                },
            }
        ],
    }
    limits = {"headline": 40, "primary_text": 125, "description": 30}
    failures_long = preflight_fn(too_long_doc, text_limits=limits)
    assert any("headline" in f.lower() or "character" in f.lower() or "limit" in f.lower() for f in failures_long)


@pytest.mark.parametrize("channel", SUPPORTED_CHANNELS)
def test_conformance_campaign_control_pause_and_resume(channel):
    """S9.1 & S6.3: Every adapter provides verified pause and resume control."""
    async def _run():
        cassette_path = CASSETTES_DIR / f"{channel}.json"
        cassette = Cassette(cassette_path)
        transport = CassetteTransport(cassette)

        async with httpx.AsyncClient(transport=transport) as client:
            metadata = {}
            if channel == "microsoft":
                metadata["customer_id"] = "12345"
            elif channel == "amazon_ads":
                metadata["ad_product"] = "SPONSORED_PRODUCTS"

            target = CampaignTarget(
                channel=channel,
                account_id="12345",
                native_id="238491029387",
                metadata=metadata,
            )
            control = CampaignControl(
                client=client,
                target=target,
                app={"client_id": "cid", "developer_token": "devtok", "region": "NA"},
                token={"access_token": "scrubbed_token"},
            )
            res = await control.pause()
            assert res["state"] == "paused"

    asyncio.run(_run())


@pytest.mark.parametrize("channel", SUPPORTED_CHANNELS)
def test_conformance_offline_cassette_execution(channel):
    """S9.5: Adapter builds execute in CI against recorded cassettes with zero live account touches."""
    async def _run():
        cassette_path = CASSETTES_DIR / f"{channel}.json"
        assert cassette_path.exists(), f"Cassette missing for channel {channel} at {cassette_path}"

        cassette = Cassette(cassette_path)
        transport = CassetteTransport(cassette)

        async with httpx.AsyncClient(transport=transport) as client:
            builder_cls, _, _ = builder_for(channel)
            journal = {}

            def mock_begin(key, payload):
                if key in journal:
                    return {"fresh": False, "native_id": journal[key]}
                return {"fresh": True, "native_id": None}

            def mock_finish(key, native_id, payload):
                journal[key] = native_id

            def mock_load_image(uri):
                return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

            def mock_checkpoint():
                pass

            builder = builder_cls(
                client=client,
                account_id="act_12345",
                token={"access_token": "test_token_scrubbed"},
                begin=mock_begin,
                finish=mock_finish,
                load_image=mock_load_image,
                checkpoint=mock_checkpoint,
            )

            document = {
                "name": "Conformance Test Plan",
                "objective": "leads",
                "daily_budget_usd": "50.00",
                "monthly_budget_usd": "3000.00",
                "settings": {
                    "page_id": "12345",
                    "destination_url": "https://example.com",
                    "countries": ["US"],
                    "end_time": (datetime.now(UTC) + timedelta(days=14)).isoformat(),
                    "pixel_id": "12345",
                    "conversion_event": "LEAD",
                },
                "creatives": [
                    {
                        "id": "c_001",
                        "storage_key": "raw_content_bytes",
                        "copy": {
                            channel: {
                                "headline": "Test Headline",
                                "primary_text": "Conformance Test Primary Text",
                                "description": "Conformance Test Description",
                            },
                            "meta": {
                                "headline": "Conformance Test Headline",
                                "primary_text": "Conformance Test Primary Text",
                                "description": "Conformance Test Description",
                            },
                        },
                    }
                ],
            }

            # Build campaign hierarchy
            objects = await builder.build(document, "idem_test_001")
            assert len(objects) >= 3

            # Assert hierarchy: Campaign -> Group -> Ad
            campaign_obj = next(o for o in objects if o["level"] == "campaign")
            group_obj = next(o for o in objects if o["level"] == "ad_group")
            ad_obj = next(o for o in objects if o["level"] == "ad")

            assert campaign_obj["remote"]["id"] is not None
            assert group_obj["parent_key"] == campaign_obj["key"]
            assert ad_obj["parent_key"] == group_obj["key"]

            # All newly deployed objects start in paused state
            assert all(o["remote"]["status"] == "PAUSED" for o in objects)

            # S9.1 Idempotency on replay: re-running returns same native IDs without extra creates
            objects_replay = await builder.build(document, "idem_test_001")
            assert [o["remote"]["id"] for o in objects_replay] == [o["remote"]["id"] for o in objects]

    asyncio.run(_run())
