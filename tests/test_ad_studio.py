"""Ad Studio integration tests; provider doubles are restricted to test boundaries."""

import asyncio
import base64
import io
import json
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from google import genai
from google.genai import types
from PIL import Image

from adjutant.errors import DomainError
from adjutant.generation import OllamaPlanner
from adjutant.rendering import SIZES, render_ad
from adjutant.research import WebsiteEvidence
from adjutant.services.visuals import VisualsGenerator
from adjutant.storage import ObjectStore
from adjutant.studio_jobs import StudioJobRunner
from adjutant.studio_models import AdCopyBundle

_image_buffer = io.BytesIO()
Image.new("RGB", (64, 64), "#90adbc").save(_image_buffer, format="PNG")
PNG = _image_buffer.getvalue()
IMAGE = "data:image/png;base64," + base64.b64encode(PNG).decode()


def test_finished_renditions_keep_text_and_reject_overflow(copy_bundle):
    document = {**copy_bundle.model_dump(), "brand_name": "Test Plumbing"}
    for ratio, size in SIZES.items():
        result = render_ad(document, PNG, ratio)
        with Image.open(io.BytesIO(result.png)) as image:
            assert image.size == size
        assert b"<text " in result.svg
        assert document["meta"]["headline"].encode() in result.svg
        assert result.scene["text_contrast_ratio"] >= 4.5
        for layer in result.scene["layers"][1:]:
            assert layer["y"] + layer["height"] <= size[1] - 64
    document["meta"]["headline"] = "Overflow " * 200
    with pytest.raises(DomainError, match="safe area"):
        render_ad(document, PNG, "1:1")


def test_provider_configuration_is_encrypted_and_applied(
    client, brand, admin, providers
):
    key = "private-google-key-never-return"
    result = client.put(
        f"/api/brands/{brand}/visual-provider",
        json={"api_key": key, "model": "gemini-2.5-flash-image"},
    )
    assert result.status_code == 200, result.text
    assert key not in result.text
    stored = admin.execute(
        "SELECT ciphertext FROM tenant_secret WHERE brand_id=%s AND name='visual_provider'",
        (brand,),
    ).fetchone()
    assert key.encode() not in bytes(stored["ciphertext"])
    assert (
        client.get(f"/api/brands/{brand}/visual-provider").json()["credentials_saved"]
        is True
    )
    response = generate(client, brand)
    assert response.status_code == 201, response.text
    assert response.json()["image_model"] == "gemini-2.5-flash-image"


def test_understanding_edits_are_versioned_and_used(client, brand, providers, admin):
    original = generate(client, brand).json()
    context = client.get(f"/api/brands/{brand}/understanding").json()
    edited = {**context["document"], "voice": "Friendly and precise"}
    response = client.put(
        f"/api/brands/{brand}/understanding",
        json={"expected_version": 1, "document": edited},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    stale = client.put(
        f"/api/brands/{brand}/understanding",
        json={"expected_version": 1, "document": edited},
    )
    assert stale.status_code == 409
    assert generate(client, brand).status_code == 201
    assert (
        providers[0].call_args.args[1]["brand_understanding"]["voice"]
        == edited["voice"]
    )
    assert (
        admin.execute(
            "SELECT document FROM brand_context WHERE brand_id=%s AND version=1",
            (brand,),
        ).fetchone()["document"]
        == original["understanding"]
    )


def test_render_download_attach_and_audit_are_connected(
    client, brand, providers, plan_input, admin
):
    bundle = generate(client, brand).json()
    endpoint = f"/api/brands/{brand}/studio/{bundle['id']}"
    result = client.post(
        endpoint + "/render", json={"expected_revision": 1, "aspect_ratio": "1:1"}
    )
    assert result.status_code == 201, result.text
    rendition = result.json()
    assert client.get(rendition["png_url"]).content.startswith(b"\x89PNG")
    assert b"<text " in client.get(rendition["svg_url"]).content
    plan = client.post(f"/api/brands/{brand}/plans", json=plan_input).json()
    attachment = client.post(
        endpoint + "/attach", json={"expected_revision": 1, "plan_id": plan["id"]}
    )
    assert attachment.status_code == 201, attachment.text
    repeated = client.post(
        endpoint + "/attach", json={"expected_revision": 1, "plan_id": plan["id"]}
    )
    assert repeated.json() == attachment.json()
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM studio_rendition WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 4
    )
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM creative WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 1
    )
    preflight = client.post(f"/api/brands/{brand}/plans/{plan['id']}/preflight").json()
    assert (
        next(
            check
            for check in preflight["checks"]
            if check["key"] == "creative_attached"
        )["passed"]
        is True
    )
    assert next(
        check for check in preflight["checks"] if check["key"] == "creative_approval"
    )["passed"]
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM rendition WHERE brand_id=%s AND spec_validation_passed",
            (brand,),
        ).fetchone()["n"]
        == 1
    )
    assert not preflight["ready"]
    assert {
        item["action_type"]
        for item in client.get(f"/api/brands/{brand}/workspace").json()["audit"]
    } >= {"studio_generate", "creative_render"}
    assert (
        client.get(rendition["png_url"].replace(brand, str(uuid4()))).status_code == 404
    )
    assert (
        client.post(
            endpoint + "/render", json={"expected_revision": 99, "aspect_ratio": "1:1"}
        ).status_code
        == 409
    )


