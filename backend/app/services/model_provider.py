"""Provider-neutral text generation for optional agent reasoning.

Model output is advisory: callers must validate it and retain authority
for policy checks, approvals, and business-system writes in application code.
"""

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence
from urllib.parse import urljoin

import httpx

from app.core.config import settings


Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class ModelResponse:
    content: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelProviderError(RuntimeError):
    """A safe-to-report model request or response error."""


class ModelClient(Protocol):
    def complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0,
    ) -> ModelResponse: ...


class _HttpModelClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float,
    ):
        self.provider = provider
        self.base_url = base_url.rstrip("/") + "/"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def _post(self, path: str, *, headers: dict, body: dict) -> dict:
        try:
            response = httpx.post(
                urljoin(self.base_url, path.lstrip("/")),
                headers=headers,
                json=body,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ModelProviderError(
                f"{self.provider} request failed ({type(exc).__name__})"
            ) from exc

        if not response.is_success:
            raise ModelProviderError(
                f"{self.provider} returned HTTP {response.status_code}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ModelProviderError(
                f"{self.provider} returned invalid JSON"
            ) from exc

        if not isinstance(data, dict):
            raise ModelProviderError(
                f"{self.provider} returned an unexpected response"
            )
        return data

    def complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0,
    ) -> ModelResponse:
        if not messages:
            raise ValueError("At least one chat message is required")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if any(
            message.role not in ("user", "assistant")
            or not isinstance(message.content, str)
            for message in messages
        ):
            raise ValueError("Messages must have a valid role and text content")

        return self._complete(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def _complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        raise NotImplementedError


class OpenAICompatibleClient(_HttpModelClient):
    """Works with OpenAI, Mistral, Ollama, vLLM, and compatible endpoints."""

    def _complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        request_messages = []
        if system:
            request_messages.append({"role": "system", "content": system})
        request_messages.extend(
            {"role": message.role, "content": message.content}
            for message in messages
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = self._post(
            "chat/completions",
            headers=headers,
            body={
                "model": self.model,
                "messages": request_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelProviderError(
                f"{self.provider} response is missing message content"
            ) from exc
        if not isinstance(content, str):
            raise ModelProviderError(
                f"{self.provider} returned non-text message content"
            )

        usage = data.get("usage") or {}
        return ModelResponse(
            content=content,
            provider=self.provider,
            model=str(data.get("model") or self.model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


class AnthropicClient(_HttpModelClient):
    """Anthropic Messages API adapter; callers still use the same interface."""

    def _complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system

        data = self._post("v1/messages", headers=headers, body=body)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ModelProviderError(
                "anthropic response is missing message content"
            )
        text = "\n".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if not text:
            raise ModelProviderError(
                "anthropic response contains no text content"
            )
        usage = data.get("usage") or {}
        return ModelResponse(
            content=text,
            provider=self.provider,
            model=str(data.get("model") or self.model),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


def create_model_client(
    *,
    provider: str,
    base_url: str,
    model: str,
    api_key: str = "",
    timeout: float = 60,
) -> ModelClient:
    """Create a client for the selected provider; never silently fail over."""
    normalized = provider.strip().lower()
    if normalized == "disabled":
        raise ValueError("Model provider is disabled")
    if normalized not in ("openai_compatible", "anthropic"):
        raise ValueError(
            f"Unsupported MODEL_PROVIDER {provider!r}; use "
            "'openai_compatible', 'anthropic', or 'disabled'"
        )
    if not base_url.strip():
        raise ValueError("MODEL_BASE_URL is required when a model provider is enabled")
    if not model.strip():
        raise ValueError("MODEL_NAME is required when a model provider is enabled")
    if timeout <= 0:
        raise ValueError("Model timeout must be positive")

    common = {
        "provider": normalized,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "timeout": timeout,
    }
    if normalized == "openai_compatible":
        return OpenAICompatibleClient(**common)
    if not api_key:
        raise ValueError("MODEL_API_KEY is required for the Anthropic provider")
    return AnthropicClient(**common)


def get_model_client() -> ModelClient:
    """Build the configured model client for the current process."""
    return create_model_client(
        provider=settings.model_provider,
        base_url=settings.model_base_url,
        model=settings.model_name,
        api_key=settings.model_api_key,
        timeout=settings.model_timeout_seconds,
    )
