"""S4.3 and S4.4 verification: dynamic capability registry limits and
field-specific preflight rejection."""

from adjutant.adapters.meta_build import load_placement_spec_limits, preflight


def test_meta_preflight_specific_character_limit_violations():
    """S4.3: Preflight rejects a plan exceeding the channel's character limits

    with a specific violation message naming the field, actual length, and allowed limit.
    """
    valid_document = {
        "objective": "leads",
        "settings": {
            "page_id": "123456789",
            "destination_url": "https://example.com/landing",
            "countries": ["US"],
            "age_min": 18,
            "age_max": 65,
            "pixel_id": "987654321",
            "conversion_event": "LEAD",
            "end_time": "2026-12-31T23:59:59Z",
        },
        "creatives": [
            {
                "id": "c-valid-1",
                "storage_key": "dummy-key",
                "copy": {
                    "meta": {
                        "headline": "A" * 40,
                        "primary_text": "B" * 125,
                        "description": "C" * 30,
                    }
                },
            }
        ],
    }

    # Valid document produces zero failures
    assert preflight(valid_document) == []

    # Exceed headline limit (45 chars vs 40 allowed)
    overflow_headline_doc = {
        **valid_document,
        "creatives": [
            {
                "id": "c-overflow-1",
                "storage_key": "dummy-key",
                "copy": {
                    "meta": {
                        "headline": "H" * 45,
                        "primary_text": "Normal text",
                        "description": "Normal desc",
                    }
                },
            }
        ],
    }
    failures = preflight(overflow_headline_doc)
    assert len(failures) == 1
    msg = failures[0]
    assert "c-overflow-1" in msg
    assert "headline" in msg
    assert "45" in msg
    assert "40" in msg
    assert "exceeds allowed limit of 40 characters" in msg

    # Exceed primary_text limit (130 chars vs 125 allowed)
    overflow_primary_doc = {
        **valid_document,
        "creatives": [
            {
                "id": "c-overflow-2",
                "storage_key": "dummy-key",
                "copy": {
                    "meta": {
                        "headline": "Normal headline",
                        "primary_text": "P" * 130,
                        "description": "Normal desc",
                    }
                },
            }
        ],
    }
    failures = preflight(overflow_primary_doc)
    assert len(failures) == 1
    assert "primary_text" in failures[0]
    assert "130" in failures[0]
    assert "125" in failures[0]

    # Exceed description limit (35 chars vs 30 allowed)
    overflow_desc_doc = {
        **valid_document,
        "creatives": [
            {
                "id": "c-overflow-3",
                "storage_key": "dummy-key",
                "copy": {
                    "meta": {
                        "headline": "Normal headline",
                        "primary_text": "Normal text",
                        "description": "D" * 35,
                    }
                },
            }
        ],
    }
    failures = preflight(overflow_desc_doc)
    assert len(failures) == 1
    assert "description" in failures[0]
    assert "35" in failures[0]
    assert "30" in failures[0]

    # Missing field
    missing_field_doc = {
        **valid_document,
        "creatives": [
            {
                "id": "c-missing-1",
                "storage_key": "dummy-key",
                "copy": {
                    "meta": {
                        "headline": "Normal headline",
                        "description": "Normal desc",
                    }
                },
            }
        ],
    }
    failures = preflight(missing_field_doc)
    assert any("missing required Meta field 'primary_text'" in f for f in failures)


def test_placement_spec_limits_loaded_from_data_registry(admin):
    """S4.4: The capability registry is data, and limits are read from placement_spec
    rather than hardcoded."""
    limits = load_placement_spec_limits(admin, "meta", "meta.facebook_feed.square")
    assert limits["headline"] == 40
    assert limits["primary_text"] == 125
    assert limits["description"] == 30

    # Custom text limits passed to preflight are respected dynamically
    custom_limits = {"headline": 25, "primary_text": 80, "description": 20}
    test_doc = {
        "objective": "traffic",
        "settings": {
            "page_id": "123456789",
            "destination_url": "https://example.com/landing",
            "countries": ["US"],
            "age_min": 18,
            "age_max": 65,
            "conversion_event": "LEAD",
            "end_time": "2026-12-31T23:59:59Z",
        },
        "creatives": [
            {
                "id": "c-custom-1",
                "storage_key": "dummy-key",
                "copy": {
                    "meta": {
                        "headline": "H"
                        * 30,  # exceeds custom 25 limit, but would be valid under default 40
                        "primary_text": "Valid primary text",
                        "description": "Valid desc",
                    }
                },
            }
        ],
    }
    # Fails under custom dynamic limits
    failures = preflight(test_doc, text_limits=custom_limits)
    assert len(failures) == 1
    assert "exceeds allowed limit of 25 characters" in failures[0]


def test_deployment_preflight_reports_character_limits(
    client, admin, brand, plan, selected_account
):
    """S4.3/S4.4: deployment_preflight endpoint checks creative copy against
    placement_spec registry limits."""
    preflight_res = client.post(f"/api/brands/{brand}/plans/{plan['id']}/preflight")
    assert preflight_res.status_code == 200, preflight_res.text
    data = preflight_res.json()
    char_check = next(
        (
            c
            for c in data["checks"]
            if c["key"] == "character_limits" and c["channel"] == "meta"
        ),
        None,
    )
    assert char_check is not None
    assert char_check["passed"] is True
    assert "character limits" in char_check["message"]
