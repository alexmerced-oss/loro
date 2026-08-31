from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from loro.agent_profiles import AgentProfileRegistry
from loro.config import load_config
from loro.webui.conversations import ConversationStore
from loro.webui.services import MAX_GROUP_PARTICIPANTS, ProfileService, RunManager, SettingsService
from loro.webui.workspace import (
    ContextReference,
    ScheduleInput,
    ScheduleStore,
    UploadInput,
    WorkspaceService,
)


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=200)
    profile_name: str | None = None
    participants: list[str] = Field(default_factory=list, max_length=5)
    group_mode: Literal["sequential", "parallel", "coordinator"] = "sequential"
    coordinator_profile: str | None = Field(default=None, max_length=120)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    status: Literal["active", "archived"] | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    context: list[ContextReference] = Field(default_factory=list, max_length=20)


class ApprovalResolve(BaseModel):
    decision: Literal["approve", "deny"]
    scope: Literal["once", "session"] = "once"
    decision_id: str | None = Field(default=None, max_length=200)


class AAISDecision(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "deny", "cancel"]
    scope: Literal["once", "session", "persistent"] = "once"
    decision_id: str | None = Field(default=None, max_length=200)


class GraphRunRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    dry_run: bool = False


class ProfileImport(BaseModel):
    document: dict[str, Any]
    rename: str | None = Field(default=None, max_length=120)
    scope: Literal["project", "portable", "universal", "user"] = "project"


class ProfileGenerate(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    name: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)
    extends: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)


class ConfigureRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=60)
    model: str = Field(default="", max_length=200)
    small_model: str = Field(default="", max_length=200)
    credential: str = Field(default="", max_length=20_000)


class AuditVerifyRequest(BaseModel):
    anchor: str = Field(default="", max_length=200)


class PolicyExplainRequest(BaseModel):
    request: dict[str, Any]


class GraphSaveRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    document: dict[str, Any]


class GraphBlankRequest(BaseModel):
    title: str = Field(default="New workflow", min_length=1, max_length=200)


class GraphCardRequest(BaseModel):
    document: dict[str, Any]


class GraphGenerateRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    use_ai: bool = True


class GateDecision(BaseModel):
    approved: bool


class MemoryRejection(BaseModel):
    # Optional: a declined proposal is more useful in the audit record with a
    # reason, but requiring one would just get an empty string typed in.
    reason: str = ""


class LocalMemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    scope: str = Field(default="local", min_length=1, max_length=120)


class SettingsUpdate(BaseModel):
    provider: str | None = None
    model: str | None = None
    small_model: str | None = None
    default_profile: str | None = None


class ScheduleUpdate(BaseModel):
    enabled: bool


