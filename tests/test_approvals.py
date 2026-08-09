from datetime import UTC, datetime, timedelta

import pytest

from loro.approvals import (
    ApprovalExpiredError,
    ApprovalManager,
    ApprovalReplayError,
)
from loro.config import ApprovalsConfig
from loro.identity import IdentityContext


def test_one_time_approval_is_bound_and_cannot_be_replayed() -> None:
    manager = ApprovalManager(ApprovalsConfig(), _identity())
    request = _request(manager, content="first")
    record = manager.grant(request, scope="once")

    manager.consume(request, record.approval_id)

    assert record.status == "used"
    assert record.use_count == 1
    with pytest.raises(ApprovalReplayError, match="already been used"):
        manager.consume(request, record.approval_id)


def test_changed_arguments_invalidate_approval() -> None:
    manager = ApprovalManager(ApprovalsConfig(), _identity())
    original = _request(manager, content="first")
    changed = _request(manager, content="second")
    record = manager.grant(original)

    with pytest.raises(ApprovalReplayError, match="arguments do not match"):
        manager.consume(changed, record.approval_id)


def test_changed_policy_version_invalidates_approval() -> None:
    manager = ApprovalManager(ApprovalsConfig(), _identity())
    original = _request(manager, content="first", policy_version="policy-v1")
    changed = _request(manager, content="first", policy_version="policy-v2")
    record = manager.grant(original)

    with pytest.raises(ApprovalReplayError, match="arguments do not match"):
        manager.consume(changed, record.approval_id)


def test_approval_cannot_cross_identity_session() -> None:
    manager = ApprovalManager(ApprovalsConfig(), _identity(session_id="session-1"))
    request = _request(manager, content="first")
    record = manager.grant(request)
    other_manager = ApprovalManager(
        ApprovalsConfig(),
        _identity(session_id="session-2"),
    )
    other_manager.records[record.approval_id] = record
    other_request = _request(other_manager, content="first")

    with pytest.raises(ApprovalReplayError, match="session does not match"):
        other_manager.consume(other_request, record.approval_id)


def test_manager_rejects_request_created_for_another_identity() -> None:
    first = ApprovalManager(ApprovalsConfig(), _identity(session_id="session-1"))
    other = ApprovalManager(ApprovalsConfig(), _identity(session_id="session-2"))
    request = _request(first, content="first")

    with pytest.raises(ApprovalReplayError, match="session does not match"):
        other.grant(request)


def test_expired_approval_fails_and_emits_event() -> None:
    events: list[str] = []
    manager = ApprovalManager(
        ApprovalsConfig(once_ttl_seconds=5),
        _identity(),
        event_handler=lambda event_type, payload: events.append(event_type),
    )
    request = _request(manager, content="first")
    granted_at = datetime(2026, 8, 8, tzinfo=UTC)
    record = manager.grant(request, now=granted_at)

    with pytest.raises(ApprovalExpiredError, match="expired"):
        manager.consume(
            request,
            record.approval_id,
            now=granted_at + timedelta(seconds=6),
        )

    assert record.status == "expired"
    assert events == ["approval.requested", "approval.granted", "approval.expired"]


def test_session_approval_reuses_only_exact_request() -> None:
    manager = ApprovalManager(ApprovalsConfig(), _identity())
    request = _request(manager, content="first")
    changed = _request(manager, content="second")
    record = manager.grant(request, scope="session")

    assert manager.consume(request, record.approval_id) is record
    assert manager.consume_matching_session(request) is record
    assert manager.consume_matching_session(changed) is None
    assert record.status == "active"
    assert record.use_count == 2


def test_managed_policy_can_disable_non_interactive_and_session_approval() -> None:
    manager = ApprovalManager(
        ApprovalsConfig(allow_non_interactive=False, allow_session_scope=False),
        _identity(),
    )
    request = _request(manager, content="first")

    with pytest.raises(PermissionError, match="Non-interactive approvals are disabled"):
        manager.grant(request, method="non_interactive")
    with pytest.raises(PermissionError, match="Session-scoped approvals are disabled"):
        manager.grant(request, scope="session")


def test_approval_events_include_request_and_record_context() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    manager = ApprovalManager(
        ApprovalsConfig(),
        _identity(),
        event_handler=lambda event_type, payload: events.append((event_type, dict(payload))),
    )
    request = _request(manager, content="first")
    record = manager.grant(request)
    manager.consume(request, record.approval_id)

    granted = events[1][1]
    used = events[2][1]
    assert granted["action"] == "edit.write file"
    assert granted["target"] == "/workspace/note.txt"
    assert granted["approval_id"] == record.approval_id
    assert used["status"] == "used"


def test_denial_emits_auditable_event() -> None:
    events: list[str] = []
    manager = ApprovalManager(
        ApprovalsConfig(),
        _identity(),
        event_handler=lambda event_type, payload: events.append(event_type),
    )
    request = _request(manager, content="first")

    manager.deny(request)

    assert events == ["approval.requested", "approval.denied"]


def test_approval_preview_recursively_redacts_credentials() -> None:
    manager = ApprovalManager(ApprovalsConfig(), _identity())
    request = manager.request(
        action="mcp.call tool",
        target="fixture",
        arguments={
            "server_id": "fixture",
            "arguments": {"api_key": "secret-value", "nested": {"password": "hidden"}},
        },
        policy_decision="ask",
        policy_reason="MCP uses configured policy",
        risk_reason="Invoke a remote tool.",
    )

    preview = request.display_arguments()

    assert "secret-value" not in preview
    assert "hidden" not in preview
    assert preview.count("[REDACTED]") == 2


def _request(
    manager: ApprovalManager,
    *,
    content: str,
    policy_version: str = "local-v1",
):
    return manager.request(
        action="edit.write file",
        target="/workspace/note.txt",
        arguments={"path": "/workspace/note.txt", "content": content},
        policy_decision="ask",
        policy_version=policy_version,
        policy_source="permissions.edit",
        policy_reason="edit uses configured policy",
        risk_reason="Write a file.",
    )


def _identity(*, session_id: str = "session-1") -> IdentityContext:
    return IdentityContext(
        subject="user-123",
        display_name="Alex",
        organization="acme",
        tenant="platform",
        groups=("engineering",),
        roles=("developer",),
        auth_method="oidc",
        session_id=session_id,
        source="managed",
    )