def test_local_stop_can_be_resumed_without_restoring_tokens(client, brand, providers):
    assert (
        client.post(
            f"/api/brands/{brand}/kill",
            json={"reason": "Stop while checking the brand setup."},
        ).status_code
        == 200
    )
    assert generate(client, brand).status_code == 409
    response = client.post(
        f"/api/brands/{brand}/resume",
        json={"reason": "Resume after checking the brand setup."},
    )
    assert response.status_code == 200, response.text
    assert generate(client, brand).status_code == 201


@pytest.fixture(autouse=True)
def provider_configuration(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-credential")
    monkeypatch.setenv("ADJUTANT_OLLAMA_MODEL", "studio-test-model")


@pytest.fixture
def copy_bundle():
    return AdCopyBundle.model_validate(
        {
            "understanding": {
                "offers": ["Residential plumbing repairs"],
                "audience": "Local homeowners",
                "voice": "Calm and practical",
                "proof_points": [],
            },
            "meta": {
                "headline": "Plumbing help for your home",
                "primary_text": "Get your plumbing back on track. Talk with our repair team.",
                "description": "Residential plumbing repairs.",
                "cta": "Contact us",
                "image_prompt": "A plumber repairing a sink in a bright kitchen, no text.",
            },
            "google": {
                "headlines": [
                    "Home Plumbing Repairs",
                    "Talk With Our Repair Team",
                    "Local Plumbing Help",
                ],
                "descriptions": [
                    "Get help with residential plumbing repairs.",
                    "Contact our team to discuss the repairs your home needs.",
                ],
                "destination_path": "plumbing/repairs",
            },
            "tiktok": {
                "hook": "That drip isn't fixing itself.",
                "visual_script": "A dripping kitchen tap, then a plumber inspecting the fitting.",
                "cta": "Talk to our repair team",
            },
        }
    )


@pytest.fixture
def providers(copy_bundle):
    with (
        patch.object(
            OllamaPlanner, "generate_document", return_value=(copy_bundle, 10, 20, 1)
        ) as copy,
        patch.object(
            VisualsGenerator,
            "generate_ad_image",
            new_callable=AsyncMock,
            return_value=IMAGE,
        ) as image,
    ):
        yield copy, image


def generate(client, brand, value="Create ads for our residential plumbing repairs"):
    return client.post(
        "/api/campaigns/generate-quick",
        json={"brand_id": brand, "url_or_prompt": value},
    )


def enqueue_studio(client, brand, request_key=None):
    client.app.state.config.workflow_enabled = True
    return client.post(
        "/api/studio/jobs",
        json={
            "brand_id": brand,
            "url_or_prompt": "Create plumbing repair ads",
            "request_key": str(request_key or uuid4()),
            "concept_count": 1,
        },
    )


def job_runner(client):
    config = client.app.state.config
    return StudioJobRunner(
        client.app.state.db, config, ObjectStore(config.object_store_path)
    )


def test_studio_disabled_worker_rejects_queue_without_persisting_job(
    client, brand, admin
):
    response = client.post(
        "/api/studio/jobs",
        json={
            "brand_id": brand,
            "url_or_prompt": "Plumbing ads",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "StudioWorkerDisabled"
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM studio_job WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 0
    )


def test_studio_durable_job_recovers_checkpoint_without_repeating_copy(
    client, brand, copy_bundle, monkeypatch, admin
):
    copy = AsyncMock(return_value=copy_bundle)
    image = AsyncMock(side_effect=[asyncio.CancelledError(), IMAGE])
    monkeypatch.setattr("adjutant.studio_worker.generate_copy", copy)
    monkeypatch.setattr(VisualsGenerator, "generate_ad_image", image)
    key = uuid4()
    response = enqueue_studio(client, brand, key)
    assert response.status_code == 202, response.text
    job = response.json()
    assert enqueue_studio(client, brand, key).json()["id"] == job["id"]
    assert enqueue_studio(client, brand).status_code == 429
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    row = admin.execute("SELECT * FROM studio_job WHERE id=%s", (job["id"],)).fetchone()
    assert row["state"] == "queued"
    checkpoint = admin.execute(
        "SELECT work_checkpoint FROM studio_draft WHERE id=%s", (job["id"],)
    ).fetchone()
    assert (
        checkpoint["work_checkpoint"]["copy"]["meta"]["headline"]
        == copy_bundle.meta.headline
    )
    asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    result = client.get(f"/api/brands/{brand}/studio/jobs/{job['id']}").json()
    assert result["state"] == "completed", result
    assert result["attempts"] == 2
    assert result["result"]["meta"]["image_url"] == IMAGE
    assert copy.await_count == 1
    assert "session_hash" not in result
    assert (
        "session_hash"
        not in client.get(f"/api/brands/{brand}/studio/jobs/latest").json()
    )
    finished = client.delete(f"/api/brands/{brand}/studio/jobs/{job['id']}").json()
    assert finished["state"] == "completed" and finished["cancel_requested_at"] is None
    job_runner(client).finish(UUID(brand), UUID(job["id"]), "cancelled")
    assert (
        client.get(f"/api/brands/{brand}/studio/jobs/{job['id']}").json()["state"]
        == "completed"
    )
    listed = next(
        row for row in client.get("/api/jobs").json() if row["id"] == job["id"]
    )
    assert listed["kind"] == "studio" and listed["state"] == "completed"
    assert "session_hash" not in listed


def test_studio_model_change_rejects_queued_job_before_provider_call(
    client, brand, monkeypatch
):
    job = enqueue_studio(client, brand).json()
    response = client.put(
        f"/api/brands/{brand}/visual-provider",
        json={
            "api_key": "private-key-for-model-change",
            "model": "gemini-2.5-flash-image",
        },
    )
    assert response.status_code == 200
    copy = AsyncMock()
    monkeypatch.setattr("adjutant.studio_worker.generate_copy", copy)
    asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    result = client.get(f"/api/brands/{brand}/studio/jobs/{job['id']}").json()
    assert result["state"] == "failed" and result["error_code"] == "ImageModelChanged"
    copy.assert_not_awaited()


def test_studio_job_cancels_before_provider_calls_and_reports_activity(
    client, brand, admin
):
    job = enqueue_studio(client, brand).json()
    prefix = f"/api/brands/{brand}/studio/jobs/{job['id']}"
    assert client.delete(prefix).status_code == 202
    asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    result = client.get(prefix).json()
    assert result["state"] == "cancelled", result
    assert result["error_code"] == "GenerationCancelled"
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM action WHERE target_id=%s AND diff->>'state'='cancelled'",
            (job["id"],),
        ).fetchone()["n"]
        == 1
    )


