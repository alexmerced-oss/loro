import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from loro.config import ModelConfig
from loro.model_tools import (
    ModelToolCall,
    ModelToolCallParseError,
    parse_provider_tool_calls,
)
from loro.providers import get_provider_profile


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelRequest:
    method: str
    url: str
    headers: dict[str, str]
    json: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    content: str
    raw: dict[str, Any] | None = None
    tool_calls: list[ModelToolCall] = field(default_factory=list)


class ModelProviderError(RuntimeError):
    """Normalized provider error suitable for CLI display."""


class ModelClient(Protocol):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest: ...

    def complete(self, messages: list[ModelMessage]) -> ModelResponse: ...

    def stream(self, messages: list[ModelMessage]) -> Iterator[str]: ...


class BaseModelClient:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def _api_key(self) -> str | None:
        if not self.config.api_key_env:
            return None
        return os.environ.get(self.config.api_key_env)

    def _message_payload(self, messages: list[ModelMessage]) -> list[dict[str, str]]:
        return [message.__dict__ for message in messages]

    def _send(self, request: ModelRequest) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.request(
                    request.method,
                    request.url,
                    headers=request.headers,
                    json=request.json,
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as error:
            raise ModelProviderError(
                f"{self.config.provider} request timed out after "
                f"{self.config.timeout_seconds} seconds."
            ) from error
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise ModelProviderError(
                f"{self.config.provider} returned HTTP {status}: "
                f"{_response_preview(error.response.text)}"
            ) from error
        except httpx.RequestError as error:
            raise ModelProviderError(f"{self.config.provider} request failed: {error}") from error
        except ValueError as error:
            raise ModelProviderError(
                f"{self.config.provider} returned malformed JSON."
            ) from error
        if not isinstance(payload, dict):
            raise ModelProviderError(f"{self.config.provider} returned a non-object JSON payload.")
        return payload

    def stream(self, messages: list[ModelMessage]) -> Iterator[str]:
        yield self.complete(messages).content


class MockModelClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        return ModelRequest(
            method="MOCK",
            url="mock://local",
            headers={},
            json={"messages": [message.__dict__ for message in messages]},
        )

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        user_text = "\n".join(message.content for message in messages if message.role == "user")
        return ModelResponse(content=f"Mock response for: {user_text}".strip())

    def stream(self, messages: list[ModelMessage]) -> Iterator[str]:
        response = self.complete(messages).content
        for chunk in response.split(" "):
            yield chunk + " "


class OpenAICompatibleClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        base_url = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._message_payload(messages),
            "temperature": self.config.temperature,
        }
        if self.config.max_tokens:
            payload["max_tokens"] = self.config.max_tokens
        return ModelRequest(
            method="POST",
            url=f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        request = self.build_request(messages)
        payload = self._send(request)
        try:
            message = payload["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError
            content = message.get("content") or ""
        except (KeyError, IndexError, TypeError) as error:
            raise ModelProviderError(
                f"{self.config.provider} response did not include choices[0].message.content."
            ) from error
        return ModelResponse(
            content=content,
            raw=payload,
            tool_calls=_provider_tool_calls(self.config.provider, payload),
        )


class AnthropicClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        base_url = (self.config.base_url or "https://api.anthropic.com").rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        api_key = self._api_key()
        if api_key:
            headers["x-api-key"] = api_key
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._message_payload(messages),
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens or 4096,
        }
        return ModelRequest(
            method="POST",
            url=f"{base_url}/v1/messages",
            headers=headers,
            json=payload,
        )

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        request = self.build_request(messages)
        payload = self._send(request)
        blocks = payload.get("content", [])
        if not isinstance(blocks, list):
            raise ModelProviderError("anthropic response did not include a content list.")
        content = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return ModelResponse(
            content=content,
            raw=payload,
            tool_calls=_provider_tool_calls(self.config.provider, payload),
        )


class GeminiClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        base_url = (self.config.base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        api_key = self._api_key()
        url = f"{base_url}/v1beta/models/{self.config.model}:generateContent"
        if api_key:
            url = f"{url}?key={api_key}"
        contents = [
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in messages
        ]
        return ModelRequest(
            method="POST",
            url=url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": contents,
                "generationConfig": {"temperature": self.config.temperature},
            },
        )

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        request = self.build_request(messages)
        payload = self._send(request)
        try:
            parts = payload["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as error:
            raise ModelProviderError(
                "gemini response did not include candidates[0].content.parts[0].text."
            ) from error
        if not isinstance(parts, list):
            raise ModelProviderError("gemini response did not include a parts list.")
        content = "".join(
            part.get("text", "") for part in parts if isinstance(part, dict)
        )
        if not content and not any(
            isinstance(part, dict) and "functionCall" in part for part in parts
        ):
            raise ModelProviderError(
                "gemini response did not include candidates[0].content.parts[0].text."
            )
        return ModelResponse(
            content=content,
            raw=payload,
            tool_calls=_provider_tool_calls(self.config.provider, payload),
        )


class OllamaClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        base_url = (self.config.base_url or "http://localhost:11434").rstrip("/")
        return ModelRequest(
            method="POST",
            url=f"{base_url}/api/chat",
            headers={"Content-Type": "application/json"},
            json={
                "model": self.config.model,
                "messages": self._message_payload(messages),
                "stream": False,
                "options": {"temperature": self.config.temperature},
            },
        )

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        request = self.build_request(messages)
        payload = self._send(request)
        try:
            content = payload["message"]["content"]
        except (KeyError, TypeError) as error:
            raise ModelProviderError("ollama response did not include message.content.") from error
        return ModelResponse(content=content, raw=payload)


class BedrockClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        return ModelRequest(
            method="AWS_BEDROCK",
            url=f"bedrock://runtime/{self.config.model}",
            headers={},
            json={
                "modelId": self.config.model,
                "messages": self._message_payload(messages),
                "temperature": self.config.temperature,
            },
        )

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        try:
            import boto3
            from botocore.exceptions import BotoCoreError, ClientError
        except ModuleNotFoundError as error:
            raise ModelProviderError(
                "Bedrock adapter requires boto3 and botocore. Install the aws extra or boto3."
            ) from error
        try:
            client = boto3.client("bedrock-runtime")
            response = client.converse(
                modelId=self.config.model,
                messages=[
                    {
                        "role": "assistant" if message.role == "assistant" else "user",
                        "content": [{"text": message.content}],
                    }
                    for message in messages
                ],
                inferenceConfig={"temperature": self.config.temperature},
            )
        except (BotoCoreError, ClientError) as error:
            raise ModelProviderError(f"bedrock request failed: {error}") from error
        try:
            content = "".join(
                item.get("text", "")
                for item in response["output"]["message"]["content"]
                if isinstance(item, dict)
            )
        except (KeyError, TypeError) as error:
            raise ModelProviderError(
                "bedrock response did not include output.message.content."
            ) from error
        return ModelResponse(
            content=content,
            raw=response,
            tool_calls=_provider_tool_calls(self.config.provider, response),
        )


def create_model_client(config: ModelConfig) -> ModelClient:
    profile = get_provider_profile(config.provider)
    if profile.protocol == "local" or profile.name == "mock":
        return MockModelClient(config)
    if profile.protocol == "anthropic":
        return AnthropicClient(config)
    if profile.protocol == "gemini":
        return GeminiClient(config)
    if profile.protocol == "ollama":
        return OllamaClient(config)
    if profile.protocol == "bedrock":
        return BedrockClient(config)
    return OpenAICompatibleClient(config)


def smoke_model_client(
    config: ModelConfig,
    *,
    prompt: str = "Reply with ok.",
    execute: bool = False,
    stream: bool = False,
) -> dict[str, Any]:
    client = create_model_client(config)
    messages = [ModelMessage(role="user", content=prompt)]
    request = client.build_request(messages)
    payload: dict[str, Any] = {
        "provider": config.provider,
        "model": config.model,
        "execute": execute,
        "stream": stream,
        "request": redact_model_request(request),
    }
    if not execute:
        return payload
    try:
        if stream:
            chunks = list(client.stream(messages))
            payload["content"] = "".join(chunks)
            payload["chunks"] = chunks
        else:
            response = client.complete(messages)
            payload["content"] = response.content
    except ModelProviderError as error:
        payload["ok"] = False
        payload["error"] = str(error)
        return payload
    payload["ok"] = True
    return payload


def redact_model_request(request: ModelRequest) -> dict[str, Any]:
    headers = dict(request.headers)
    for key in list(headers):
        if key.lower() in {"authorization", "x-api-key"}:
            headers[key] = "[redacted]"
    url = request.url
    parsed = urlsplit(url)
    if parsed.query:
        query = [
            (key, "[redacted]" if key.lower() in {"key", "api_key", "apikey"} else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        url = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
        )
    return {
        "method": request.method,
        "url": url,
        "headers": headers,
        "json": request.json,
    }


def _response_preview(text: str, limit: int = 500) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:limit]


def _provider_tool_calls(provider: str, payload: dict[str, Any]) -> list[ModelToolCall]:
    protocol = get_provider_profile(provider).protocol
    try:
        return parse_provider_tool_calls(protocol, payload)
    except ModelToolCallParseError as error:
        raise ModelProviderError(f"{provider} returned malformed tool calls: {error}") from error
