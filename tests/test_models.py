import httpx
import pytest

from loro.config import ModelConfig
from loro.model_tools import ModelToolCall, ModelToolResult
from loro.models import (
    AnthropicClient,
    BedrockClient,
    GeminiClient,
    MockModelClient,
    ModelMessage,
    ModelProviderError,
    OllamaClient,
    OpenAICompatibleClient,
    create_model_client,
    redact_model_request,
    smoke_model_client,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeHttpClient:
    payload = {}
    calls = []

    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def request(self, method, url, headers, json):
        self.__class__.calls.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        return FakeResponse(self.__class__.payload)


class FakeStreamResponse:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        yield from self.lines


class FakeStreamHttpClient(FakeHttpClient):
    lines = []
    options = {}

    def __init__(self, **options):
        self.__class__.options = options

    def stream(self, method, url, headers, json):
        self.__class__.calls.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        return FakeStreamResponse(self.__class__.lines)


class FakeStatusResponse:
    status_code = 401
    text = "unauthorized"

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://example.com")
        response = httpx.Response(401, text=self.text, request=request)
        raise httpx.HTTPStatusError("bad", request=request, response=response)

    def json(self):
        return {"error": "nope"}


class FakeErrorHttpClient(FakeHttpClient):
    def request(self, method, url, headers, json):
        return FakeStatusResponse()


class FakeTransientHttpClient(FakeHttpClient):
    attempts = 0

    def request(self, method, url, headers, json):
        self.__class__.attempts += 1
        if self.__class__.attempts == 1:
            request = httpx.Request("POST", url)
            response = httpx.Response(503, text="unavailable", request=request)
            raise httpx.HTTPStatusError("retry", request=request, response=response)
        return FakeResponse({"choices": [{"message": {"content": "recovered"}}]})


def test_mock_model_client_complete() -> None:
    client = MockModelClient(ModelConfig())
    response = client.complete([ModelMessage(role="user", content="hello")])
    assert response.content == "Mock response for: hello"


def test_mock_model_client_stream() -> None:
    client = MockModelClient(ModelConfig())
    chunks = list(client.stream([ModelMessage(role="user", content="hello")]))
    assert "".join(chunks).strip() == "Mock response for: hello"


def test_openai_compatible_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpClient.payload = {"choices": [{"message": {"content": "done"}}]}
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = OpenAICompatibleClient(
        ModelConfig(provider="openai", model="gpt-test", base_url="https://example.com/v1")
    )
    response = client.complete([ModelMessage(role="user", content="hello")])
    assert response.content == "done"
    assert response.raw == FakeHttpClient.payload
    assert response.tool_calls == []
    assert FakeHttpClient.calls[0]["url"] == "https://example.com/v1/chat/completions"


def test_openai_compatible_complete_with_native_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeHttpClient.payload = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-read-1",
                            "type": "function",
                            "function": {
                                "name": "file.read",
                                "arguments": '{"path": "README.md", "limit": 100}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = OpenAICompatibleClient(
        ModelConfig(provider="openai", model="gpt-test", base_url="https://example.com/v1")
    )

    response = client.complete([ModelMessage(role="user", content="hello")])

    assert response.content == ""
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "file.read"
    assert response.tool_calls[0].args == {"path": "README.md", "limit": 100}


def test_openai_compatible_normalizes_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("loro.models.httpx.Client", FakeErrorHttpClient)
    client = OpenAICompatibleClient(
        ModelConfig(provider="openai", model="gpt-test", base_url="https://example.com/v1")
    )
    with pytest.raises(ModelProviderError, match="HTTP 401"):
        client.complete([ModelMessage(role="user", content="hello")])


def test_openai_compatible_retries_transient_status(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTransientHttpClient.attempts = 0
    monkeypatch.setattr("loro.models.httpx.Client", FakeTransientHttpClient)
    client = OpenAICompatibleClient(
        ModelConfig(
            provider="openai",
            model="gpt-test",
            base_url="https://example.com/v1",
            max_retries=1,
            backoff_seconds=0,
        )
    )

    assert client.complete([ModelMessage(role="user", content="hello")]).content == "recovered"
    assert FakeTransientHttpClient.attempts == 2


def test_model_transport_requires_configured_ca_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORO_TEST_CA_BUNDLE", raising=False)
    client = OpenAICompatibleClient(
        ModelConfig(
            provider="openai",
            model="gpt-test",
            ca_bundle_env="LORO_TEST_CA_BUNDLE",
        )
    )

    with pytest.raises(ModelProviderError, match="CA bundle"):
        client.complete([ModelMessage(role="user", content="hello")])


def test_openai_compatible_normalizes_missing_content(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpClient.payload = {"choices": []}
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = OpenAICompatibleClient(
        ModelConfig(provider="openai", model="gpt-test", base_url="https://example.com/v1")
    )
    with pytest.raises(ModelProviderError, match="choices"):
        client.complete([ModelMessage(role="user", content="hello")])


def test_openai_compatible_rejects_malformed_native_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeHttpClient.payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "file.read",
                                "arguments": "{not-json",
                            }
                        }
                    ],
                }
            }
        ]
    }
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = OpenAICompatibleClient(
        ModelConfig(provider="openai", model="gpt-test", base_url="https://example.com/v1")
    )

    with pytest.raises(ModelProviderError, match="malformed tool calls"):
        client.complete([ModelMessage(role="user", content="hello")])


