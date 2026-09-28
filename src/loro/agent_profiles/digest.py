"""OAP profile and spec digests (OAP 1.0 SPEC section 2.2).

The canonical digest is the JCS SHA-256 of the document exactly as authored: the parsed file,
including ``state`` and ``history``, with nothing filled in. The reference ``open-agent-profile``
library computes it, and MagAgent and Merced AI agree with it. Before 0.22 Loro hashed its
defaults-filled projection (``canonical_document``) instead, so the same file had a different
digest in Loro. Those legacy digests are still recognized where Loro reads a stored digest
(conversation pins and state-delta proposals) and are re-stamped with the canonical value.
"""

from __future__ import annotations

from typing import Any, Literal

from oap.validate import canonical_json as canonical_json
from oap.validate import profile_digest as _profile_digest
from oap.validate import spec_digest as _spec_digest

from loro.agent_profiles.compat import canonical_document
from loro.agent_profiles.models import AgentProfileModel

DigestMatch = Literal["canonical", "legacy"]


def _is_canonical_oap(value: Any) -> bool:
    return (
        isinstance(value, dict) and bool(value.get("oap")) and value.get("kind") == "AgentProfile"
    )


def _model(document: dict[str, Any] | AgentProfileModel) -> AgentProfileModel:
    if isinstance(document, AgentProfileModel):
        return document
    return AgentProfileModel.model_validate(document)


def source_document(document: dict[str, Any] | AgentProfileModel) -> dict[str, Any]:
    """The document a digest covers: the authored OAP file when there is one.

    A canonical OAP dict is used as is. A model loaded from a canonical OAP file carries that
    file in ``canonical_source``. Only Loro's own pre-OAP ``apiVersion`` form, which has no
    authored canonical document, falls back to the canonical projection.
    """

    if _is_canonical_oap(document):
        return document  # type: ignore[return-value]
    model = _model(document)
    if _is_canonical_oap(model.canonical_source):
        return model.canonical_source  # type: ignore[return-value]
    return canonical_document(model)


def profile_digest(document: dict[str, Any] | AgentProfileModel) -> str:
    return _profile_digest(source_document(document))


def spec_digest(document: dict[str, Any] | AgentProfileModel) -> str:
    return _spec_digest(source_document(document))


def legacy_spec_digest(document: dict[str, Any] | AgentProfileModel) -> str:
    """The pre-0.22 Loro spec digest (over the defaults-filled projection)."""

    return _spec_digest(canonical_document(_model(document)))


def legacy_profile_digest(document: dict[str, Any] | AgentProfileModel) -> str:
    """The pre-0.22 Loro profile digest (over the defaults-filled projection)."""

    return _profile_digest(canonical_document(_model(document)))


def match_spec_digest(
    document: dict[str, Any] | AgentProfileModel, stored: str | None
) -> DigestMatch | None:
    """Whether a stored spec digest names this profile, canonically or in the legacy form."""

    if not stored:
        return None
    if stored == spec_digest(document):
        return "canonical"
    if stored == legacy_spec_digest(document):
        return "legacy"
    return None
