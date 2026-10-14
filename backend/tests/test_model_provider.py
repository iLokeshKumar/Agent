from unittest.mock import Mock

import httpx
import pytest

from app.services.model_provider import (
    AnthropicClient,
    ChatMessage,
    ModelProviderError,
    OpenAICompatibleClient,
    create_model_client,
)


def _response(body, status_code=200):
    response = Mock()
    response.is_success = 200 <= status_code < 300
    response.status_code = status_code
    response.json.return_value = body
    return response


def test_openai_compatible_client_uses_configured_endpoint_and_parses_usage(
    monkeypatch,
):
    post = Mock(return_value=_response({
        "model": "mistral-small-latest",
        "choices": [{"message": {"content": "draft"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }))
    monkeypatch.setattr(httpx, "post", post)
    client = create_model_client(
        provider="openai_compatible",
        base_url="https://api.mistral.ai/v1",
        model="mistral-small-latest",
        api_key="test-token",
    )

    result = client.complete(
        system="Only draft; do not send.",
        messages=[ChatMessage(role="user", content="Write a reply")],
    )

    assert result.content == "draft"
    assert result.provider == "openai_compatible"
    assert result.input_tokens == 12
    assert post.call_args.args[0] == (
        "https://api.mistral.ai/v1/chat/completions"
    )
    assert post.call_args.kwargs["headers"]["Authorization"] == (
        "Bearer test-token"
    )
    assert post.call_args.kwargs["json"]["messages"][0]["role"] == "system"


def test_anthropic_client_uses_native_messages_api(monkeypatch):
    post = Mock(return_value=_response({
        "model": "claude-test",
        "content": [{"type": "text", "text": "proposal"}],
        "usage": {"input_tokens": 8, "output_tokens": 2},
    }))
    monkeypatch.setattr(httpx, "post", post)
    client = create_model_client(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-test",
        api_key="test-token",
    )

    result = client.complete(
        messages=[ChatMessage(role="user", content="Propose only")],
    )

    assert isinstance(client, AnthropicClient)
    assert result.content == "proposal"
    assert result.input_tokens == 8
    assert post.call_args.args[0] == "https://api.anthropic.com/v1/messages"
    assert post.call_args.kwargs["headers"]["x-api-key"] == "test-token"


def test_provider_http_error_does_not_expose_response_body(monkeypatch):
    post = Mock(return_value=_response({"error": "sensitive provider detail"}, 401))
    monkeypatch.setattr(httpx, "post", post)
    client = OpenAICompatibleClient(
        provider="openai_compatible",
        base_url="https://model.example/v1",
        model="model-name",
        api_key="test-token",
        timeout=5,
    )

    with pytest.raises(ModelProviderError, match="HTTP 401") as exc:
        client.complete(
            messages=[ChatMessage(role="user", content="hello")],
        )
    assert "sensitive provider detail" not in str(exc.value)


@pytest.mark.parametrize(
    ("provider", "base_url", "model", "api_key", "error"),
    [
        ("disabled", "https://example.com/v1", "model", "", "disabled"),
        ("openai_compatible", "", "model", "", "MODEL_BASE_URL"),
        ("anthropic", "https://api.anthropic.com", "model", "", "MODEL_API_KEY"),
        ("unknown", "https://example.com/v1", "model", "", "Unsupported"),
    ],
)
def test_invalid_provider_configuration_fails_explicitly(
    provider, base_url, model, api_key, error
):
    with pytest.raises(ValueError, match=error):
        create_model_client(
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
        )


def test_completion_rejects_empty_messages_before_network(monkeypatch):
    post = Mock()
    monkeypatch.setattr(httpx, "post", post)
    client = OpenAICompatibleClient(
        provider="openai_compatible",
        base_url="https://model.example/v1",
        model="model-name",
        api_key="",
        timeout=5,
    )

    with pytest.raises(ValueError, match="At least one"):
        client.complete(messages=[])
    post.assert_not_called()