def test_anthropic_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpClient.payload = {"content": [{"type": "text", "text": "hello"}]}
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = AnthropicClient(ModelConfig(provider="anthropic", model="claude-test"))
    response = client.complete([ModelMessage(role="user", content="hello")])
    assert response.content == "hello"


def test_anthropic_complete_with_native_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpClient.payload = {
        "content": [
            {"type": "text", "text": "checking"},
            {
                "type": "tool_use",
                "id": "toolu-search-1",
                "name": "memory.search",
                "input": {"query": "launch"},
            },
        ]
    }
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = AnthropicClient(ModelConfig(provider="anthropic", model="claude-test"))

    response = client.complete([ModelMessage(role="user", content="hello")])

    assert response.content == "checking"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "memory.search"
    assert response.tool_calls[0].args == {"query": "launch"}


def test_gemini_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpClient.payload = {"candidates": [{"content": {"parts": [{"text": "gemini"}]}}]}
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = GeminiClient(ModelConfig(provider="gemini", model="gemini-test"))
    response = client.complete([ModelMessage(role="user", content="hello")])
    assert response.content == "gemini"


def test_gemini_complete_with_native_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpClient.payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"functionCall": {"name": "file.search", "args": {"query": "TODO"}}}]
                }
            }
        ]
    }
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = GeminiClient(ModelConfig(provider="gemini", model="gemini-test"))

    response = client.complete([ModelMessage(role="user", content="hello")])

    assert response.content == ""
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "file.search"
    assert response.tool_calls[0].args == {"query": "TODO"}


def test_ollama_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpClient.payload = {"message": {"content": "ollama"}}
    FakeHttpClient.calls = []
    monkeypatch.setattr("loro.models.httpx.Client", FakeHttpClient)
    client = OllamaClient(ModelConfig(provider="ollama", model="llama3.2"))
    response = client.complete([ModelMessage(role="user", content="hello")])
    assert response.content == "ollama"


def test_openai_compatible_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = OpenAICompatibleClient(
        ModelConfig(
            provider="openai",
            model="gpt-test",
            api_key_env="OPENAI_API_KEY",
            base_url="https://example.com/v1",
        )
    )
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert request.url == "https://example.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.json["model"] == "gpt-test"


def test_prime_intellect_request_adds_optional_team_header(monkeypatch) -> None:
    monkeypatch.setenv("PRIME_API_KEY", "test-key")
    monkeypatch.setenv("PRIME_TEAM_ID", "team-123")
    client = OpenAICompatibleClient(
        ModelConfig(
            provider="prime-intellect",
            model="openai/gpt-oss-20b",
            api_key_env="PRIME_API_KEY",
            base_url="https://api.pinference.ai/api/v1",
        )
    )

    request = client.build_request([ModelMessage(role="user", content="hello")])

    assert request.url == "https://api.pinference.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["X-Prime-Team-ID"] == "team-123"


