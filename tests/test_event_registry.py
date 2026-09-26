import ast
import json
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, ValidationError

from adjutant.config import Settings
from adjutant.events import EventRegistry


def test_bundled_registry_covers_every_current_producer(tmp_path, monkeypatch):
    resource = files("adjutant").joinpath("event_registry.json")
    document = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(document["envelope_schema"])
    definitions = {event["event_type"] for event in document["events"]}
    assert len(definitions) == len(document["events"])
    for event in document["events"]:
        Draft202012Validator.check_schema(event["payload_schema"])
    emitted = set()
    for path in (Path(__file__).resolve().parents[1] / "src/adjutant").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "events"
                and node.func.attr == "append"
            ):
                assert isinstance(
                    node.args[1], ast.Constant
                ), "Dynamic event names need contract coverage"
                emitted.add(node.args[1].value)
    assert emitted <= definitions
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, database_url="postgresql://unused")
    assert set(EventRegistry(settings.registry_path).events) == definitions


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("brand_id", "not-a-uuid"),
        ("subject_hash", "changed"),
        ("usd_daily_cap", -1),
        ("expires_at", "not-a-date"),
        ("signature", "must-not-enter-the-outbox"),
    ],
)
def test_runtime_event_contract_rejects_malformed_authority(bad_field, bad_value):
    registry = EventRegistry(
        Settings(_env_file=None, database_url="postgresql://unused").registry_path
    )
    payload = {
        "brand_id": str(uuid4()),
        "token_id": str(uuid4()),
        "approval_request_id": str(uuid4()),
        "subject_type": "plan",
        "subject_id": str(uuid4()),
        "subject_hash": "a" * 64,
        "scopes": ["channel:meta", "op:create"],
        "usd_daily_cap": 100,
        "usd_total_cap": 3000,
        "expires_at": "2026-09-19T12:00:00Z",
    }
    registry.payloads["approval.token.issued"].validate(payload)
    with pytest.raises(ValidationError):
        registry.payloads["approval.token.issued"].validate(
            {**payload, bad_field: bad_value}
        )