def create_app(
    *,
    project_root: Path | None = None,
    database_path: Path | None = None,
    auth_token: str | None = None,
    static_path: Path | None = None,
    database_synchronous: str = "FULL",
):
    root = (project_root or Path.cwd()).resolve()
    db_path = database_path or root / ".loro" / "webui.sqlite3"
    store = ConversationStore(db_path, synchronous=database_synchronous)
    profiles = ProfileService(root)
    settings = SettingsService(root)
    runs = RunManager(root, store)
    workspace = WorkspaceService(root)
    sessions: dict[str, str] = {}
    app = FastAPI(title="Loro Web UI", version="1.0", docs_url=None, redoc_url=None)
    app.state.project_root = root
    app.state.store = store
    app.state.profiles = profiles
    app.state.settings = settings
    app.state.runs = runs

    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if auth_token is not None:
                supplied = request.headers.get("authorization", "")
                if not secrets.compare_digest(supplied, f"Bearer {auth_token}"):
                    return Response(status_code=401, content="Authentication required.")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                    return Response(status_code=403, content="Origin rejected.")
                session_id = request.cookies.get("loro_web_session", "")
                expected = sessions.get(session_id)
                supplied = request.headers.get("x-loro-csrf", "")
                if expected is None or not secrets.compare_digest(expected, supplied):
                    return Response(status_code=403, content="CSRF validation failed.")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; frame-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'"
        )
        return response

    def translate(error: Exception) -> HTTPException:
        if isinstance(error, KeyError):
            return HTTPException(status_code=404, detail=str(error).strip("'"))
        if isinstance(error, PermissionError):
            return HTTPException(status_code=403, detail=str(error))
        return HTTPException(status_code=400, detail=str(error))

    from loro.webui.graphs import GraphService

    graphs = GraphService(root, aais=runs.aais)
    schedules = ScheduleStore(root, lambda path: graphs.start(path))
    scheduler_stop = threading.Event()

    def schedule_loop() -> None:
        while not scheduler_stop.wait(10):
            schedules.tick()

    @app.on_event("startup")
    async def start_scheduler() -> None:
        threading.Thread(target=schedule_loop, daemon=True, name="loro-web-scheduler").start()

    @app.on_event("shutdown")
    async def stop_scheduler() -> None:
        scheduler_stop.set()

    from loro.webui.governance import GovernanceService
    from loro.webui.memory import MemoryService
    from loro.webui.onboarding import OnboardingService

    onboarding = OnboardingService(root)

    @app.get("/api/onboarding/readiness")
    async def onboarding_readiness() -> dict[str, Any]:
        return onboarding.readiness()

    @app.get("/api/onboarding/providers")
    async def onboarding_providers() -> dict[str, Any]:
        return onboarding.providers()

    @app.post("/api/onboarding/configure")
    async def onboarding_configure(payload: ConfigureRequest) -> dict[str, Any]:
        """Write the route and optionally store a credential in the OS keyring vault."""
        try:
            return onboarding.configure(
                payload.provider, payload.model, payload.small_model, payload.credential
            )
        except ValueError as error:
            raise translate(error) from error

    governance = GovernanceService(root)
    memory = MemoryService(root)

    @app.get("/api/memory/overview")
    async def memory_overview() -> dict[str, Any]:
        return memory.overview()

    @app.get("/api/memory/memories")
    async def memory_list(q: str = "", limit: int = 50) -> dict[str, Any]:
        return memory.memories(q, limit)

    @app.post("/api/memory/memories", status_code=201)
    async def memory_create(payload: LocalMemoryInput) -> dict[str, Any]:
        try:
            return memory.create_memory(payload.content, payload.scope)
        except ValueError as error:
            raise translate(error) from error

    @app.put("/api/memory/memories/{memory_id}")
    async def memory_update(memory_id: str, payload: LocalMemoryInput) -> dict[str, Any]:
        try:
            return memory.update_memory(memory_id, payload.content, payload.scope)
        except ValueError as error:
            raise translate(error) from error

    @app.delete("/api/memory/memories/{memory_id}")
    async def memory_delete(memory_id: str) -> dict[str, Any]:
        try:
            return memory.delete_memory(memory_id)
        except ValueError as error:
            raise translate(error) from error

    @app.get("/api/memory/proposals")
    async def memory_proposals(status: str = "") -> dict[str, Any]:
        return memory.proposals(status)

    @app.post("/api/memory/proposals/{proposal_id}/accept")
    async def memory_accept(proposal_id: str) -> dict[str, Any]:
        try:
            return memory.accept(proposal_id)
        except ValueError as error:
            raise translate(error) from error

    @app.post("/api/memory/proposals/{proposal_id}/reject")
    async def memory_reject(
        proposal_id: str, payload: MemoryRejection | None = None
    ) -> dict[str, Any]:
        try:
            return memory.reject(proposal_id, payload.reason if payload else "")
        except ValueError as error:
            raise translate(error) from error

    @app.get("/api/memory/shared")
    async def memory_shared(q: str = "", tenant_id: str = "", limit: int = 20) -> dict[str, Any]:
        return memory.shared_search(q, tenant_id=tenant_id, limit=limit)

    @app.get("/api/governance/status")
    async def governance_status() -> dict[str, Any]:
        return governance.status()

    @app.get("/api/governance/audit")
    async def governance_audit(
        limit: int = 100, event_type: str = "", actor: str = ""
    ) -> dict[str, Any]:
        return governance.audit(limit=limit, event_type=event_type, actor=actor)

    @app.post("/api/governance/verify")
    async def governance_verify(payload: AuditVerifyRequest) -> dict[str, Any]:
        """Recompute the audit hash chain. Read-only; nothing is written."""
        return governance.verify(payload.anchor)

    @app.post("/api/governance/explain")
    async def governance_explain(payload: PolicyExplainRequest) -> dict[str, Any]:
        try:
            return governance.explain(payload.request)
        except ValueError as error:
            raise translate(error) from error

    @app.get("/api/graphs")
    async def list_graphs() -> list[dict[str, Any]]:
        return graphs.list_graphs()

    @app.get("/api/graphs/plan")
    async def plan_graph(path: str) -> dict[str, Any]:
        try:
            return graphs.plan(path)
        except (ValueError, FileNotFoundError) as error:
            raise translate(error) from error

    @app.get("/api/graphs/document")
    async def graph_document(path: str) -> dict[str, Any]:
        """The raw graph, for editing in the board or exporting to a file."""
        try:
            return {"path": path, "document": graphs.document(path)}
        except (ValueError, FileNotFoundError) as error:
            raise translate(error) from error

    @app.post("/api/graphs/document")
    async def save_graph(payload: GraphSaveRequest) -> dict[str, Any]:
        try:
            return graphs.save(payload.path, payload.document)
        except (ValueError, FileNotFoundError) as error:
            raise translate(error) from error

    @app.post("/api/graphs/blank")
    async def blank_graph(payload: GraphBlankRequest) -> dict[str, Any]:
        return {"document": graphs.blank(payload.title)}

    @app.post("/api/graphs/card")
    async def add_graph_card(payload: GraphCardRequest) -> dict[str, Any]:
        try:
            return graphs.add_card(payload.document)
        except ValueError as error:
            raise translate(error) from error

    @app.post("/api/graphs/generate")
    async def generate_graph_document(payload: GraphGenerateRequest) -> dict[str, Any]:
        """Draft a graph from a goal, using the bundled agentic-graph skill."""
        try:
            return {"document": graphs.generate(payload.goal, use_ai=payload.use_ai)}
        except ValueError as error:
            raise translate(error) from error

    @app.post("/api/graphs/generate/start")
    async def start_graph_document(payload: GraphGenerateRequest) -> dict[str, Any]:
        try:
            return graphs.start_draft(payload.goal, use_ai=payload.use_ai)
        except ValueError as error:
            raise translate(error) from error

    @app.get("/api/graphs/generate/status/{job_id}")
    async def graph_document_status(job_id: str) -> dict[str, Any]:
        try:
            return graphs.draft_status(job_id)
        except FileNotFoundError as error:
            raise translate(error) from error

    @app.get("/api/graphs/runs")
    async def graph_history() -> list[dict[str, Any]]:
        return graphs.history()

    @app.get("/api/graphs/runs/active")
    async def graph_active() -> list[dict[str, Any]]:
        # A browser that reloaded mid-run knows nothing about the run it lost;
        # this is how it finds the live handle again.
        return graphs.active()

    @app.post("/api/graphs/runs", status_code=202)
    async def start_graph(payload: GraphRunRequest) -> dict[str, str]:
        try:
            handle = graphs.start(payload.path, dry_run=payload.dry_run)
        except (ValueError, FileNotFoundError) as error:
            raise translate(error) from error
        return {"run_id": handle.run_id}

    @app.get("/api/graphs/runs/{run_id}/events")
    async def graph_events(run_id: str, after: int = -1):
        try:
            handle = graphs.handle(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Unknown graph run.") from error

        async def generate():
            # Replay from the cursor first, so a reconnecting browser misses
            # nothing, then poll in the same shape as the chat run stream.
            cursor = max(-1, after)
            quiet_cycles = 0
            while True:
                available = handle.since(cursor)
                for event in available:
                    cursor = int(event["seq"])
                    yield (
                        f"id: {cursor}\nevent: {event['type']}\n"
                        f"data: {json.dumps(event, default=str)}\n\n"
                    )
                    if event["type"] == "run.closed":
                        return
                if not available:
                    quiet_cycles += 1
                    if quiet_cycles >= 300:
                        quiet_cycles = 0
                        yield ": keepalive\n\n"
                    await asyncio.sleep(0.05)
                else:
                    quiet_cycles = 0

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/graphs/runs/{run_id}/gates/{request_id}", status_code=202)
    async def resolve_gate(run_id: str, request_id: str, payload: GateDecision) -> dict[str, bool]:
        try:
            handle = graphs.handle(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Unknown graph run.") from error
        return {"ok": handle.decide_gate(request_id, payload.approved)}

    @app.get("/api/session")
    async def web_session(request: Request, response: Response) -> dict[str, str]:
        session_id = secrets.token_urlsafe(24)
        csrf = secrets.token_urlsafe(32)
        if len(sessions) >= 1024:
            sessions.pop(next(iter(sessions)))
        sessions[session_id] = csrf
        response.set_cookie(
            "loro_web_session",
            session_id,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return {"csrf_token": csrf, "workspace": str(root)}

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        config = load_config(root)
        return {
            "ok": True,
            "workspace": str(root),
            "provider": config.model.provider,
            "model": config.model.model,
            "default_profile": config.agent_profiles.default_profile,
        }

    @app.get("/api/workspace/files")
    async def workspace_files(q: str = "") -> dict[str, Any]:
        return {"workspace": str(root), "files": workspace.files(q)}

    @app.get("/api/workspace/file")
    async def workspace_file(path: str, download: bool = False) -> Response:
        try:
            content, media_type, name = workspace.read(path)
        except Exception as error:
            raise translate(error) from error
        safe_name = name.replace('"', "_").replace("\r", "_").replace("\n", "_")
        headers = (
            {"Content-Disposition": f'attachment; filename="{safe_name}"'} if download else None
        )
        return Response(content=content, media_type=media_type, headers=headers)

    @app.get("/api/workspace/artifacts")
    async def workspace_artifacts() -> dict[str, Any]:
        return {"artifacts": workspace.artifacts(), "changes": workspace.changes()}

    @app.get("/api/workspace/extensions")
    async def workspace_extensions() -> dict[str, Any]:
        return workspace.extensions()

    @app.post("/api/workspace/extensions")
    async def manage_workspace_extension(request: Request) -> dict[str, Any]:
        try:
            return workspace.manage_extension(await request.json())
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/workspaces")
    async def workspaces() -> dict[str, Any]:
        return {"workspaces": workspace.workspaces()}

    @app.get("/api/run-center")
    async def run_center() -> dict[str, Any]:
        return {
            "chat_runs": store.list_runs(),
            "active_chat_runs": [
                handle.snapshot() for handle in runs.handles.values() if not handle.finished
            ],
            "graph_runs": graphs.history(),
            "active_graph_runs": graphs.active(),
            "schedules": schedules.list(),
        }

    @app.get("/api/schedules")
    async def list_schedules() -> list[dict[str, Any]]:
        return schedules.list()

    @app.post("/api/schedules", status_code=201)
    async def create_schedule(payload: ScheduleInput) -> dict[str, Any]:
        try:
            graphs.plan(payload.graph_path)
            return schedules.create(payload)
        except Exception as error:
            raise translate(error) from error

    @app.patch("/api/schedules/{schedule_id}")
    async def update_schedule(schedule_id: str, payload: ScheduleUpdate) -> dict[str, Any]:
        try:
            return schedules.update(schedule_id, payload.enabled)
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/conversations")
    async def list_conversations(include_archived: bool = False) -> list[dict[str, Any]]:
        return store.list_conversations(include_archived=include_archived)

    @app.post("/api/conversations", status_code=201)
    async def create_conversation(payload: ConversationCreate) -> dict[str, Any]:
        try:
            config = load_config(root)
            registry = AgentProfileRegistry(config.agent_profiles, cwd=root, safety=config.safety)

            # A group names several profiles. Pin each one's spec digest now, so
            # a profile that changes mid-conversation is refused rather than
            # quietly speaking with different authority.
            roster = [name for name in (payload.participants or []) if name]
            if roster:
                if len(roster) > MAX_GROUP_PARTICIPANTS:
                    raise ValueError(
                        f"A group conversation allows at most {MAX_GROUP_PARTICIPANTS} profiles."
                    )
                if len(set(roster)) != len(roster):
                    raise ValueError("Each profile can join a group only once.")
                digests = {name: registry.load(name).spec_digest for name in roster}
                if (
                    payload.group_mode == "coordinator"
                    and payload.coordinator_profile not in roster
                ):
                    raise ValueError("Choose one of the group participants as coordinator.")
                return store.create_conversation(
                    title=payload.title,
                    workspace=str(root),
                    participants=roster,
                    participant_digests=digests,
                    group_mode=payload.group_mode,
                    coordinator_profile=payload.coordinator_profile,
                )

            selected = payload.profile_name or config.agent_profiles.default_profile
            revision = None
            digest = None
            if selected:
                resolved = registry.load(selected)
                revision = resolved.document.metadata.revision
                digest = resolved.spec_digest
            return store.create_conversation(
                title=payload.title,
                workspace=str(root),
                profile_name=selected,
                profile_revision=revision,
                profile_spec_digest=digest,
            )
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        try:
            return store.get_conversation(conversation_id)
        except Exception as error:
            raise translate(error) from error

    @app.patch("/api/conversations/{conversation_id}")
    async def update_conversation(
        conversation_id: str, payload: ConversationUpdate
    ) -> dict[str, Any]:
        try:
            return store.update_conversation(
                conversation_id, title=payload.title, status=payload.status
            )
        except Exception as error:
            raise translate(error) from error

    @app.delete("/api/conversations/{conversation_id}", status_code=204, response_model=None)
    async def delete_conversation(conversation_id: str):
        try:
            if runs.is_conversation_active(conversation_id):
                raise ValueError("Cancel the active run before deleting this conversation.")
            store.delete_conversation(conversation_id)
            return Response(status_code=204)
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/conversations/{conversation_id}/messages")
    async def messages(conversation_id: str, limit: int = 500) -> list[dict[str, Any]]:
        try:
            return store.list_messages(conversation_id, limit=limit)
        except Exception as error:
            raise translate(error) from error

    @app.post("/api/conversations/{conversation_id}/messages", status_code=202)
    async def create_message(conversation_id: str, payload: MessageCreate) -> dict[str, Any]:
        try:
            context_suffix, manifest = workspace.context(payload.context)
            handle = runs.start(conversation_id, payload.content + context_suffix)
            return {"run_id": handle.run_id, "context": manifest}
        except Exception as error:
            raise translate(error) from error

    @app.post("/api/conversations/{conversation_id}/attachments", status_code=201)
    async def create_attachment(conversation_id: str, payload: UploadInput) -> dict[str, Any]:
        try:
            store.get_conversation(conversation_id)
            return workspace.save_upload(conversation_id, payload)
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/conversations/{conversation_id}/active-run")
    async def active_run(conversation_id: str) -> dict[str, Any]:
        # A browser that reloaded mid-turn knows its conversation, not the run
        # id it lost; this is how it finds the live handle again.
        handle = runs.active_for(conversation_id)
        return {"run": handle.snapshot() if handle is not None else None}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        try:
            return store.get_run(run_id)
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, after: int = -1):
        try:
            handle = runs.get(run_id)
        except Exception as error:
            raise translate(error) from error

        async def generate():
            cursor = max(-1, after)
            quiet_cycles = 0
            while True:
                with handle.condition:
                    available = handle.events[cursor + 1 :]
                    done = handle.finished and not available
                for event in available:
                    cursor = int(event["sequence"])
                    yield (
                        f"id: {cursor}\nevent: {event['event']}\n"
                        f"data: {json.dumps(event['data'], default=str)}\n\n"
                    )
                if done:
                    break
                if not available:
                    quiet_cycles += 1
                    if quiet_cycles >= 300:
                        quiet_cycles = 0
                        yield ": keepalive\n\n"
                    await asyncio.sleep(0.05)
                else:
                    quiet_cycles = 0

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    async def cancel_run(run_id: str) -> dict[str, bool]:
        try:
            runs.cancel(run_id)
            return {"cancelled": True}
        except Exception as error:
            raise translate(error) from error

    @app.post("/api/runs/{run_id}/approvals/{request_id}")
    async def resolve_approval(
        run_id: str, request_id: str, payload: ApprovalResolve
    ) -> dict[str, Any]:
        try:
            scope = payload.scope if payload.decision == "approve" else None
            runs.get(run_id).resolve_approval(
                request_id,
                scope,
                decision_id=payload.decision_id,
            )
            return {"resolved": True, "decision": payload.decision, "scope": scope}
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/approvals/snapshot")
    async def approval_snapshot() -> dict[str, Any]:
        return runs.aais.snapshot()

    @app.get("/api/approvals/events")
    async def approval_events(after: int = 0) -> dict[str, Any]:
        return {"events": runs.aais.events_after(after)}

    @app.post("/api/approvals/decisions")
    async def decide_aais(payload: AAISDecision) -> dict[str, Any]:
        try:
            resolution = runs.aais.decide(
                payload.request_id,
                decision=payload.decision,
                scope=payload.scope,
                actor_id="local-user",
                decision_id=payload.decision_id,
            )
            return {"ok": True, "resolution": resolution}
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/profiles")
    async def list_profiles() -> list[dict[str, Any]]:
        try:
            return profiles.list()
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/profiles/{name}")
    async def get_profile(name: str) -> dict[str, Any]:
        try:
            return profiles.get(name)
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/profiles/{name}/effective")
    async def effective_profile(name: str) -> dict[str, Any]:
        try:
            return profiles.effective(name)
        except Exception as error:
            raise translate(error) from error

    @app.post("/api/profiles", status_code=201)
    async def create_profile(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if isinstance(payload.get("document"), dict):
                return profiles.create(
                    payload["document"], scope=str(payload.get("scope") or "project")
                )
            return profiles.create(payload)
        except Exception as error:
            raise translate(error) from error

    @app.post("/api/profiles/generate")
    async def generate_profile(payload: ProfileGenerate) -> dict[str, Any]:
        try:
            from loro.agent_profiles.generation import generate_profile_proposal
            from loro.runtime import AgentRuntime

            config = load_config(root)
            runtime = AgentRuntime(config, _profile_cwd=root)
            return generate_profile_proposal(
                payload.prompt,
                config,
                root,
                lambda author_prompt: (
                    runtime.run(author_prompt, mode="plan", session_id=None).response
                ),
                preferred_name=payload.name,
                extends=payload.extends,
                autonomous=False,
            )
        except Exception as error:
            raise translate(error) from error

    @app.put("/api/profiles/{name}")
    async def update_profile(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return profiles.update(name, payload)
        except Exception as error:
            raise translate(error) from error

    @app.delete("/api/profiles/{name}")
    async def delete_profile(name: str) -> dict[str, Any]:
        try:
            return profiles.delete(name)
        except Exception as error:
            raise translate(error) from error

    @app.post("/api/profiles/validate")
    async def validate_profile(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from loro.agent_profiles.models import AgentProfileModel

            document = AgentProfileModel.model_validate(payload)
            return {
                "ok": True,
                "name": document.metadata.name,
                "revision": document.metadata.revision,
            }
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/profiles/{name}/export")
    async def export_profile(name: str) -> dict[str, Any]:
        try:
            return profiles.export(name)
        except Exception as error:
            raise translate(error) from error

    @app.post("/api/profiles/import", status_code=201)
    async def import_profile(payload: ProfileImport) -> dict[str, Any]:
        try:
            document = dict(payload.document)
            if payload.rename:
                metadata = dict(document.get("metadata") or {})
                metadata["name"] = payload.rename
                document["metadata"] = metadata
            return profiles.create(document, scope=payload.scope)
        except Exception as error:
            raise translate(error) from error

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return settings.get()

    @app.patch("/api/settings")
    async def update_settings(payload: SettingsUpdate) -> dict[str, Any]:
        try:
            values = payload.model_dump(exclude_unset=True)
            return settings.update(values)
        except Exception as error:
            raise translate(error) from error

    assets = static_path or Path(__file__).parent / "static"
    index = assets / "index.html"
    if assets.exists():

        @app.get("/{path:path}")
        async def frontend(path: str):
            candidate = assets / path
            contained = candidate.resolve().is_relative_to(assets.resolve())
            if path and candidate.is_file() and contained:
                media_type = mimetypes.guess_type(candidate.name)[0]
                return Response(content=candidate.read_bytes(), media_type=media_type)
            return Response(content=index.read_bytes(), media_type="text/html")

    return app


__all__ = ["create_app"]
