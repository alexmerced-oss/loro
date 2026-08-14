from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loro.budgets import _usage_tokens
from loro.config import ModelConfig
from loro.model_tools import parse_provider_tool_calls
from loro.models import (
    AnthropicClient,
    GeminiClient,
    ModelMessage,
    ModelProviderError,
    ModelResponse,
    OpenAICompatibleClient,
)
from loro.provider_contracts import ProviderContractError, validate_provider_contracts

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "providers"
MATRIX = ROOT / "docs" / "interoperability-matrix.json"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_provider_contract_matrix_covers_profiles_and_required_cases() -> None:
    report = validate_provider_contracts(MATRIX, FIXTURES)
    assert report.release_line == "0.12"
    assert set(report.protocols) == {
        "openai-compatible",
        "anthropic",
        "gemini",
        "bedrock",
    }
    assert {"openai", "nous", "opencode-zen", "anthropic", "gemini", "bedrock"} <= set(
        report.profiles
    )


def test_contract_validator_rejects_unsanitized_fixture(tmp_path: Path) -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    for source in FIXTURES.glob("*.json"):
        (fixture_root / source.name).write_bytes(source.read_bytes())
    fixture = _fixture("anthropic")
    fixture["sanitized"] = False
    (fixture_root / "anthropic.json").write_text(json.dumps(fixture), encoding="utf-8")
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(ProviderContractError, match="not marked sanitized"):
        validate_provider_contracts(path, fixture_root)


@pytest.mark.parametrize(
    ("protocol", "client_type", "provider"),
    [
        ("openai-compatible", OpenAICompatibleClient, "openai"),
        ("anthropic", AnthropicClient, "anthropic"),
        ("gemini", GeminiClient, "gemini"),
    ],
)
def test_sanitized_completion_tool_usage_and_malformed_contracts(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    client_type: type,
    provider: str,
) -> None:
    cases = _fixture(protocol)["cases"]
    client = client_type(ModelConfig(provider=provider, model="fixture-model"))
    messages = [ModelMessage(role="user", content="fixture")]

    monkeypatch.setattr(client, "_send", lambda _request: cases["completion"]["response"])
    assert client.complete(messages).content == "fixture-ok"

    monkeypatch.setattr(client, "_send", lambda _request: cases["native_tools"]["response"])
    tool_response = client.complete(messages)
    assert tool_response.tool_calls[0].name == "file__read"

    usage_payload = cases["usage"]
    assert _usage_tokens(ModelResponse(content="ok", raw=usage_payload["response"])) == (
        usage_payload["expected"]["input_tokens"],
        usage_payload["expected"]["output_tokens"],
    )

    monkeypatch.setattr(client, "_send", lambda _request: cases["malformed"]["response"])
    with pytest.raises(ModelProviderError, match=re.escape(cases["malformed"]["error_contains"])):
        client.complete(messages)


@pytest.mark.parametrize("protocol", ["openai-compatible", "anthropic", "gemini", "bedrock"])
def test_sanitized_native_tool_fixtures_parse(protocol: str) -> None:
    payload = _fixture(protocol)["cases"]["native_tools"]["response"]
    calls = parse_provider_tool_calls(protocol, payload)
    assert [(call.name, call.args) for call in calls] == [("file__read", {"path": "README.md"})]


@pytest.mark.parametrize(
    ("protocol", "client_type", "provider"),
    [
        ("openai-compatible", OpenAICompatibleClient, "openai"),
        ("anthropic", AnthropicClient, "anthropic"),
    ],
)
def test_sanitized_native_stream_contracts(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    client_type: type,
    provider: str,
) -> None:
    events = _fixture(protocol)["cases"]["streaming"]["events"]
    client = client_type(ModelConfig(provider=provider, model="fixture-model"))
    monkeypatch.setattr(client, "_stream_events", lambda _messages: iter(events))
    chunks: list[str] = []
    response = client.stream_complete([ModelMessage(role="user", content="fixture")], chunks.append)
    assert response.content == "fixture-ok"
    assert chunks == ["fixture-", "ok"]


def test_sanitized_gemini_and_bedrock_stream_fallback_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini = GeminiClient(ModelConfig(provider="gemini", model="fixture-model"))
    payload = _fixture("gemini")["cases"]["completion"]["response"]
    monkeypatch.setattr(gemini, "_send", lambda _request: payload)
    assert "".join(gemini.stream([ModelMessage(role="user", content="fixture")])) == "fixture-ok"

    from loro.models import BedrockClient

    bedrock = BedrockClient(ModelConfig(provider="bedrock", model="fixture-model"))
    monkeypatch.setattr(
        bedrock,
        "complete",
        lambda _messages: ModelResponse(content="fixture-ok"),
    )
    assert "".join(bedrock.stream([ModelMessage(role="user", content="fixture")])) == "fixture-ok"


def test_retryable_status_contracts_are_bounded_and_exclude_terminal_errors() -> None:
    for protocol in ("openai-compatible", "anthropic", "gemini"):
        retry = _fixture(protocol)["cases"]["retryable_error"]
        assert retry["statuses"]
        assert all(status == 429 or status >= 500 for status in retry["statuses"])
        assert retry["terminal_status"] < 500 and retry["terminal_status"] != 429
