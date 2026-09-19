"""One inference process. Its private parent pipe is also its lifetime lease."""

import json
import os
import sys
import threading

from adjutant.errors import DomainError
from adjutant.generation import OllamaPlanner


def watch_parent() -> None:
    """Exit if the supervising API dies; no detached inference worker survives pipe EOF."""
    while os.read(sys.stdin.fileno(), 1):
        continue
    os._exit(70)


def main() -> None:
    data = json.loads(sys.stdin.buffer.readline(1024 * 1024))
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        result = OllamaPlanner(
            data["url"], provider=data.get("provider", "local"), api_key=data.get("api_key", "")
        ).generate(data["model"], data["context"])
        output = {
            "plan": result.plan.model_dump(mode="json"),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "attempts": result.attempts,
        }
    except DomainError as exc:
        output = {"error": {"code": exc.code, "message": exc.message, "status": exc.status}}
    sys.stdout.write(json.dumps(output))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
