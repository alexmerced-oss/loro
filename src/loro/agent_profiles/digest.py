from __future__ import annotations

from typing import Any

from oap.validate import canonical_json as canonical_json
from oap.validate import profile_digest as _profile_digest
from oap.validate import spec_digest as _spec_digest

from loro.agent_profiles.compat import canonical_document
from loro.agent_profiles.models import AgentProfileModel


def profile_digest(document: dict[str, Any] | AgentProfileModel) -> str:
    value = (
        document
        if isinstance(document, AgentProfileModel)
        else AgentProfileModel.model_validate(document)
    )
    return _profile_digest(canonical_document(value))


def spec_digest(document: dict[str, Any] | AgentProfileModel) -> str:
    value = (
        document
        if isinstance(document, AgentProfileModel)
        else AgentProfileModel.model_validate(document)
    )
    return _spec_digest(canonical_document(value))
