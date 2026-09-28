"""OAP digests: Loro must agree with the reference open-agent-profile library (SPEC 2.2).

Before 0.22 Loro hashed its defaults-filled projection, so the same file had a different digest
in Loro than in the reference library, MagAgent and Merced AI. Digests Loro stored in that form
are still accepted where they are read, re-stamped, and audited.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from oap.validate import canonical_json as reference_canonical_json
from oap.validate import load_document as reference_load
from oap.validate import profile_digest as reference_profile_digest
from oap.validate import spec_digest as reference_spec_digest
from typer.testing import CliRunner

from loro.agent_profiles import AgentProfileRegistry, load_path
from loro.agent_profiles.delta import apply_delta, create_delta
from loro.agent_profiles.digest import (
    canonical_json,
    legacy_spec_digest,
    match_spec_digest,
    profile_digest,
    spec_digest,
)
from loro.agent_profiles.errors import ConflictError
from loro.cli import app
from loro.config import LoroConfig

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "oap").glob("*.agent.*"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.name)
def test_digests_match_the_reference_library(path: Path) -> None:
    reference, _warnings = reference_load(path)
    loaded = load_path(path)
    assert profile_digest(loaded) == reference_profile_digest(reference)
    assert spec_digest(loaded) == reference_spec_digest(reference)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"b": 2, "a": 1}, '{"a":1,"b":2}'),
        ({"n": 1.0}, '{"n":1}'),
        ({"s": "é", "z": -0.0}, '{"s":"é","z":0}'),
    ],
)
def test_jcs_interoperability_vectors(value: dict, expected: str) -> None:
    """OAP SPEC Appendix B."""

    assert canonical_json(value).decode("utf-8") == expected
    assert canonical_json(value) == reference_canonical_json(value)


def _config(tmp_path: Path) -> LoroConfig:
    config = LoroConfig()
    config.agent_profiles.managed_paths = []
    config.agent_profiles.user_paths = []
    config.agent_profiles.project_paths = [str(tmp_path / "agents")]
    config.agent_profiles.proposal_path = str(tmp_path / "proposals")
    config.audit.path = str(tmp_path / "audit.jsonl")
    return config


def _create(tmp_path: Path, monkeypatch, name: str = "reviewer") -> Path:
    config = _config(tmp_path)
    monkeypatch.setattr("loro.cli.agents.load_config", lambda: config)
    result = CliRunner().invoke(
        app, ["agents", "create", name, "--output-dir", str(tmp_path / "agents")]
    )
    assert result.exit_code == 0, result.output
    return tmp_path / "agents" / f"{name}.agent.yaml"


def test_a_profile_loro_created_has_the_reference_digest(tmp_path, monkeypatch) -> None:
    """The integration finding: `loro agents create` output hashed differently in Loro."""

    path = _create(tmp_path, monkeypatch)
    reference, _warnings = reference_load(path)
    resolved = AgentProfileRegistry(_config(tmp_path).agent_profiles, cwd=tmp_path).load("reviewer")
    assert resolved.spec_digest == reference_spec_digest(reference)
    assert resolved.profile_digest == reference_profile_digest(reference)
    # The pre-0.22 digest differed, which is what the migration has to recognize.
    assert legacy_spec_digest(resolved.document) != resolved.spec_digest
    assert match_spec_digest(resolved.document, resolved.spec_digest) == "canonical"
    assert match_spec_digest(resolved.document, legacy_spec_digest(resolved.document)) == ("legacy")
    assert match_spec_digest(resolved.document, "sha256:" + "0" * 64) is None


def test_a_legacy_delta_digest_is_accepted_and_audited(tmp_path, monkeypatch) -> None:
    path = _create(tmp_path, monkeypatch)
    config = _config(tmp_path)
    document = load_path(path)
    legacy = legacy_spec_digest(document)
    events: list[tuple[str, dict]] = []
    delta = create_delta(document, legacy, "Prefers short answers.", session_id="s1")
    updated = apply_delta(
        path,
        delta,
        config.agent_profiles,
        config.safety,
        event_handler=lambda event, payload: events.append((event, dict(payload))),
    )
    assert updated.metadata.revision == 2
    migrated = [payload for event, payload in events if event == "agent_profile.digest_migrated"]
    assert migrated == [
        {
            "profile": "reviewer",
            "surface": "state_delta",
            "legacy_spec_digest": legacy,
            "spec_digest": spec_digest(document),
        }
    ]
    # A digest that is neither form is still a conflict.
    stale = create_delta(load_path(path), "sha256:" + "1" * 64, "x", session_id="s2")
    with pytest.raises(ConflictError):
        apply_delta(path, stale, config.agent_profiles, config.safety)


def test_a_conversation_pinned_with_a_legacy_digest_is_restamped(tmp_path, monkeypatch) -> None:
    from loro.webui import services
    from loro.webui.conversations import ConversationStore

    _create(tmp_path, monkeypatch)
    config = _config(tmp_path)
    resolved = AgentProfileRegistry(config.agent_profiles, cwd=tmp_path).load("reviewer")
    legacy = legacy_spec_digest(resolved.document)
    store = ConversationStore(tmp_path / "webui.sqlite3")
    conversation = store.create_conversation(
        workspace=str(tmp_path),
        profile_name="reviewer",
        profile_revision=1,
        profile_spec_digest=legacy,
    )
    manager = services.RunManager(tmp_path, store)
    manager._resolve_profile(config, "reviewer", legacy, conversation_id=conversation["id"])
    assert store.get_conversation(conversation["id"])["profile_spec_digest"] == (
        resolved.spec_digest
    )
    events = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    migrated = [event for event in events if event["event_type"] == "agent_profile.digest_migrated"]
    assert len(migrated) == 1
    assert migrated[0]["details"]["legacy_spec_digest"] == legacy
    assert migrated[0]["details"]["spec_digest"] == resolved.spec_digest
    with pytest.raises(ValueError, match="changed after this conversation started"):
        manager._resolve_profile(config, "reviewer", "sha256:" + "2" * 64)


def test_a_composed_profile_digest_still_changes_when_a_parent_changes(tmp_path) -> None:
    import shutil

    agents = tmp_path / "agents"
    agents.mkdir()
    for name in ("base-reviewer.agent.yaml", "python-reviewer.agent.yaml"):
        shutil.copy(Path(__file__).parent / "fixtures" / "oap" / name, agents / name)
    config = _config(tmp_path)
    registry = AgentProfileRegistry(config.agent_profiles, cwd=tmp_path)
    before = registry.load("python-reviewer")
    leaf, _warnings = reference_load(agents / "python-reviewer.agent.yaml")
    # Not just the leaf file: the parent's authored content is part of the contract.
    assert before.spec_digest != reference_spec_digest(leaf)
    parent = agents / "base-reviewer.agent.yaml"
    parent.write_text(
        # A field the child does not override: the parent now allows network access.
        parent.read_text(encoding="utf-8").replace("network: deny", "network: allow", 1),
        encoding="utf-8",
    )
    after = AgentProfileRegistry(config.agent_profiles, cwd=tmp_path).load("python-reviewer")
    assert after.spec_digest != before.spec_digest