def test_prime_intellect_team_header_is_optional(monkeypatch) -> None:
    monkeypatch.delenv("PRIME_TEAM_ID", raising=False)
    client = OpenAICompatibleClient(
        ModelConfig(provider="prime-intellect", model="openai/gpt-oss-20b")
    )

    request = client.build_request([ModelMessage(role="user", content="hello")])

    assert "X-Prime-Team-ID" not in request.headers


def test_openai_gpt5_request_omits_temperature() -> None:
    client = OpenAICompatibleClient(ModelConfig(provider="openai", model="gpt-5.6-luna"))
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert "temperature" not in request.json


def test_anthropic_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = AnthropicClient(
        ModelConfig(provider="anthropic", model="claude-test", api_key_env="ANTHROPIC_API_KEY")
    )
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert request.url == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert request.json["max_tokens"] == 4096


def test_anthropic_sonnet_5_request_omits_temperature() -> None:
    client = AnthropicClient(ModelConfig(provider="anthropic", model="claude-sonnet-5"))
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert "temperature" not in request.json


def test_gemini_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = GeminiClient(
        ModelConfig(provider="gemini", model="gemini-test", api_key_env="GEMINI_API_KEY")
    )
    request = client.build_request([ModelMessage(role="assistant", content="hello")])
    assert "gemini-test:generateContent?key=test-key" in request.url
    assert request.json["contents"][0]["role"] == "model"


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        (
            OpenAICompatibleClient(ModelConfig(provider="openai", model="gpt-test")),
            {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        ),
        (
            AnthropicClient(ModelConfig(provider="anthropic", model="claude-test")),
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "result",
                        "is_error": False,
                    }
                ],
            },
        ),
    ],
)
def test_native_tool_results_use_provider_protocol(client, expected) -> None:
    messages = [
        ModelMessage(
            role="assistant",
            tool_calls=[ModelToolCall("file.read", {"path": "README.md"}, "call-1")],
        ),
        ModelMessage(
            role="tool",
            tool_results=[ModelToolResult("file.read", "result", "call-1")],
        ),
    ]

    request = client.build_request(messages)

    assert request.json["messages"][1] == expected


def test_gemini_native_tool_result_uses_function_response() -> None:
    client = GeminiClient(ModelConfig(provider="gemini", model="gemini-test"))
    request = client.build_request(
        [
            ModelMessage(
                role="tool",
                tool_results=[ModelToolResult("file.search", "found", "call-1")],
            )
        ]
    )

    response = request.json["contents"][0]["parts"][0]["functionResponse"]
    assert response["name"] == "file_search"
    assert response["response"] == {"output": "found", "is_error": False}


def test_bedrock_native_tool_result_uses_tool_result() -> None:
    client = BedrockClient(ModelConfig(provider="bedrock", model="bedrock-test"))
    message = client._message_payload(
        [
            ModelMessage(
                role="tool",
                tool_results=[ModelToolResult("file.read", "denied", "call-1", is_error=True)],
            )
        ]
    )[0]

    assert message["content"][0]["toolResult"] == {
        "toolUseId": "call-1",
        "content": [{"text": "denied"}],
        "status": "error",
    }