def test_studio_scheduler_executes_queued_job(client, brand, copy_bundle, monkeypatch):
    monkeypatch.setattr(
        "adjutant.studio_worker.generate_copy", AsyncMock(return_value=copy_bundle)
    )
    monkeypatch.setattr(
        VisualsGenerator, "generate_ad_image", AsyncMock(return_value=IMAGE)
    )
    job = enqueue_studio(client, brand).json()

    async def run():
        runner = job_runner(client)
        runner.start()
        try:
            async with asyncio.timeout(5):
                while True:
                    await asyncio.sleep(0.1)
                    status = client.get(
                        f"/api/brands/{brand}/studio/jobs/{job['id']}"
                    ).json()
                    if status["state"] == "completed":
                        return
                    assert status["state"] in {"queued", "running"}, status
        finally:
            await runner.close()

    asyncio.run(run())


def test_studio_inference_child_is_reaped_on_cancellation(client, monkeypatch):
    from adjutant import studio_worker

    children = []
    original = studio_worker.launch

    def launch(module, output):
        process = original(module, output)
        children.append(process)
        return process

    monkeypatch.setattr(studio_worker, "launch", launch)

    async def run():
        task = asyncio.create_task(
            studio_worker.generate_copy(client.app.state.config, {})
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(children) == 1
    assert children[0].poll() is not None


@pytest.mark.parametrize("operation", ["logout", "logout-all", "kill"])
def test_session_revocation_and_kill_wait_for_studio_cancellation(
    client, brand, monkeypatch, admin, operation
):
    entered = asyncio.Event()
    terminated = asyncio.Event()

    async def copy(config, context):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            terminated.set()

    monkeypatch.setattr("adjutant.studio_worker.generate_copy", copy)
    job = enqueue_studio(client, brand).json()

    async def exercise():
        runner = job_runner(client)
        task = asyncio.create_task(runner.run_job(UUID(brand), UUID(job["id"])))
        try:
            async with asyncio.timeout(10):
                await entered.wait()
                if operation == "kill":
                    result = await asyncio.to_thread(
                        client.post,
                        f"/api/brands/{brand}/kill",
                        json={"reason": "Stop Studio work"},
                    )
                else:
                    result = await asyncio.to_thread(
                        client.post, f"/api/auth/{operation}"
                    )
                assert result.status_code == 200, result.text
                assert result.json()["cancellation_verified"] is True
                assert any(row["id"] == job["id"] for row in result.json()["jobs"])
                assert terminated.is_set()
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    row = admin.execute("SELECT * FROM studio_job WHERE id=%s", (job["id"],)).fetchone()
    assert row["state"] == "cancelled" and row["finished_at"] is not None
    assert row["cancel_requested_at"] is not None


def test_url_ingest_real_bundle_persists_without_confirmation(
    client, brand, providers, admin
):
    with patch(
        "adjutant.campaign_api.fetch_website",
        return_value=WebsiteEvidence(
            "https://example.com/services",
            "Plumbing services",
            "Residential plumbing repairs for local homeowners.",
            "a" * 64,
        ),
    ) as ingest:
        result = generate(client, brand, "https://example.com/services")
    assert result.status_code == 201, result.text
    bundle = result.json()
    assert bundle["meta"]["image_url"] == IMAGE
    assert bundle["google"]["headlines"] and bundle["tiktok"]["hook"]
    ingest.assert_called_once()
    assert "Residential plumbing" in providers[0].call_args.args[1]["business_context"]
    providers[1].assert_awaited_once_with(
        bundle["meta"]["image_prompt"], aspect_ratio="1:1"
    )
    assert client.get(f"/api/brands/{brand}/studio/latest").json() == bundle
    context = admin.execute(
        "SELECT * FROM brand_context WHERE brand_id=%s", (brand,)
    ).fetchone()
    assert context["accepted_automatically_at"] is not None and context["version"] == 1
    layers = bundle["scene_graph"]["layers"]
    assert layers[0]["type"] == "image" and "content" not in layers[0]
    assert all(layer["type"] == "text" for layer in layers[1:])
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM approval_request WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 0
    )


