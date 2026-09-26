"""Cancellable, parent-owned Studio copy inference without database credentials."""

import asyncio
import json
import os
import sys
import tempfile
import threading

from pydantic import ValidationError

from adjutant.config import Settings
from adjutant.creative_concepts import distinct_concept
from adjutant.errors import DomainError
from adjutant.generation import OllamaPlanner
from adjutant.generation_worker import watch_parent
from adjutant.processes import launch, terminate_owned
from adjutant.studio_models import AdCopyBundle


async def generate_distinct_copy(
    config: Settings, context: dict, previous: list[dict]
) -> AdCopyBundle:
    """Repair repeated concepts before image generation, with bounded cancellable inference."""
    current = dict(context)
    for attempt in range(3):
        bundle = await generate_copy(config, current)
        try:
            distinct_concept(bundle, previous)
        except DomainError:
            if attempt == 2:
                raise
            current["creative_repair"] = {
                "rejected_headline": bundle.meta.headline,
                "rejected_image_prompt": bundle.meta.image_prompt,
                "instruction": "The last answer repeated a prior concept. Write a new headline "
                "and a substantially different scene with a different central subject, camera "
                "composition, and setting. Follow this concept's specific creative direction.",
            }
        else:
            return bundle
    raise RuntimeError("Concept repair exhausted without returning or raising")


async def generate_copy(config: Settings, context: dict) -> AdCopyBundle:
    """Terminate the inference child on cancellation and verify its exit before returning."""
    with tempfile.TemporaryFile() as output:
        child = launch("adjutant.studio_worker", output)
        try:
            data = {
                "url": config.ollama_url,
                "model": config.ollama_model,
                "context": context,
            }
            if child.stdin:
                child.stdin.write((json.dumps(data, default=str) + "\n").encode())
                child.stdin.flush()
            async with asyncio.timeout(250):
                while child.poll() is None:
                    if os.fstat(output.fileno()).st_size > 1024 * 1024:
                        raise DomainError(
                            "GenerationInvalid",
                            "Copy output exceeded its size limit.",
                            422,
                        )
                    await asyncio.sleep(0.15)
            if child.returncode != 0:
                raise DomainError(
                    "GenerationWorkerFailed", "Studio inference process failed.", 503
                )
            output.seek(0)
            value = json.loads(output.read(1024 * 1024))
            if "error" in value:
                error = value["error"]
                raise DomainError(error["code"], error["message"], error["status"])
            return AdCopyBundle.model_validate(value["copy"])
        except TimeoutError as exc:
            raise DomainError(
                "GenerationTimeout", "Studio inference exceeded its time limit.", 504
            ) from exc
        except (ValueError, ValidationError, KeyError) as exc:
            raise DomainError(
                "GenerationInvalid", "Studio inference returned invalid output.", 502
            ) from exc
        finally:
            terminate_owned(child)
            if child.stdin:
                child.stdin.close()


def main() -> None:
    from adjutant.campaign_api import COPY_INSTRUCTIONS

    data = json.loads(sys.stdin.buffer.readline(1024 * 1024))
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        result, _, _, _ = OllamaPlanner(data["url"]).generate_document(
            data["model"],
            data["context"],
            AdCopyBundle,
            COPY_INSTRUCTIONS,
            max_attempts=2,
            timeout_seconds=120,
            schema_constrained=False,
        )
        output = {"copy": result.model_dump()}
    except DomainError as exc:
        output = {
            "error": {"code": exc.code, "message": exc.message, "status": exc.status}
        }
    sys.stdout.write(json.dumps(output))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
