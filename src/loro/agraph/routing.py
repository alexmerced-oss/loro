from __future__ import annotations

from dataclasses import dataclass

from loro.agraph.policy import TIER_RANK
from loro.config import LoroConfig, ModelConfig, ModelTierConfig


class RoutingError(RuntimeError):
    code = "RT011"


@dataclass(frozen=True)
class RoutingDecision:
    requested_tier: str
    effective_tier: str
    provider: str
    model: str
    downgraded: bool
    context_tokens: int | None
    api_key_env: str | None
    base_url: str | None

    def model_config(self, base: ModelConfig) -> ModelConfig:
        return base.model_copy(
            update={
                "provider": self.provider,
                "model": self.model,
                "api_key_env": self.api_key_env or base.api_key_env,
                "base_url": self.base_url or base.base_url,
            }
        )


def route_model(
    config: LoroConfig, intelligence: dict[str, object] | None, attempt: int = 1
) -> RoutingDecision:
    block = intelligence or {}
    requested = str(block.get("tier") or "standard")
    target = requested
    escalate = block.get("escalate_to")
    if attempt > 1 and escalate:
        target = str(escalate)
    tiers = config.model.tiers
    candidate = tiers.get(target)
    effective = target
    if candidate is None:
        candidate, effective = _fallback(config, target)
    downgraded = TIER_RANK[effective] < TIER_RANK[target]
    if downgraded and not bool(block.get("allow_downgrade", False)):
        raise RoutingError(f"RT011: no configured model satisfies tier {target!r}")
    minimum = block.get("min_context_tokens")
    if minimum and (candidate.context_tokens is None or candidate.context_tokens < int(minimum)):
        raise RoutingError(f"RT011: routed model does not satisfy min_context_tokens={minimum}")
    return RoutingDecision(
        target,
        effective,
        candidate.provider,
        candidate.model,
        downgraded,
        candidate.context_tokens,
        candidate.api_key_env,
        candidate.base_url,
    )


def _fallback(config: LoroConfig, target: str) -> tuple[ModelTierConfig, str]:
    if not config.model.tiers:
        model = config.model.small_model if target == "minimal" else config.model.model
        return ModelTierConfig(provider=config.model.provider, model=model), target
    candidates = sorted(config.model.tiers, key=lambda tier: TIER_RANK[tier], reverse=True)
    lower = [tier for tier in candidates if TIER_RANK[tier] <= TIER_RANK[target]]
    effective = lower[0] if lower else candidates[-1]
    return config.model.tiers[effective], effective