def test_prompt_ingest_reuses_context_and_edits_persist(
    client, brand, providers, admin
):
    first = generate(client, brand).json()
    second = generate(client, brand).json()
    assert first["context_version"] == second["context_version"] == 1
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM brand_context WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 1
    )
    meta = {k: v for k, v in second["meta"].items() if k != "image_url"}
    body = {
        "expected_revision": 1,
        "brand_name": "Edited business",
        "destination_url": "https://example.com/repairs",
        "meta": {**meta, "headline": "A clearer headline"},
        "google": second["google"],
        "tiktok": {**second["tiktok"], "hook": "An edited opening hook."},
    }
    edited = client.put(f"/api/brands/{brand}/studio/{second['id']}", json=body)
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 2
    assert edited.json()["meta"]["image_url"] == second["meta"]["image_url"]
    assert (
        client.get(f"/api/brands/{brand}/studio/latest").json()["tiktok"]["hook"]
        == body["tiktok"]["hook"]
    )
    assert (
        client.put(f"/api/brands/{brand}/studio/{second['id']}", json=body).status_code
        == 409
    )


def test_image_failure_returns_no_placeholder_and_records_failure(
    client, brand, providers, admin
):
    providers[1].side_effect = DomainError(
        "ImageGenerationBlocked", "Provider blocked the image", 422
    )
    result = generate(client, brand)
    assert result.status_code == 422 and "image_url" not in result.json()
    assert client.get(f"/api/brands/{brand}/studio/latest").json() is None
    row = admin.execute(
        "SELECT state,error_code FROM studio_draft WHERE brand_id=%s", (brand,)
    ).fetchone()
    assert row == {"state": "failed", "error_code": "ImageGenerationBlocked"}


