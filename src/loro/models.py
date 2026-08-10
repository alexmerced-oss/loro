import json
import os
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from loro.config import ModelConfig
from loro.credentials import CredentialError, CredentialVault
from loro.model_tools import (
    ModelToolCall,
    ModelToolCallParseError,
    ModelToolResult,
    parse_provider_tool_calls,
)
from loro.providers import get_provider_profile
from loro.tool_schemas import ToolSchema, provider_tool_name, provider_tool_payload


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str = ""
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    tool_results: list[ModelToolResult] = field(default_factory=list)


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

    def stream_complete(
        self,
        messages: list[ModelMessage],
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse: ...

    def stream(self, messages: list[ModelMessage]) -> Iterator[str]: ...


class BaseModelClient:
    def __init__(
        self,
        config: ModelConfig,
        tools: Sequence[ToolSchema] | None = None,
    ) -> None:
        self.config = config
        self.tools: list[ToolSchema] = list(tools or [])

    def _tool_payload(self) -> dict[str, Any]:
        """Provider-shaped tool declarations, or {} when none are published."""

        protocol = get_provider_profile(self.config.provider).protocol
        return provider_tool_payload(protocol, self.tools)

    def _api_key(self) -> str | None:
        if self.config.api_key_env:
            value = os.environ.get(self.config.api_key_env)
            if value:
                return value
        if self.config.credential_ref:
            try:
                return CredentialVault().get(self.config.credential_ref)
            except CredentialError as error:
                raise ModelProviderError(f"Credential vault lookup failed: {error}") from error
        return None

    def _optional_provider_headers(self) -> dict[str, str]:
        profile = get_provider_profile(self.config.provider)
        return {
            header: value
            for header, environment_name in profile.optional_header_env
            if (value := os.environ.get(environment_name))
        }

    def _message_payload(self, messages: list[ModelMessage]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for message in messages:
            if message.tool_calls:
                payload.append(
                    {
                        "role": "assistant",
                        "content": message.content or None,
                        "tool_calls": [
                            call.provider_payload
                            or {
                                "id": call.call_id,
                                "type": "function",
                                "function": {
                                    "name": provider_tool_name(call.name),
                                    "arguments": json.dumps(call.args),
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )
                continue
            if message.tool_results:
                payload.extend(
                    {
                        "role": "tool",
                        "tool_call_id": result.call_id,
                        "content": result.content,
                    }
                    for result in message.tool_results
                )
                if message.content:
                    payload.append({"role": "user", "content": message.content})
                continue
            payload.append({"role": message.role, "content": message.content})
        return payload

    def _client_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"timeout": self.config.timeout_seconds}
        if not self.config.verify_tls:
            options["verify"] = False
        if self.config.ca_bundle_env:
            ca_bundle = os.environ.get(self.config.ca_bundle_env)
            if not ca_bundle:
                raise ModelProviderError(
                    "Configured CA bundle environment variable is missing: "
                    f"{self.config.ca_bundle_env}"
                )
            options["verify"] = ca_bundle
        if self.config.proxy_env:
            proxy = os.environ.get(self.config.proxy_env)
            if not proxy:
                raise ModelProviderError(
                    f"Configured proxy environment variable is missing: {self.config.proxy_env}"
                )
            options["proxy"] = proxy
        return options

    def _send(self, request: ModelRequest) -> dict[str, Any]:
        client_options = self._client_options()

        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(**client_options) as client:
                    response = client.request(
                        request.method,
                        request.url,
                        headers=request.headers,
                        json=request.json,
                    )
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.TimeoutException as error:
                if attempt < self.config.max_retries:
                    self._retry_pause(attempt)
                    continue
                raise ModelProviderError(
                    f"{self.config.provider} request timed out after "
                    f"{self.config.timeout_seconds} seconds."
                ) from error
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                if (status == 429 or status >= 500) and attempt < self.config.max_retries:
                    self._retry_pause(attempt)
                    continue
                raise ModelProviderError(
                    f"{self.config.provider} returned HTTP {status}: "
                    f"{_response_preview(error.response.text)}"
                ) from error
            except httpx.RequestError as error:
                if attempt < self.config.max_retries:
                    self._retry_pause(attempt)
                    continue
                raise ModelProviderError(
                    f"{self.config.provider} request failed: {error}"
                ) from error
            except ValueError as error:
                raise ModelProviderError(
                    f"{self.config.provider} returned malformed JSON."
                ) from error
        if not isinstance(payload, dict):
            raise ModelProviderError(f"{self.config.provider} returned a non-object JSON payload.")
        return payload

    def _retry_pause(self, attempt: int) -> None:
        delay = self.config.backoff_seconds * (2**attempt)
        if delay:
            time.sleep(delay)

    def stream(self, messages: list[ModelMessage]) -> Iterator[str]:
        chunks: list[str] = []
        self.stream_complete(messages, chunks.append)
        yield from chunks

    def stream_complete(
        self,
        messages: list[ModelMessage],
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        response = self.complete(messages)
        if response.content and on_token:
            on_token(response.content)
        return response

    def _stream_events(self, messages: list[ModelMessage]) -> Iterator[dict[str, Any]]:
        """Yield SSE JSON events with the configured enterprise transport policy.

        A failed stream may be retried only before an event has been emitted. Once data
        has reached the caller, retrying could duplicate model output and tool calls.
        """

        request = self.build_request(messages)
        emitted = False
        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(**self._client_options()) as client:
                    with client.stream(
                        request.method,
                        request.url,
                        headers=request.headers,
                        json={**request.json, "stream": True},
                    ) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            try:
                                event = json.loads(data)
                            except ValueError:
                                continue
                            if isinstance(event, dict):
                                emitted = True
                                yield event
                        return
            except (httpx.HTTPError, OSError) as error:
                if emitted:
                    raise ModelProviderError(
                        f"{self.config.provider} stream ended unexpectedly."
                    ) from error
                if attempt < self.config.max_retries:
                    self._retry_pause(attempt)
                    continue
                return


class MockModelClient(BaseModelClient):
    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        return ModelRequest(
            method="MOCK",
            url="mock://local",
            headers={},
            json={"messages": self._message_payload(messages)},
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
        headers = {"Content-Type": "application/json", **self._optional_provider_headers()}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._message_payload(messages),
        }
        if _supports_openai_temperature(self.config.provider, self.config.model):
            payload["temperature"] = self.config.temperature
        if self.config.max_tokens:
            payload["max_tokens"] = self.config.max_tokens
        payload.update(self._tool_payload())
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

    def stream_complete(
        self,
        messages: list[ModelMessage],
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        content: list[str] = []
        pending_calls: dict[int, dict[str, Any]] = {}
        saw_event = False
        for event in self._stream_events(messages):
            saw_event = True
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
            if not isinstance(delta, dict):
                continue
            chunk = str(delta.get("content") or "")
            if chunk:
                content.append(chunk)
                if on_token:
                    on_token(chunk)
            raw_calls = delta.get("tool_calls", [])
            if not isinstance(raw_calls, list):
                raise ModelProviderError(
                    f"{self.config.provider} returned malformed streamed tool calls."
                )
            for raw_call in raw_calls:
                if not isinstance(raw_call, dict):
                    continue
                index = raw_call.get("index", 0)
                if not isinstance(index, int):
                    continue
                pending = pending_calls.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if raw_call.get("id"):
                    pending["id"] = str(raw_call["id"])
                function = raw_call.get("function")
                if isinstance(function, dict):
                    pending["function"]["name"] += str(function.get("name") or "")
                    pending["function"]["arguments"] += str(function.get("arguments") or "")
        if not saw_event:
            return super().stream_complete(messages, on_token)
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "".join(content),
                        "tool_calls": [pending_calls[index] for index in sorted(pending_calls)],
                    }
                }
            ]
        }
        return ModelResponse(
            content="".join(content),
            raw=payload,
            tool_calls=_provider_tool_calls(self.config.provider, payload),
        )


class AnthropicClient(BaseModelClient):
    def _message_payload(self, messages: list[ModelMessage]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for message in messages:
            if message.tool_calls:
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                blocks.extend(
                    call.provider_payload
                    or {
                        "type": "tool_use",
                        "id": call.call_id,
                        "name": provider_tool_name(call.name),
                        "input": call.args,
                    }
                    for call in message.tool_calls
                )
                payload.append({"role": "assistant", "content": blocks})
                continue
            if message.tool_results:
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call_id,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                    for result in message.tool_results
                ]
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                payload.append({"role": "user", "content": blocks})
                continue
            payload.append({"role": message.role, "content": message.content})
        return payload

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
            "max_tokens": self.config.max_tokens or 4096,
        }
        if _supports_anthropic_temperature(self.config.model):
            payload["temperature"] = self.config.temperature
        payload.update(self._tool_payload())
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

    def stream_complete(
        self,
        messages: list[ModelMessage],
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        content: list[str] = []
        pending_calls: dict[int, dict[str, Any]] = {}
        saw_event = False
        for event in self._stream_events(messages):
            saw_event = True
            event_type = event.get("type")
            index = event.get("index", 0)
            if event_type == "content_block_start" and isinstance(index, int):
                block = event.get("content_block")
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    pending_calls[index] = {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input", {}),
                    }
                continue
            if event_type != "content_block_delta":
                continue
            delta = event.get("delta")
            if not isinstance(delta, dict):
                continue
            chunk = str(delta.get("text") or "")
            if chunk:
                content.append(chunk)
                if on_token:
                    on_token(chunk)
            if delta.get("type") == "input_json_delta" and isinstance(index, int):
                pending = pending_calls.setdefault(
                    index,
                    {"type": "tool_use", "id": None, "name": None, "input": {}},
                )
                pending["_input_json"] = pending.get("_input_json", "") + str(
                    delta.get("partial_json") or ""
                )
        if not saw_event:
            return super().stream_complete(messages, on_token)
        blocks: list[dict[str, Any]] = []
        if content:
            blocks.append({"type": "text", "text": "".join(content)})
        for index in sorted(pending_calls):
            block = pending_calls[index]
            raw_input = block.pop("_input_json", "")
            if raw_input:
                try:
                    block["input"] = json.loads(raw_input)
                except json.JSONDecodeError as error:
                    raise ModelProviderError(
                        "anthropic returned malformed streamed tool-call JSON."
                    ) from error
            blocks.append(block)
        payload = {"content": blocks}
        return ModelResponse(
            content="".join(content),
            raw=payload,
            tool_calls=_provider_tool_calls(self.config.provider, payload),
        )


class GeminiClient(BaseModelClient):
    def _message_payload(self, messages: list[ModelMessage]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = "model" if message.role == "assistant" else "user"
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"text": message.content})
            parts.extend(
                call.provider_payload
                or {
                    "functionCall": {
                        "name": provider_tool_name(call.name),
                        "args": call.args,
                    }
                }
                for call in message.tool_calls
            )
            parts.extend(
                {
                    "functionResponse": {
                        "name": provider_tool_name(result.name),
                        "response": {"output": result.content, "is_error": result.is_error},
                    }
                }
                for result in message.tool_results
            )
            contents.append({"role": role, "parts": parts or [{"text": ""}]})
        return contents

    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        base_url = (self.config.base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        api_key = self._api_key()
        url = f"{base_url}/v1beta/models/{self.config.model}:generateContent"
        if api_key:
            url = f"{url}?key={api_key}"
        contents = self._message_payload(messages)
        return ModelRequest(
            method="POST",
            url=url,
            headers={"Content-Type": "application/json"},
            json=_gemini_payload(
                contents=contents,
                model=self.config.model,
                temperature=self.config.temperature,
                tools=self._tool_payload(),
            ),
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
        content = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
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
    def _message_payload(self, messages: list[ModelMessage]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for message in messages:
            role = "assistant" if message.role == "assistant" else "user"
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"text": message.content})
            blocks.extend(
                call.provider_payload
                or {
                    "toolUse": {
                        "toolUseId": call.call_id,
                        "name": provider_tool_name(call.name),
                        "input": call.args,
                    }
                }
                for call in message.tool_calls
            )
            blocks.extend(
                {
                    "toolResult": {
                        "toolUseId": result.call_id,
                        "content": [{"text": result.content}],
                        "status": "error" if result.is_error else "success",
                    }
                }
                for result in message.tool_results
            )
            payload.append({"role": role, "content": blocks or [{"text": ""}]})
        return payload

    def build_request(self, messages: list[ModelMessage]) -> ModelRequest:
        return ModelRequest(
            method="AWS_BEDROCK",
            url=f"bedrock://runtime/{self.config.model}",
            headers={},
            json={
                "modelId": self.config.model,
                "messages": self._message_payload(messages),
                "temperature": self.config.temperature,
                **self._tool_payload(),
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
                messages=self._message_payload(messages),
                inferenceConfig={"temperature": self.config.temperature},
                **self._tool_payload(),
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


def create_model_client(
    config: ModelConfig,
    tools: Sequence[ToolSchema] | None = None,
) -> ModelClient:
    profile = get_provider_profile(config.provider)
    if profile.protocol == "local" or profile.name == "mock":
        return MockModelClient(config, tools)
    if profile.protocol == "anthropic":
        return AnthropicClient(config, tools)
    if profile.protocol == "gemini":
        return GeminiClient(config, tools)
    if profile.protocol == "ollama":
        return OllamaClient(config, tools)
    if profile.protocol == "bedrock":
        return BedrockClient(config, tools)
    return OpenAICompatibleClient(config, tools)


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


def _supports_openai_temperature(provider: str, model: str) -> bool:
    if provider == "openai" and model.startswith("gpt-5"):
        return False
    return True


def _supports_anthropic_temperature(model: str) -> bool:
    return not model.startswith("claude-sonnet-5")


def _gemini_payload(
    *,
    contents: list[dict[str, Any]],
    model: str,
    temperature: float,
    tools: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"contents": contents}
    if _supports_gemini_temperature(model):
        payload["generationConfig"] = {"temperature": temperature}
    payload.update(tools or {})
    return payload


def _supports_gemini_temperature(model: str) -> bool:
    return not (
        model == "gemini-3.6-flash"
        or model.startswith("gemini-3.6-")
        or model == "gemini-3.5-flash-lite"
        or model.startswith("gemini-3.5-flash-lite-")
    )
