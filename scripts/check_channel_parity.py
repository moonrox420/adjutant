"""Reject channel-specific branching above Python's provider adapter layer."""

import ast
from pathlib import Path
from typing import get_args

from adjutant.models import Channel

CHANNELS = frozenset(get_args(Channel))


def violations(source: str, filename: str) -> list[str]:
    """Match direct comparisons, membership lists, named constants, and match cases."""
    tree = ast.parse(source, filename=filename)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str) and node.value.value in CHANNELS:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = node.value.value

    def channel_value(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str) and node.value in CHANNELS
        if isinstance(node, ast.Name):
            return node.id in aliases
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(channel_value(value) for value in node.elts)
        return False

    errors = []
    for node in ast.walk(tree):
        values = []
        if isinstance(node, ast.Compare):
            values = [node.left, *node.comparators]
        elif isinstance(node, ast.MatchValue):
            values = [node.value]
        if any(channel_value(value) for value in values):
            lineno = getattr(node, "lineno", 1)
            errors.append(f"{filename}:{lineno}: channel-specific branching belongs in adapters/")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures = []
    for path in sorted((root / "src/adjutant").rglob("*.py")):
        relative = path.relative_to(root)
        if "adapters" not in relative.parts:
            failures.extend(violations(path.read_text(encoding="utf-8"), str(relative)))
    for failure in failures:
        print(failure)
    if not failures:
        print("Python channel parity: no channel-specific branches above adapters.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