def test_openai_stream_preserves_native_tool_calls_and_transport(monkeypatch) -> None:
    FakeStreamHttpClient.calls = []
    FakeStreamHttpClient.lines = [
        'data: {"choices":[{"delta":{"content":"Checking "}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-7",'
        '"function":{"name":"file_","arguments":"{\\"path\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"name":"read","arguments":"\\"README.md\\"}"}}]}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setenv("LORO_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("LORO_CA", "/tmp/enterprise-ca.pem")
    monkeypatch.setattr("loro.models.httpx.Client", FakeStreamHttpClient)
    client = OpenAICompatibleClient(
        ModelConfig(
            provider="openai",
            model="gpt-test",
            proxy_env="LORO_PROXY",
            ca_bundle_env="LORO_CA",
        )
    )
    chunks = []

    response = client.stream_complete([ModelMessage(role="user", content="read")], chunks.append)

    assert chunks == ["Checking "]
    assert response.tool_calls == [
        ModelToolCall(
            "file_read",
            {"path": "README.md"},
            "call-7",
            {
                "id": "call-7",
                "type": "function",
                "function": {"name": "file_read", "arguments": '{"path":"README.md"}'},
            },
        )
    ]
    assert FakeStreamHttpClient.options["proxy"] == "http://proxy.example:8080"
    assert FakeStreamHttpClient.options["verify"] == "/tmp/enterprise-ca.pem"


def test_anthropic_stream_preserves_native_tool_call(monkeypatch) -> None:
    FakeStreamHttpClient.lines = [
        'data: {"type":"content_block_start","index":0,"content_block":'
        '{"type":"tool_use","id":"toolu-8","name":"memory_search","input":{}}}',
        'data: {"type":"content_block_delta","index":0,"delta":'
        '{"type":"input_json_delta","partial_json":"{\\"query\\":\\"launch\\"}"}}',
        "data: [DONE]",
    ]
    monkeypatch.setattr("loro.models.httpx.Client", FakeStreamHttpClient)
    client = AnthropicClient(ModelConfig(provider="anthropic", model="claude-test"))

    response = client.stream_complete([ModelMessage(role="user", content="search")])

    assert response.tool_calls[0].call_id == "toolu-8"
    assert response.tool_calls[0].args == {"query": "launch"}


def test_gemini_36_flash_request_omits_temperature() -> None:
    client = GeminiClient(ModelConfig(provider="gemini", model="gemini-3.6-flash"))
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert "generationConfig" not in request.json


def test_ollama_request() -> None:
    client = OllamaClient(ModelConfig(provider="ollama", model="llama3.2"))
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert request.url == "http://localhost:11434/api/chat"
    assert request.json["stream"] is False


def test_create_model_client_for_nous_is_openai_compatible() -> None:
    client = create_model_client(
        ModelConfig(provider="nous", model="hermes-3-405b", base_url="https://example.com/v1")
    )
    assert isinstance(client, OpenAICompatibleClient)


def test_create_model_client_for_bedrock() -> None:
    client = create_model_client(ModelConfig(provider="bedrock"))
    assert isinstance(client, BedrockClient)


def test_bedrock_missing_boto3_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "boto3":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = BedrockClient(ModelConfig(provider="bedrock"))
    with pytest.raises(ModelProviderError, match="boto3"):
        client.complete([ModelMessage(role="user", content="hello")])


def test_smoke_model_client_dry_run_redacts_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    result = smoke_model_client(
        ModelConfig(
            provider="openai",
            model="gpt-test",
            api_key_env="OPENAI_API_KEY",
            base_url="https://example.com/v1",
        )
    )
    assert result["execute"] is False
    assert result["request"]["headers"]["Authorization"] == "[redacted]"


def test_smoke_model_client_execute_stream_mock() -> None:
    result = smoke_model_client(ModelConfig(), execute=True, stream=True, prompt="hello")
    assert result["ok"] is True
    assert "Mock response for: hello" in result["content"]


def test_no_api_key_header_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    client = OpenAICompatibleClient(ModelConfig(api_key_env="MISSING_API_KEY"))
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert "Authorization" not in request.headers


def test_redact_model_request_redacts_headers_and_query() -> None:
    client = GeminiClient(
        ModelConfig(provider="gemini", model="gemini-test", api_key_env="GEMINI_API_KEY")
    )
    request = client.build_request([ModelMessage(role="user", content="hello")])
    request = type(request)(
        method=request.method,
        url=f"{request.url}?key=secret-key",
        headers={"Authorization": "Bearer secret", "x-api-key": "secret"},
        json=request.json,
    )
    redacted = redact_model_request(request)
    assert redacted["headers"]["Authorization"] == "[redacted]"
    assert redacted["headers"]["x-api-key"] == "[redacted]"
    assert "key=%5Bredacted%5D" in redacted["url"]