def test_blocked_claim_stops_before_visual_generation(client, brand, providers, admin):
    admin.execute(
        "INSERT INTO brand_constraint(brand_id,kind,value) "
        "VALUES(%s,'banned_claim','PLUMBING HELP')",
        (brand,),
    )
    result = generate(client, brand)
    assert (
        result.status_code == 422 and result.json()["error"]["code"] == "BlockedClaim"
    )
    providers[1].assert_not_awaited()


def test_foreign_tenant_and_viewer_cannot_generate(
    client, brand, providers, admin, identity
):
    result = generate(client, str(uuid4()))
    assert result.status_code == 404
    admin.execute(
        "UPDATE seat SET role='client_viewer',brand_id=%s WHERE user_id=%s",
        (brand, identity["user"]),
    )
    assert generate(client, brand).status_code == 403
    providers[0].assert_not_called()
    assert client.post("/api/auth/logout").status_code == 200
    assert generate(client, brand).status_code == 401


def test_logout_during_inference_prevents_image_call_and_save(
    client, brand, providers, admin
):
    def revoke(*args, **kwargs):
        assert client.post("/api/auth/logout").status_code == 200
        return providers[0].return_value

    providers[0].side_effect = revoke
    assert generate(client, brand).status_code == 401
    providers[1].assert_not_awaited()
    assert (
        admin.execute(
            "SELECT state FROM studio_draft WHERE brand_id=%s", (brand,)
        ).fetchone()["state"]
        == "failed"
    )


def test_unsafe_url_rejected_before_inference(client, brand, providers):
    result = generate(client, brand, "http://127.0.0.1/private")
    assert (
        result.status_code == 422 and result.json()["error"]["code"] == "UnsafeWebsite"
    )
    providers[0].assert_not_called()


