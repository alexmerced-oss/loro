"""Helpers shared by several CLI command families."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
import typer
from rich.console import Console
from rich.live import Live
from rich.text import Text

from loro.approvals import ApprovalManager, ApprovalRequest, ApprovalScope
from loro.artifacts.briefs import create_brief_artifact
from loro.artifacts.common import ArtifactResult, write_provenance
from loro.artifacts.generation import (
    ArtifactPayload,
    BriefPayload,
    DocumentPayload,
    PresentationPayload,
    SpreadsheetPayload,
    brief_draft,
    document_draft,
    generation_prompt,
    parse_generated_payload,
    presentation_draft,
    repair_generation_prompt,
)
from loro.audit import AuditLogger, prompt_preview
from loro.config import (
    LoroConfig,
    load_config,
)
from loro.data_protection import DataProtectionEngine, DataSurface
from loro.identity import (
    IdentityConfigurationError,
    IdentityContext,
    resolve_identity,
)
from loro.memory.drafts import SharedMemoryDraftStore
from loro.permissions import PermissionEngine, PermissionRequest
from loro.resources import (
    NormalizedResource,
)
from loro.runtime import AgentRuntime, RuntimeEventHandler

console = Console()


DEFAULT_ARTIFACT_DIR = Path("artifacts")


def _runtime(
    agent_name: str | None = None,
    approval_provider: Callable[[ApprovalRequest], ApprovalScope | None] | None = None,
) -> AgentRuntime:
    config = load_config()
    profile = None
    selected_agent = agent_name or config.agent_profiles.default_profile
    if selected_agent is not None:
        from loro.agent_profiles import AgentProfileRegistry, ProfileError, build_effective_profile

        try:
            profile = build_effective_profile(
                AgentProfileRegistry(config.agent_profiles, safety=config.safety).load(
                    selected_agent
                ),
                config,
            )
        except ProfileError as error:
            raise typer.BadParameter(str(error)) from error
    try:
        return AgentRuntime(
            config,
            approval_provider=approval_provider
            or (
                lambda request: _prompt_for_approval(
                    request,
                    allow_session_scope=config.approvals.allow_session_scope,
                )
            )
            if config.approvals.interactive
            else None,
            profile=profile,
        )
    except IdentityConfigurationError as error:
        raise typer.BadParameter(str(error)) from error


def _identity() -> IdentityContext:
    try:
        return resolve_identity(load_config().identity)
    except IdentityConfigurationError as error:
        raise typer.BadParameter(str(error)) from error


def _audit() -> AuditLogger:
    config = load_config()
    try:
        identity = resolve_identity(config.identity)
    except IdentityConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    return AuditLogger(config.audit, identity, safety_config=config.safety)


def _permissions() -> PermissionEngine:
    return PermissionEngine(load_config().permissions)


def _data_protection() -> DataProtectionEngine:
    return DataProtectionEngine(load_config().safety)


def _shared_memory_tenant(config: LoroConfig, requested: str | None) -> str:
    identity_tenant = _identity().tenant
    resolved = requested or identity_tenant
    if config.memory.shared.tenant_isolation == "identity" and resolved != identity_tenant:
        _audit().write(
            "memory.tenant_denied",
            requested_tenant=resolved,
            identity_tenant=identity_tenant,
            decision="deny",
            policy_source="memory.shared.tenant_isolation",
        )
        raise typer.BadParameter(f"Cross-tenant shared-memory access denied: {resolved}")
    return resolved


def _shared_draft_store(config: LoroConfig) -> SharedMemoryDraftStore:
    authorized_tenant = (
        _identity().tenant if config.memory.shared.tenant_isolation == "identity" else None
    )
    return SharedMemoryDraftStore(
        Path(config.memory.local.path), authorized_tenant_id=authorized_tenant
    )


def _approval_manager(config: LoroConfig | None = None) -> ApprovalManager:
    resolved_config = config or load_config()
    try:
        identity = resolve_identity(resolved_config.identity)
    except IdentityConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    audit = AuditLogger(resolved_config.audit, identity, safety_config=resolved_config.safety)
    return ApprovalManager(
        resolved_config.approvals,
        identity,
        event_handler=lambda event_type, payload: audit.write(event_type, **dict(payload)),
    )


def _prompt_for_approval(
    request: ApprovalRequest,
    *,
    allow_session_scope: bool,
) -> ApprovalScope | None:
    console.print("[bold yellow]Approval required[/bold yellow]")
    console.print(f"Action: {request.action}")
    console.print(f"Target: {request.target}")
    console.print(f"Arguments: {request.display_arguments()}")
    console.print(f"Policy: {request.policy_decision} ({request.policy_reason})")
    console.print(f"Policy source: {request.policy_source} @ {request.policy_version}")
    console.print(f"Risk: {request.risk_reason}")
    console.print(
        f"Identity: {request.identity_subject} / {request.identity_tenant} "
        f"(session {request.identity_session_id})"
    )
    choices = "once/session/deny" if allow_session_scope else "once/deny"
    choice = typer.prompt(f"Approval ({choices})", default="deny").strip().casefold()
    if choice == "once":
        return "once"
    if choice == "session" and allow_session_scope:
        return "session"
    return None


def _authorize_cli_action(
    *,
    tool: str,
    action: str,
    target: str,
    arguments: dict[str, Any],
    risk_reason: str,
    non_interactive_approved: bool = False,
    resource: NormalizedResource | None = None,
) -> None:
    config = load_config()
    permission_request = PermissionRequest(
        tool=tool,
        action=action,
        target=target,
        resource=resource,
    )
    result = PermissionEngine(config.permissions).evaluate(permission_request)
    if result.decision == "deny":
        raise typer.BadParameter(f"{tool} is denied by policy: {action}")
    if result.decision == "allow":
        return
    manager = _approval_manager(config)
    request = manager.request(
        action=f"{tool}.{action}",
        target=target,
        arguments=arguments,
        policy_decision=result.decision,
        policy_version=result.policy_version,
        policy_source=result.policy_source,
        policy_reason=result.reason,
        risk_reason=risk_reason,
    )
    if non_interactive_approved:
        try:
            record = manager.grant(request, scope="once", method="non_interactive")
            manager.consume(request, record.approval_id)
        except PermissionError as error:
            manager.deny(request)
            raise typer.BadParameter(str(error)) from error
        return
    if not config.approvals.interactive:
        manager.deny(request)
        raise typer.BadParameter(f"{tool} requires trusted user approval.")
    try:
        scope = _prompt_for_approval(
            request,
            allow_session_scope=config.approvals.allow_session_scope,
        )
    except click.Abort:
        manager.deny(request)
        raise
    if scope is None:
        manager.deny(request)
        raise typer.Abort()
    record = manager.grant(request, scope=scope, method="interactive")
    manager.consume(request, record.approval_id)


def _enforce_safe_content(content: str, context: str, allow_sensitive: bool = False) -> None:
    surface = _surface_for_context(context)
    decision = _data_protection().evaluate(content, surface, allow_sensitive=allow_sensitive)
    findings = list(decision.findings)
    if not findings:
        return
    _audit().write(
        "safety.findings_detected",
        context=context,
        finding_kinds=sorted({finding.kind for finding in findings}),
    )
    if decision.blocked:
        kinds = ", ".join(sorted({finding.kind for finding in findings}))
        raise typer.BadParameter(
            f"Sensitive content detected ({kinds}). Re-run with --allow-sensitive "
            "only if policy allows storing this content."
        )


def _surface_for_context(context: str) -> DataSurface:
    if context.startswith("memory.shared"):
        return "memory_shared"
    if context.startswith("memory."):
        return "memory_local"
    if context.startswith("session"):
        return "session_message"
    return "artifact"


def _print_artifact_result(result: ArtifactResult, prompt: str) -> None:
    provenance_path = write_provenance(result=result, prompt_preview=prompt_preview(prompt))
    _audit().write(
        "artifact.created",
        kind=result.kind,
        title=result.title,
        paths=[str(path) for path in result.paths],
        provenance_path=str(provenance_path),
        prompt_preview=prompt_preview(prompt),
    )
    console.print(result.summary)
    console.print(f"Provenance: {provenance_path}")


def _create_and_print_artifact(
    *,
    prompt: str,
    output_dir: Path,
    allow_sensitive: bool,
    context: str,
    factory: Callable[..., ArtifactResult],
    kind: str,
    use_ai: bool = True,
    brief_type: str | None = None,
    agent_name: str | None = None,
) -> None:
    _enforce_safe_content(prompt, context=context, allow_sensitive=allow_sensitive)
    if use_ai:
        config = load_config()
        console.print(f"Drafting {kind} with {config.model.provider}/{config.model.model}...")
    draft = (
        _generate_artifact_draft(kind, prompt, brief_type=brief_type, agent_name=agent_name)
        if use_ai
        else None
    )
    if draft is not None:
        _enforce_safe_content(
            draft.model_dump_json(), context=context, allow_sensitive=allow_sensitive
        )
    if isinstance(draft, DocumentPayload):
        result = factory(prompt, output_dir, draft=document_draft(draft))
    elif isinstance(draft, PresentationPayload):
        result = factory(prompt, output_dir, outline=presentation_draft(draft))
    elif isinstance(draft, SpreadsheetPayload):
        result = factory(prompt, output_dir, draft=draft)
    elif isinstance(draft, BriefPayload):
        result = factory(prompt, output_dir, draft=brief_draft(draft))
    else:
        result = factory(prompt, output_dir)
    _print_artifact_result(result, prompt)


def _generate_artifact_draft(
    kind: str,
    prompt: str,
    *,
    brief_type: str | None = None,
    agent_name: str | None = None,
) -> ArtifactPayload:
    config = load_config()
    request = generation_prompt(kind, prompt, brief_type=brief_type)
    result = _run_task(request, mode="run", session_id=None, stream=False, agent_name=agent_name)
    response = getattr(result, "response", None) or result.summary
    try:
        return parse_generated_payload(response, expected_kind=kind)
    except ValueError as first_error:
        if config.model.provider == "mock":
            raise typer.BadParameter(
                "AI artifact creation requires a configured model provider; the resolved "
                "provider is mock. Run `loro configure`, then retry. Use --no-ai only when "
                "you explicitly want the offline scaffold. No artifact was written."
            ) from first_error
        console.print("Model draft failed validation; requesting one corrected draft...")
        retry = _run_task(
            repair_generation_prompt(
                kind,
                prompt,
                str(first_error),
                brief_type=brief_type,
            ),
            mode="run",
            session_id=None,
            stream=False,
            agent_name=agent_name,
        )
        retry_response = getattr(retry, "response", None) or retry.summary
        try:
            return parse_generated_payload(retry_response, expected_kind=kind)
        except ValueError as second_error:
            raise typer.BadParameter(
                f"The model could not produce a valid {kind} draft after one correction: "
                f"{second_error}. No artifact was written."
            ) from second_error


def _create_and_print_brief(
    *,
    prompt: str,
    output_dir: Path,
    allow_sensitive: bool,
    brief_type: str,
    use_ai: bool = True,
) -> None:
    _create_and_print_artifact(
        prompt=prompt,
        output_dir=output_dir,
        allow_sensitive=allow_sensitive,
        context="artifact.brief",
        kind="brief",
        use_ai=use_ai,
        brief_type=brief_type,
        agent_name=None,
        factory=lambda artifact_prompt, artifact_dir, **kwargs: create_brief_artifact(
            artifact_prompt,
            artifact_dir,
            brief_type=brief_type,
            draft=kwargs.get("draft"),
        ),
    )


def _run_task(
    prompt: str,
    *,
    mode: str,
    session_id: str | None,
    stream: bool,
    agent_name: str | None = None,
    on_token: Callable[[str], None] | None = None,
    on_event: RuntimeEventHandler | None = None,
    approval_provider: Callable[[ApprovalRequest], ApprovalScope | None] | None = None,
):
    """Run one agent task, live-rendering tokens when streaming is requested."""

    runtime = _runtime(agent_name, approval_provider=approval_provider)
    if on_token is not None or on_event is not None:
        return runtime.run(
            prompt,
            mode=mode,
            session_id=session_id,
            on_token=on_token if stream else None,
            on_event=on_event,
        )
    if not stream:
        return runtime.run(prompt, mode=mode, session_id=session_id)
    with Live(Text(""), console=console, refresh_per_second=12, transient=True) as live:
        buffer: list[str] = []

        def on_token(chunk: str) -> None:
            buffer.append(chunk)
            live.update(Text("".join(buffer)))

        result = runtime.run(prompt, mode=mode, session_id=session_id, on_token=on_token)
    return result
