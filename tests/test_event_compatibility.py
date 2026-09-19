from copy import deepcopy

import pytest
from check_event_contracts import check_registry, schema_changes


@pytest.fixture
def registry():
    return {
        "envelope_schema": {"type": "object"},
        "events": [
            {
                "event_type": "test.changed",
                "event_version": 1,
                "topic": "test.v1",
                "payload_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "count": {"type": "number", "minimum": 0},
                    },
                    "required": ["name"],
                },
            }
        ],
    }


def test_optional_field_passes_but_required_field_fails(registry):
    new = deepcopy(registry)
    schema = new["events"][0]["payload_schema"]
    schema["properties"]["optional"] = {"type": "string"}
    assert check_registry(registry, new) == []
    schema["required"].append("optional")
    assert any("new required" in error for error in check_registry(registry, new))


def test_removed_field_requires_version_bump(registry):
    new = deepcopy(registry)
    del new["events"][0]["payload_schema"]["properties"]["count"]
    assert any("field removed" in error for error in check_registry(registry, new))
    new["events"][0]["event_version"] = 2
    assert check_registry(registry, new) == []


@pytest.mark.parametrize(
    "old,new",
    [
        ({"type": "number"}, {"type": "integer"}),
        ({"enum": ["a", "b"]}, {"enum": ["a"]}),
        ({"minimum": 0}, {"exclusiveMinimum": 0}),
        ({"maximum": 5}, {"exclusiveMaximum": 5}),
        ({"items": {"type": "number"}}, {"items": {"type": "string"}}),
        ({}, {"pattern": "^[a-z]+$"}),
        ({"additionalProperties": True}, {"additionalProperties": False}),
    ],
)
def test_narrowing_is_rejected(old, new):
    assert schema_changes(old, new)


@pytest.mark.parametrize(
    "old,new",
    [
        ({"type": "integer"}, {"type": "number"}),
        ({"const": 1}, {"type": "integer", "minimum": 1}),
        ({"enum": ["a", "b"]}, {"type": "string"}),
        ({"exclusiveMinimum": 0}, {"minimum": 0}),
        ({"exclusiveMaximum": 5}, {"maximum": 5}),
        ({"uniqueItems": True}, {}),
    ],
)
def test_widening_preserves_old_messages(old, new):
    assert schema_changes(old, new) == []


def test_event_removal_and_topic_changes_fail(registry):
    assert check_registry(registry, {**registry, "events": []})
    new = deepcopy(registry)
    new["events"][0]["topic"] = "different"
    assert check_registry(registry, new)
