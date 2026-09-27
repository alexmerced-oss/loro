"""Provider defaults reach the run path, not only `loro providers smoke` (L-8 regression)."""

from __future__ import annotations

import pytest

from loro.config import ModelConfig
from loro.models import ModelMessage, ModelProviderError, create_model_client

MESSAGES = [ModelMessage(role="user", content="hi")]


def test_named_provider_uses_its_profile_base_url_and_key_env(monkeypatch) -> None:
    # A config naming only provider and model used to fall back to api.openai.com with no key,
    # so Nous returned OpenAI's "Incorrect API key" 401 while `providers smoke` worked.
    monkeypatch.setenv("NOUS_API_KEY", "test-nous-key")
    config = ModelConfig(provider="nous", model="deepseek/deepseek-v4-flash")

    request = create_model_client(config).build_request(MESSAGES)

    assert request.url.startswith("https://inference-api.nousresearch.com/v1/")
    assert request.headers["Authorization"] == "Bearer test-nous-key"


def test_explicit_settings_still_win(monkeypatch) -> None:
    monkeypatch.setenv("NOUS_API_KEY", "profile-key")
    monkeypatch.setenv("MY_GATEWAY_KEY", "gateway-key")
    config = ModelConfig(
        provider="nous",
        model="m",
        base_url="https://gateway.internal/v1",
        api_key_env="MY_GATEWAY_KEY",
    )

    request = create_model_client(config).build_request(MESSAGES)

    assert request.url.startswith("https://gateway.internal/v1/")
    assert request.headers["Authorization"] == "Bearer gateway-key"


def test_placeholder_profile_base_url_asks_for_configuration() -> None:
    config = ModelConfig(provider="azure-openai", model="gpt")
    with pytest.raises(ModelProviderError, match="model.base_url"):
        create_model_client(config).build_request(MESSAGES)
