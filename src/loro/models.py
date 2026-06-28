import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from loro.config import ModelConfig
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


class ModelClient(Protocol):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest: ...

    def complete(self, messages: list[ModelMessage]) -> ModelResponse: ...


class BaseModelClient:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def _api_key(self) -> str | None:
        if not self.config.api_key_env:
            return None
        return os.environ.get(self.config.api_key_env)


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


class OpenAICompatibleClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        base_url = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [message.__dict__ for message in messages],
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
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.request(
                request.method,
                request.url,
                headers=request.headers,
                json=request.json,
            )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return ModelResponse(content=content, raw=payload)


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
            "messages": [message.__dict__ for message in messages],
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
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.request(
                request.method,
                request.url,
                headers=request.headers,
                json=request.json,
            )
        response.raise_for_status()
        payload = response.json()
        content = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        return ModelResponse(content=content, raw=payload)


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
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.request(
                request.method,
                request.url,
                headers=request.headers,
                json=request.json,
            )
        response.raise_for_status()
        payload = response.json()
        content = payload["candidates"][0]["content"]["parts"][0]["text"]
        return ModelResponse(content=content, raw=payload)


class OllamaClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        base_url = (self.config.base_url or "http://localhost:11434").rstrip("/")
        return ModelRequest(
            method="POST",
            url=f"{base_url}/api/chat",
            headers={"Content-Type": "application/json"},
            json={
                "model": self.config.model,
                "messages": [message.__dict__ for message in messages],
                "stream": False,
                "options": {"temperature": self.config.temperature},
            },
        )

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        request = self.build_request(messages)
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.request(
                request.method,
                request.url,
                headers=request.headers,
                json=request.json,
            )
        response.raise_for_status()
        payload = response.json()
        return ModelResponse(content=payload["message"]["content"], raw=payload)


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
        raise NotImplementedError("Bedrock adapter requires AWS SDK integration.")
    return OpenAICompatibleClient(config)
