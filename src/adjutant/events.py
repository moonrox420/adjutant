import json
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from psycopg import Connection
from psycopg.types.json import Jsonb


def event_uuid() -> UUID:
    """UUIDv7 with a millisecond timestamp and 74 random bits (Python 3.12 compatible)."""
    value = (time.time_ns() // 1_000_000) << 80
    value |= 7 << 76 | secrets.randbits(12) << 64 | 2 << 62 | secrets.randbits(62)
    return UUID(int=value)


class EventRegistry:
    """Validate every outbox write against the supplied shared event registry."""

    def __init__(self, path: Path) -> None:
        self.document = json.loads(path.read_text(encoding="utf-8"))
        self.events = {e["event_type"]: e for e in self.document["events"]}
        if len(self.events) != len(self.document["events"]):
            raise ValueError("Duplicate event definitions are not permitted")
        Draft202012Validator.check_schema(self.document["envelope_schema"])
        for event in self.events.values():
            Draft202012Validator.check_schema(event["payload_schema"])
        self.envelope = Draft202012Validator(
            self.document["envelope_schema"], format_checker=FormatChecker()
        )
        self.payloads = {
            name: Draft202012Validator(event["payload_schema"], format_checker=FormatChecker())
            for name, event in self.events.items()
        }

    def append(
        self,
        conn: Connection[Any],
        event_type: str,
        brand_id: UUID,
        payload: dict[str, Any],
    ) -> UUID:
        self.payloads[event_type].validate(payload)
        now = datetime.now(UTC)
        event_id = event_uuid()
        envelope = {
            "event_id": str(event_id),
            "event_type": event_type,
            "event_version": self.events[event_type].get("event_version", 1),
            "occurred_at": now.isoformat(),
            "produced_at": now.isoformat(),
            "producer": "core-api@0.1.0",
            "brand_id": str(brand_id),
            "payload": payload,
        }
        self.envelope.validate(envelope)
        conn.execute(
            """INSERT INTO event_outbox(event_id,brand_id,event_type,topic,partition_key,
                     envelope,occurred_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (
                event_id,
                brand_id,
                event_type,
                self.events[event_type]["topic"],
                str(brand_id),
                Jsonb(envelope),
                now,
            ),
        )
        return event_id