def test_copywriter_http_contract_uses_supplied_context(copy_bundle):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, json={"message": {"content": copy_bundle.model_dump_json()}}
        )

    with (
        patch.object(OllamaPlanner, "models", return_value=["test-model"]),
        patch(
            "adjutant.generation.httpx.Client",
            return_value=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
    ):
        bundle, _, _, _ = OllamaPlanner("http://localhost:11434").generate_document(
            "test-model",
            {"business_context": "Residential repairs"},
            AdCopyBundle,
            "Write ads.",
            schema_constrained=False,
        )
    assert bundle == copy_bundle
    assert "Residential repairs" in captured[0]["messages"][1]["content"]
    assert captured[0]["format"] == "json"
    assert '"understanding"' in captured[0]["messages"][0]["content"]


@pytest.mark.parametrize("ratio", ["1:1", "4:5", "9:16", "16:9"])
def test_supported_gemini_sdk_request_and_real_bytes(ratio):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "data": base64.b64encode(PNG).decode(),
                                        "mimeType": "image/png",
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    sdk = genai.Client(
        api_key="test-key",
        http_options=types.HttpOptions(
            httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ),
    )
    with patch("adjutant.services.visuals.genai.Client", return_value=sdk):
        result = asyncio.run(
            VisualsGenerator("test-key").generate_ad_image(
                "A kitchen with a dripping tap", ratio
            )
        )
    assert base64.b64decode(result.split(",")[1]) == PNG
    assert "gemini-3.1-flash-image" in str(calls[0].url)
    payload = json.loads(calls[0].content)
    assert payload["generationConfig"]["imageConfig"]["aspectRatio"] == ratio
    assert "BACKGROUND IMAGE ONLY" in payload["contents"][0]["parts"][0]["text"]


def test_visual_missing_key_and_invalid_ratio_fail_without_request():
    with pytest.raises(DomainError, match="GEMINI_API_KEY"):
        asyncio.run(VisualsGenerator("").generate_ad_image("A kitchen"))
    with pytest.raises(DomainError, match="aspect ratio"):
        asyncio.run(VisualsGenerator("test-key").generate_ad_image("A kitchen", "7:2"))


def test_google_retired_model_error_is_actionable():
    def handler(request):
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": 404,
                    "message": "retired model",
                    "status": "NOT_FOUND",
                }
            },
        )

    sdk = genai.Client(
        api_key="test-key",
        http_options=types.HttpOptions(
            httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ),
    )
    with (
        patch("adjutant.services.visuals.genai.Client", return_value=sdk),
        pytest.raises(DomainError) as error,
    ):
        asyncio.run(VisualsGenerator("test-key").generate_ad_image("A bright kitchen"))
    assert error.value.code == "ImageModelUnavailable"


def test_corrupt_image_and_retired_model_fail_before_persistence():
    invalid_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jBv0AAAAASUVORK5CYII="
    )
    with pytest.raises(DomainError) as error:
        VisualsGenerator.data_uri(invalid_png, "image/png")
    assert error.value.code == "InvalidGeneratedImage"
    with pytest.raises(DomainError) as error:
        VisualsGenerator(
            "test-key", model="imagen-3.0-generate-002"
        ).require_configuration()
    assert error.value.code == "UnsupportedImageModel"


