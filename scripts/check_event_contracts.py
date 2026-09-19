"""Reject incompatible event changes unless their event_version increases."""

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REGISTRY = "src/adjutant/event_registry.json"
ANNOTATIONS = {"$schema", "$id", "title", "description", "examples", "default", "$comment"}


def numeric_bound(schema: dict, inclusive: str, exclusive: str, direction: int) -> tuple:
    candidates = []
    if inclusive in schema:
        candidates.append((direction * schema[inclusive], False))
    if exclusive in schema:
        candidates.append((direction * schema[exclusive], True))
    return max(candidates, default=(float("-inf"), False))


def schema_changes(old: Any, new: Any, path: str = "payload") -> list[str]:
    """Conservatively compare supported constraints; ambiguous changes require a version bump."""
    if old == new or new is True or old is False:
        return []
    if new is False or old is True:
        return [f"{path}: schema narrowed"]
    if "const" in old or "enum" in old:
        values = [old["const"]] if "const" in old else old["enum"]
        validator = Draft202012Validator(new)
        return (
            [f"{path}: allowed value removed"]
            if any(not validator.is_valid(value) for value in values)
            else []
        )
    errors = []
    handled = set(ANNOTATIONS)
    handled.update({"properties", "required", "type", "enum", "additionalProperties", "items"})
    old_properties, new_properties = old.get("properties", {}), new.get("properties", {})
    for name, schema in old_properties.items():
        if name not in new_properties:
            errors.append(f"{path}.{name}: field removed")
        else:
            errors.extend(schema_changes(schema, new_properties[name], f"{path}.{name}"))
    for name in set(new.get("required", [])) - set(old.get("required", [])):
        errors.append(f"{path}.{name}: new required field")
    if "type" in new:
        old_types = old.get(
            "type", ["null", "boolean", "integer", "number", "string", "array", "object"]
        )
        new_types = new["type"]
        old_types = {old_types} if isinstance(old_types, str) else set(old_types)
        new_types = {new_types} if isinstance(new_types, str) else set(new_types)
        if "number" in new_types:
            new_types.add("integer")
        if not old_types <= new_types:
            errors.append(f"{path}: type narrowed")
    if "enum" in new and (
        "enum" not in old or any(value not in new["enum"] for value in old["enum"])
    ):
        errors.append(f"{path}: enum narrowed")
    for key in ("minLength", "minItems", "minProperties"):
        handled.add(key)
        if key in new and (key not in old or new[key] > old[key]):
            errors.append(f"{path}: {key} narrowed")
    for key in ("maxLength", "maxItems", "maxProperties"):
        handled.add(key)
        if key in new and (key not in old or new[key] < old[key]):
            errors.append(f"{path}: {key} narrowed")
    handled.update({"minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum"})
    for lower, exclusive, direction in (
        ("minimum", "exclusiveMinimum", 1),
        ("maximum", "exclusiveMaximum", -1),
    ):
        if numeric_bound(new, lower, exclusive, direction) > numeric_bound(
            old, lower, exclusive, direction
        ):
            errors.append(f"{path}: numeric bound narrowed")
    if old.get("additionalProperties", True) != new.get("additionalProperties", True):
        errors.extend(
            schema_changes(
                old.get("additionalProperties", True),
                new.get("additionalProperties", True),
                f"{path}.additionalProperties",
            )
        )
    if "items" in old or "items" in new:
        errors.extend(schema_changes(old.get("items", True), new.get("items", True), f"{path}[]"))
    for key in (set(old) | set(new)) - handled:
        if key in {"const", "pattern", "format", "multipleOf", "uniqueItems"} and key not in new:
            continue
        if old.get(key) != new.get(key):
            errors.append(f"{path}: changed constraint {key} requires a new version")
    return errors


def check_registry(previous: dict, current: dict) -> list[str]:
    """Validate all schemas and compare every previously published event contract."""
    Draft202012Validator.check_schema(current["envelope_schema"])
    events = {}
    for event in current["events"]:
        name = event["event_type"]
        if name in events:
            raise ValueError(f"Duplicate event contract: {name}")
        Draft202012Validator.check_schema(event["payload_schema"])
        events[name] = event
    errors = []
    for event in previous["events"]:
        name = event["event_type"]
        replacement = events.get(name)
        if replacement is None:
            errors.append(f"{name}: event removed")
            continue
        old_version, new_version = (
            event.get("event_version", 1),
            replacement.get("event_version", 1),
        )
        if new_version < old_version:
            errors.append(f"{name}: event version decreased")
        if new_version > old_version:
            continue
        errors.extend(schema_changes(event["payload_schema"], replacement["payload_schema"], name))
        if event["topic"] != replacement["topic"]:
            errors.append(f"{name}: topic changed without a new version")
    errors.extend(
        schema_changes(previous["envelope_schema"], current["envelope_schema"], "envelope")
    )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, help="Git revision to compare with the working tree"
    )
    args = parser.parse_args()
    current = json.loads(Path(REGISTRY).read_text(encoding="utf-8"))
    if set(args.base) == {"0"}:
        previous = current
    else:
        result = subprocess.run(
            ["git", "show", f"{args.base}:{REGISTRY}"],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            parser.exit(1, "Cannot read the base event registry; comparison did not run.\n")
        previous = json.loads(result.stdout.decode("utf-8"))
    errors = check_registry(previous, current)
    if errors:
        parser.exit(1, "Event compatibility failed:\n" + "\n".join(errors) + "\n")
    print(f"Validated {len(current['events'])} event contracts and backward compatibility.")


if __name__ == "__main__":
    main()
