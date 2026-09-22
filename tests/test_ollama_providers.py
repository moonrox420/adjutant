import json
from unittest.mock import patch

import httpx
import pytest

from adjutant.errors import DomainError
from adjutant.generation import OllamaPlanner
from adjutant.models import PlanInput


def test_cloud_uses_bearer_auth_and_validates_without_unsupported_format(plan_input):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"message": {"content": json.dumps(plan_input)}})

    transport = httpx.Client(transport=httpx.MockTransport(respond))
    with (
        patch.object(OllamaPlanner, "models", return_value=["cloud-model"]),
        patch("adjutant.generation.httpx.Client", return_value=transport),
    ):
        result = OllamaPlanner(
            "https://ollama.com", provider="cloud", api_key="private-test-key"
        ).generate("cloud-model", {"facts": []})
    assert result.plan.name == plan_input["name"]
    assert requests[0].headers["Authorization"] == "Bearer private-test-key"
    payload = json.loads(requests[0].content)
    assert "format" not in payload
    assert (
        json.dumps(PlanInput.model_json_schema(mode="serialization"))
        in payload["messages"][0]["content"]
    )
    assert "private-test-key" not in requests[0].content.decode()


def test_cloud_invalid_drafts_are_never_accepted():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"message": {"content": '{"unapproved":"value"}'}})

    transport = httpx.Client(transport=httpx.MockTransport(respond))
    with (
        patch.object(OllamaPlanner, "models", return_value=["cloud-model"]),
        patch("adjutant.generation.httpx.Client", return_value=transport),
        pytest.raises(DomainError) as error,
    ):
        OllamaPlanner("https://ollama.com", provider="cloud", api_key="private-test-key").generate(
            "cloud-model", {}
        )
    assert error.value.code == "GenerationInvalid"
    assert len(calls) == 3


@pytest.mark.parametrize(
    "url", ["http://ollama.com", "https://evil.example", "https://ollama.com.evil.example"]
)
def test_cloud_key_cannot_be_sent_to_another_origin(url):
    with pytest.raises(ValueError, match="Cloud credentials"):
        OllamaPlanner(url, provider="cloud", api_key="secret")


def test_unconfigured_cloud_fails_before_network():
    with patch("adjutant.generation.httpx.get") as get, pytest.raises(DomainError) as error:
        OllamaPlanner("https://ollama.com", provider="cloud").models()
    assert error.value.code == "CloudNotConfigured"
    get.assert_not_called()


def test_local_catalog_accepts_standard_tags_and_excludes_cloud_models():
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "http://localhost:11434/api/tags"),
        json={
            "models": [
                {"name": "local-model"},
                {"name": "remote", "remote_host": "https://ollama.com"},
                {"name": "embedding", "capabilities": ["embedding"]},
            ]
        },
    )
    with patch("adjutant.generation.httpx.get", return_value=response) as get:
        assert OllamaPlanner("http://localhost:11434", api_key="must-not-send").models() == [
            "local-model"
        ]
    assert get.call_args.kwargs["headers"] == {}
    assert get.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "ModelAccessDenied"),
        (403, "ModelAccessDenied"),
        (429, "ModelRateLimited"),
        (500, "ModelUnavailable"),
    ],
)
def test_cloud_errors_are_actionable_without_response_or_key_leaks(status, code):
    response = httpx.Response(
        status,
        request=httpx.Request("GET", "https://ollama.com/api/tags"),
        text="provider-body-containing-secrets",
    )
    with (
        patch("adjutant.generation.httpx.get", return_value=response),
        pytest.raises(DomainError) as error,
    ):
        OllamaPlanner("https://ollama.com", provider="cloud", api_key="private-test-key").models()
    assert error.value.code == code
    assert "secrets" not in error.value.message and "private-test-key" not in error.value.message


def test_provider_configuration_endpoint_never_returns_credentials(client):
    response = client.get("/api/model-providers")
    assert response.status_code == 200
    assert {provider["id"] for provider in response.json()["providers"]} == {"local", "cloud"}
    assert "api_key" not in response.text and "password" not in response.text
