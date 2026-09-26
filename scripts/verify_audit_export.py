"""Verify a downloaded audit export against an independently trusted public-key ring."""

import argparse
import base64
import json
from pathlib import Path

from adjutant.audit_export import verify_export
from adjutant.errors import DomainError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--public-keys", type=Path, required=True)
    args = parser.parse_args()
    try:
        bundle = json.loads(args.export.read_text(encoding="utf-8"))
        keys = {
            name: base64.b64decode(value, validate=True)
            for name, value in json.loads(
                args.public_keys.read_text(encoding="utf-8")
            ).items()
        }
        verify_export(bundle, keys)
    except (OSError, ValueError, AttributeError, DomainError) as exc:
        parser.exit(1, f"Audit verification failed: {type(exc).__name__}\n")
    print(f"Verified {bundle['manifest']['entry_count']} signed audit records.")


if __name__ == "__main__":
    main()