def test_five_concepts_resume_and_render_all_ratios(
    client, brand, copy_bundle, monkeypatch, admin
):
    messages = [
        (
            "Stop the drip",
            "Leaking faucet beneath dark cabinets and a puddle on tiles.",
        ),
        (
            "Enjoy your kitchen again",
            "Family preparing dinner around a sunny island with fruit.",
        ),
        (
            "A closer look at repairs",
            "Macro photography of wrench tightening a copper pipe joint.",
        ),
        (
            "Getting ready for guests?",
            "Holiday table set with dishes next to a modern dishwasher.",
        ),
        (
            "Explore home plumbing services",
            "Neatly arranged professional toolbox photographed overhead.",
        ),
    ]
    bundles = []
    for headline, prompt in messages:
        value = copy_bundle.model_copy(deep=True)
        value.meta.headline = headline
        value.meta.image_prompt = prompt
        bundles.append(value)
    copy = AsyncMock(side_effect=bundles)
    image = AsyncMock(
        side_effect=[IMAGE, IMAGE, asyncio.CancelledError(), IMAGE, IMAGE, IMAGE]
    )
    monkeypatch.setattr("adjutant.studio_worker.generate_copy", copy)
    monkeypatch.setattr(VisualsGenerator, "generate_ad_image", image)
    client.app.state.config.workflow_enabled = True
    queued = client.post(
        "/api/studio/jobs",
        json={
            "brand_id": brand,
            "url_or_prompt": "Residential plumbing",
            "request_key": str(uuid4()),
        },
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert job["concept_count"] == 5
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM studio_draft WHERE job_id=%s AND state='completed'",
            (job["id"],),
        ).fetchone()["n"]
        == 2
    )
    asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    result = client.get(f"/api/brands/{brand}/studio/jobs/{job['id']}").json()
    assert result["state"] == "completed", result
    concepts = result["result"]["concepts"]
    assert len(concepts) == 5
    assert copy.await_count == 5
    assert image.await_count == 6
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM studio_rendition WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 20
    )
    assert len({c["headline"] for c in concepts}) == 5
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM brand_context WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        == 1
    )
    for concept in concepts:
        response = client.get(f"/api/brands/{brand}/studio/{concept['id']}")
        assert response.status_code == 200
        assert response.json()["meta"]["image_url"] == IMAGE
    assert (
        client.get(f"/api/brands/{uuid4()}/studio/{concepts[0]['id']}").status_code
        == 404
    )


def test_duplicate_concept_does_not_spend_on_a_second_image(
    client, brand, copy_bundle, monkeypatch
):
    monkeypatch.setattr(
        "adjutant.studio_worker.generate_copy", AsyncMock(return_value=copy_bundle)
    )
    image = AsyncMock(return_value=IMAGE)
    monkeypatch.setattr(VisualsGenerator, "generate_ad_image", image)
    client.app.state.config.workflow_enabled = True
    job = client.post(
        "/api/studio/jobs",
        json={
            "brand_id": brand,
            "url_or_prompt": "Residential plumbing",
            "request_key": str(uuid4()),
        },
    ).json()
    asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    result = client.get(f"/api/brands/{brand}/studio/jobs/{job['id']}").json()
    assert result["state"] == "failed" and result["error_code"] == "ConceptsTooSimilar"
    image.assert_awaited_once()


def test_failed_concept_retry_keeps_completed_work(
    client, brand, copy_bundle, monkeypatch, admin
):
    copy = AsyncMock(return_value=copy_bundle)
    image = AsyncMock(
        side_effect=DomainError(
            "ImageGenerationUnavailable", "Network unavailable", 503
        )
    )
    monkeypatch.setattr("adjutant.studio_worker.generate_copy", copy)
    monkeypatch.setattr(VisualsGenerator, "generate_ad_image", image)
    job = enqueue_studio(client, brand).json()
    asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    url = f"/api/brands/{brand}/studio/jobs/{job['id']}"
    assert client.get(url).json()["state"] == "failed"
    image.side_effect = None
    image.return_value = IMAGE
    assert client.post(url + "/retry").status_code == 202
    asyncio.run(job_runner(client).run_job(UUID(brand), UUID(job["id"])))
    assert client.get(url).json()["state"] == "completed"
    assert copy.await_count == 1
    assert client.post(url + "/retry").status_code == 409
    assert (
        admin.execute(
            "SELECT count(*) AS n FROM studio_draft WHERE job_id=%s", (job["id"],)
        ).fetchone()["n"]
        == 1
    )


def test_database_rejects_incomplete_concept_set(client, brand, admin):
    client.app.state.config.workflow_enabled = True
    job = client.post(
        "/api/studio/jobs",
        json={
            "brand_id": brand,
            "url_or_prompt": "Residential plumbing",
            "request_key": str(uuid4()),
        },
    ).json()
    with pytest.raises(psycopg.Error, match="Every requested concept"):
        admin.execute(
            "UPDATE studio_job SET state='completed' WHERE id=%s", (job["id"],)
        )
