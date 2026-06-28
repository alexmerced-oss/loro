import pytest

from loro.config import ModelConfig
from loro.models import (
    AnthropicClient,
    GeminiClient,
    MockModelClient,
    ModelMessage,
    OllamaClient,
    OpenAICompatibleClient,
    create_model_client,
    redact_model_request,
)


def test_mock_model_client_complete() -> None:
    client = MockModelClient(ModelConfig())
    response = client.complete([ModelMessage(role="user", content="hello")])
    assert response.content == "Mock response for: hello"


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


def test_anthropic_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = AnthropicClient(
        ModelConfig(provider="anthropic", model="claude-test", api_key_env="ANTHROPIC_API_KEY")
    )
    request = client.build_request([ModelMessage(role="user", content="hello")])
    assert request.url == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert request.json["max_tokens"] == 4096


def test_gemini_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = GeminiClient(
        ModelConfig(provider="gemini", model="gemini-test", api_key_env="GEMINI_API_KEY")
    )
    request = client.build_request([ModelMessage(role="assistant", content="hello")])
    assert "gemini-test:generateContent?key=test-key" in request.url
    assert request.json["contents"][0]["role"] == "model"


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


def test_create_model_client_rejects_bedrock_until_sdk_exists() -> None:
    with pytest.raises(NotImplementedError):
        create_model_client(ModelConfig(provider="bedrock"))


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
