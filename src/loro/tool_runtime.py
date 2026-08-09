import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from loro.approvals import ApprovalManager, ApprovalRequest, ApprovalScope
from loro.artifacts.briefs import create_brief_artifact
from loro.artifacts.common import ArtifactResult, write_provenance
from loro.artifacts.documents import create_document_artifact
from loro.artifacts.presentations import create_presentation_artifact
from loro.artifacts.spreadsheets import create_spreadsheet_artifact
from loro.audit import prompt_preview
from loro.config import LoroConfig
from loro.data_protection import DataProtectionDecision, DataProtectionEngine
from loro.identity import IdentityContext, resolve_identity
from loro.mcp import MCPRegistry, MCPService
from loro.mcp.registry import server_endpoint_for_display
from loro.memory.local import LocalMemoryStore
from loro.memory.operations import search_shared_memories
from loro.permissions import PermissionEngine, PermissionRequest
from loro.polaris import PolarisClient
from loro.resources import (
    filesystem_resource,
    git_resource,
    mcp_resource,
    memory_resource,
    polaris_resource,
    session_message_resource,
    shell_resource,
)
from loro.session_messages import SessionMailbox, message_digest
from loro.skills import SkillRegistry
from loro.tools.files import FileTools
from loro.tools.git import GitTools
from loro.tools.shell import ShellTools


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    origin: Literal["user", "model"] = "user"


@dataclass(frozen=True)
class ToolExecution:
    call: ToolCall
    ok: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "tool": self.call.name,
            "args": self.call.args,
            "origin": self.call.origin,
            "ok": self.ok,
            "output": self.output,
            "metadata": self.metadata,
        }


class ToolRegistry:
    """Executes explicit, typed tool calls for the runtime loop."""

    def __init__(
        self,
        config: LoroConfig,
        identity: IdentityContext | None = None,
        approval_manager: ApprovalManager | None = None,
        approval_provider: Callable[[ApprovalRequest], ApprovalScope | None] | None = None,
        mcp_service: MCPService | None = None,
        active_session_id: str | None = None,
    ) -> None:
        self.config = config
        self.identity = identity or resolve_identity(config.identity)
        self.approvals = approval_manager or ApprovalManager(config.approvals, self.identity)
        self.approval_provider = approval_provider
        self.permissions = PermissionEngine(config.permissions)
        self.files = FileTools()
        self.git = GitTools(
            config.sandbox,
            workspace_roots=config.permissions.workspace_roots,
        )
        self.shell = ShellTools(
            config.sandbox,
            workspace_roots=config.permissions.workspace_roots,
        )
        self.protection = DataProtectionEngine(config.safety)
        self.mcp = mcp_service or MCPService(
            config.mcp,
            sandbox_config=config.sandbox,
            workspace_roots=config.permissions.workspace_roots,
        )
        self.skills = SkillRegistry(config.skills)
        self.mailbox = SessionMailbox(config.sessions, config.safety)
        self.active_session_id = active_session_id

    def execute(self, call: ToolCall) -> ToolExecution:
        execution = self._execute(call)
        decision = self.protection.evaluate(execution.output, "tool_output")
        if decision.blocked:
            return ToolExecution(
                call=call,
                ok=False,
                output="Data protection blocked tool output.",
                metadata={**execution.metadata, "data_protection": decision.metadata()},
            )
        if decision.redacted:
            return replace(
                execution,
                output=decision.content,
                metadata={**execution.metadata, "data_protection": decision.metadata()},
            )
        return execution

    def _execute(self, call: ToolCall) -> ToolExecution:
        try:
            if call.name == "file.read":
                return self._read_file(call)
            if call.name == "file.search":
                return self._search_files(call)
            if call.name == "file.write":
                return self._write_file(call)
            if call.name == "file.replace":
                return self._replace_file(call)
            if call.name.startswith("git."):
                return self._run_git(call)
            if call.name == "shell.run":
                return self._run_shell(call)
            if call.name == "memory.search":
                return self._search_memory(call)
            if call.name == "memory.shared_search":
                return self._search_shared_memory(call)
            if call.name == "polaris.readonly":
                return self._run_polaris_readonly(call)
            if call.name == "artifact.create":
                return self._create_artifact(call)
            if call.name.startswith("mcp."):
                return self._run_mcp(call)
            if call.name.startswith("skill."):
                return self._run_skill(call)
            if call.name.startswith("session."):
                return self._run_session_message(call)
            return ToolExecution(call=call, ok=False, output=f"Unknown tool: {call.name}")
        except Exception as error:
            return ToolExecution(call=call, ok=False, output=str(error))

    def _read_file(self, call: ToolCall) -> ToolExecution:
        resource = filesystem_resource(
            str(call.args["path"]),
            operation="read",
            workspace_roots=self.config.permissions.workspace_roots,
        )
        path = Path(str(resource.fields["path"]))
        limit = int(call.args.get("limit", 20000))
        self.permissions.require_allowed(
            PermissionRequest(
                tool="edit",
                action="read file",
                target=str(path),
                resource=resource,
            ),
            approved=True,
        )
        return ToolExecution(
            call=call,
            ok=True,
            output=self.files.read_text(path, limit=limit),
        )

    def _search_files(self, call: ToolCall) -> ToolExecution:
        query = str(call.args["query"])
        resource = filesystem_resource(
            str(call.args.get("root", ".")),
            operation="search",
            workspace_roots=self.config.permissions.workspace_roots,
        )
        root = Path(str(resource.fields["path"]))
        limit = int(call.args.get("limit", 50))
        self.permissions.require_allowed(
            PermissionRequest(
                tool="edit",
                action="search files",
                target=str(root),
                resource=resource,
            ),
            approved=True,
        )
        matches = self.files.search(root=root, query=query, limit=limit)
        output = "\n".join(f"{match.path}:{match.line_number}: {match.line}" for match in matches)
        return ToolExecution(call=call, ok=True, output=output or "No matches.")

    def _write_file(self, call: ToolCall) -> ToolExecution:
        resource = filesystem_resource(
            str(call.args["path"]),
            operation="write",
            workspace_roots=self.config.permissions.workspace_roots,
        )
        path = Path(str(resource.fields["path"]))
        content = str(call.args["content"])
        append = bool(call.args.get("append", False))
        allow_sensitive = bool(call.args.get("allow_sensitive", False))
        self._authorize(
            call,
            PermissionRequest(
                tool="edit",
                action="write file",
                target=str(path),
                resource=resource,
            ),
            approval_target=resource.target,
            risk_reason="Write or append content to a filesystem path.",
        )
        self._assert_safe_write(content, allow_sensitive=allow_sensitive)
        written = self.files.write_text(path, content, append=append)
        action = "appended" if append else "wrote"
        return ToolExecution(call=call, ok=True, output=f"{action}: {written}")

    def _replace_file(self, call: ToolCall) -> ToolExecution:
        resource = filesystem_resource(
            str(call.args["path"]),
            operation="replace",
            workspace_roots=self.config.permissions.workspace_roots,
        )
        path = Path(str(resource.fields["path"]))
        old = str(call.args["old"])
        new = str(call.args["new"])
        count = int(call.args.get("count", -1))
        allow_sensitive = bool(call.args.get("allow_sensitive", False))
        self._authorize(
            call,
            PermissionRequest(
                tool="edit",
                action="replace file",
                target=str(path),
                resource=resource,
            ),
            approval_target=resource.target,
            risk_reason="Replace existing content in a filesystem path.",
        )
        self._assert_safe_write(new, allow_sensitive=allow_sensitive)
        replacements = self.files.replace_text(path, old, new, count=count)
        return ToolExecution(call=call, ok=True, output=f"replacements: {replacements}")

    def _run_shell(self, call: ToolCall) -> ToolExecution:
        args = call.args.get("args")
        if (
            not isinstance(args, list)
            or not args
            or not all(isinstance(item, str) for item in args)
        ):
            raise ValueError("shell.run requires args as a list of strings.")
        timeout = int(call.args.get("timeout", 120))
        resource = shell_resource(args)
        self._authorize(
            call,
            PermissionRequest(
                tool="shell",
                action="run command",
                target=" ".join(args),
                resource=resource,
            ),
            approval_target=resource.target,
            risk_reason="Execute a subprocess with the displayed arguments.",
        )
        result = self.shell.run(args, timeout=timeout)
        output = _format_process_output(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return ToolExecution(
            call=call,
            ok=result.returncode == 0,
            output=output,
            metadata={
                "sandbox_profile": result.profile,
                "sandbox_os_enforced": result.os_enforced,
                "output_truncated": result.output_truncated,
            },
        )

    def _run_git(self, call: ToolCall) -> ToolExecution:
        cwd = Path(str(call.args.get("cwd", ".")))
        timeout = int(call.args.get("timeout", 120))
        action = call.name.removeprefix("git.")
        if action == "status":
            resource = git_resource(
                operation=action,
                cwd=cwd,
                workspace_roots=self.config.permissions.workspace_roots,
            )
            cwd = Path(str(resource.fields["repository"]))
            self.permissions.require_allowed(
                PermissionRequest(tool="git", action="status", target=str(cwd), resource=resource),
                approved=True,
            )
            result = self.git.status(cwd=cwd, timeout=timeout)
        elif action == "diff":
            resource = git_resource(
                operation=action,
                cwd=cwd,
                workspace_roots=self.config.permissions.workspace_roots,
            )
            cwd = Path(str(resource.fields["repository"]))
            self.permissions.require_allowed(
                PermissionRequest(tool="git", action="diff", target=str(cwd), resource=resource),
                approved=True,
            )
            result = self.git.diff(cwd=cwd, timeout=timeout)
        elif action == "show":
            revision = str(call.args.get("revision", "HEAD"))
            resource = git_resource(
                operation=action,
                cwd=cwd,
                workspace_roots=self.config.permissions.workspace_roots,
                revision=revision,
            )
            cwd = Path(str(resource.fields["repository"]))
            self.permissions.require_allowed(
                PermissionRequest(tool="git", action="show", target=revision, resource=resource),
                approved=True,
            )
            result = self.git.show(revision, cwd=cwd, timeout=timeout)
        elif action == "add":
            paths = _string_list(call.args.get("paths"), "git.add requires paths.")
            resource = git_resource(
                operation=action,
                cwd=cwd,
                workspace_roots=self.config.permissions.workspace_roots,
                paths=paths,
            )
            cwd = Path(str(resource.fields["repository"]))
            self._authorize(
                call,
                PermissionRequest(
                    tool="git",
                    action="add",
                    target=" ".join(paths),
                    resource=resource,
                ),
                approval_target=resource.target,
                risk_reason="Stage repository paths for a future commit.",
            )
            result = self.git.add(paths, cwd=cwd, timeout=timeout)
        elif action == "commit":
            message = str(call.args["message"])
            resource = git_resource(
                operation=action,
                cwd=cwd,
                workspace_roots=self.config.permissions.workspace_roots,
                message=message,
            )
            cwd = Path(str(resource.fields["repository"]))
            self._authorize(
                call,
                PermissionRequest(tool="git", action="commit", target=message, resource=resource),
                approval_target=resource.target,
                risk_reason="Create a Git commit in the target repository.",
            )
            result = self.git.commit(message, cwd=cwd, timeout=timeout)
        else:
            raise ValueError(
                "Git tool must be one of: git.status, git.diff, git.show, git.add, git.commit."
            )
        return ToolExecution(
            call=call,
            ok=result.returncode == 0,
            output=_format_process_output(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            ),
            metadata={
                "sandbox_profile": result.sandbox_profile,
                "sandbox_os_enforced": result.sandbox_os_enforced,
                "output_truncated": result.output_truncated,
            },
        )

    def _create_artifact(self, call: ToolCall) -> ToolExecution:
        kind = str(call.args.get("kind", "document"))
        prompt = str(call.args["prompt"])
        output_dir = Path(str(call.args.get("output_dir", "artifacts")))
        allow_sensitive = bool(call.args.get("allow_sensitive", False))
        decision = self.protection.evaluate(prompt, "artifact", allow_sensitive=allow_sensitive)
        if decision.blocked:
            detail = _data_protection_detail(decision)
            return ToolExecution(
                call=call,
                ok=False,
                output=(
                    f"Sensitive content detected ({detail}). "
                    "Set allow_sensitive only if policy allows persistence."
                ),
            )
        if kind == "brief":
            brief_type = str(call.args.get("brief_type", "meeting"))
            result = create_brief_artifact(prompt, output_dir, brief_type=brief_type)
        else:
            factory = _artifact_factory(kind)
            result = factory(prompt, output_dir)
        provenance_path = write_provenance(result=result, prompt_preview=prompt_preview(prompt))
        return ToolExecution(
            call=call,
            ok=True,
            output=_format_artifact_output(result, provenance_path),
        )

    def _search_memory(self, call: ToolCall) -> ToolExecution:
        query = str(call.args["query"])
        limit = int(call.args.get("limit", 10))
        if not self.config.memory.local.enabled:
            return ToolExecution(call=call, ok=False, output="Local memory is disabled.")
        store = LocalMemoryStore.from_config(self.config.memory.local, self.config.safety)
        memories = store.search(query)[:limit]
        output = "\n".join(f"{memory.memory_id}: {memory.content}" for memory in memories)
        return ToolExecution(call=call, ok=True, output=output or "No matching local memories.")

    def _search_shared_memory(self, call: ToolCall) -> ToolExecution:
        query = str(call.args["query"])
        tenant_id = str(call.args.get("tenant_id", self.identity.tenant))
        limit = int(call.args.get("limit", 10))
        execute = bool(call.args.get("execute", True))
        if not self.config.memory.shared.enabled:
            return ToolExecution(call=call, ok=False, output="Shared memory is disabled.")
        if (
            self.config.memory.shared.tenant_isolation == "identity"
            and tenant_id != self.identity.tenant
        ):
            raise PermissionError(f"Cross-tenant shared-memory access denied: {tenant_id}")
        resource = memory_resource(
            operation="search",
            tenant=tenant_id,
            backend=self.config.memory.shared.backend,
        )
        self.permissions.require_allowed(
            PermissionRequest(
                tool="shared_memory",
                action="search",
                target=tenant_id,
                resource=resource,
            ),
            approved=True,
        )
        result = search_shared_memories(
            self.config,
            query=query,
            tenant_id=tenant_id,
            limit=limit,
            execute=execute,
        )
        if result.records:
            output = "\n".join(
                f"{record.citation}: {record.summary} | {record.content}"
                for record in result.records
            )
            return ToolExecution(call=call, ok=True, output=output)
        if result.statement:
            output = "\n".join(
                [
                    *result.messages,
                    "sql:",
                    result.statement.sql,
                    f"params: {result.statement.params}",
                ]
            )
            return ToolExecution(call=call, ok=False, output=output)
        return ToolExecution(call=call, ok=True, output="No matching shared memories.")

    def _run_polaris_readonly(self, call: ToolCall) -> ToolExecution:
        if not self.config.polaris.enabled:
            return ToolExecution(call=call, ok=False, output="Polaris is disabled.")
        args = call.args.get("args")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError("polaris.readonly requires args as a list of strings.")
        resource = polaris_resource(args, default_catalog=self.config.polaris.catalog)
        self._authorize(
            call,
            PermissionRequest(
                tool="governed_data",
                action="read governed metadata",
                target=" ".join(args),
                resource=resource,
            ),
            approval_target=resource.target,
            risk_reason="Read metadata from the governed enterprise catalog.",
        )
        result = PolarisClient(
            self.config.polaris,
            self.config.sandbox,
            workspace_roots=self.config.permissions.workspace_roots,
        ).run_readonly(args)
        output = _format_process_output(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return ToolExecution(
            call=call,
            ok=result.returncode == 0,
            output=output,
            metadata={
                "sandbox_profile": result.sandbox_profile,
                "sandbox_os_enforced": result.sandbox_os_enforced,
                "output_truncated": result.output_truncated,
            },
        )

    def _run_mcp(self, call: ToolCall) -> ToolExecution:
        if not self.config.mcp.enabled:
            return ToolExecution(call=call, ok=False, output="MCP is disabled.")
        server_id = str(call.args["server_id"])
        server = MCPRegistry(self.config.mcp).get(server_id)
        endpoint = server_endpoint_for_display(server)
        operation = call.name.removeprefix("mcp.")
        name = ""
        arguments: dict[str, Any] = {}
        if operation == "call":
            name = str(call.args["tool_name"])
            raw_arguments = call.args.get("arguments", {})
            if not isinstance(raw_arguments, dict):
                raise ValueError("mcp.call requires arguments as an object.")
            arguments = raw_arguments
            action = "call tool"
        elif operation == "tools":
            action = "list tools"
        elif operation == "resources":
            action = "list resources"
        elif operation == "read":
            name = str(call.args["uri"])
            action = "read resource"
        elif operation == "prompts":
            action = "list prompts"
        elif operation == "prompt":
            name = str(call.args["prompt_name"])
            raw_arguments = call.args.get("arguments", {})
            if not isinstance(raw_arguments, dict) or not all(
                isinstance(value, str) for value in raw_arguments.values()
            ):
                raise ValueError("mcp.prompt requires string-valued arguments.")
            arguments = raw_arguments
            action = "get prompt"
        elif operation == "task_start":
            name = str(call.args["tool_name"])
            raw_arguments = call.args.get("arguments", {})
            if not isinstance(raw_arguments, dict):
                raise ValueError("mcp.task_start requires arguments as an object.")
            arguments = raw_arguments
            action = "start task"
        elif operation == "task_get":
            name = str(call.args["task_id"])
            action = "get task"
        elif operation == "task_update":
            name = str(call.args["task_id"])
            raw_arguments = call.args.get("responses", {})
            if not isinstance(raw_arguments, dict):
                raise ValueError("mcp.task_update requires responses as an object.")
            arguments = raw_arguments
            action = "update task"
        elif operation == "task_cancel":
            name = str(call.args["task_id"])
            action = "cancel task"
        else:
            raise ValueError(
                "MCP tool must be one of: mcp.tools, mcp.call, mcp.resources, "
                "mcp.read, mcp.prompts, mcp.prompt, mcp.task_start, "
                "mcp.task_get, mcp.task_update, mcp.task_cancel."
            )
        resource = mcp_resource(
            operation=operation,
            server_id=server_id,
            transport=server.transport,
            endpoint=endpoint,
            name=name,
            arguments=arguments,
        )
        self._authorize(
            call,
            PermissionRequest(
                tool="mcp",
                action=action,
                target=resource.target,
                resource=resource,
            ),
            approval_target=resource.target,
            risk_reason="Connect to an MCP server and perform the displayed remote operation.",
        )
        result = asyncio.run(
            self._execute_mcp_request(
                operation=operation,
                server_id=server_id,
                name=name,
                arguments=arguments,
            )
        )
        connection = result.get("connection", {})
        return ToolExecution(
            call=call,
            ok=True,
            output=json.dumps(result, sort_keys=True, ensure_ascii=True),
            metadata={
                "mcp_server_id": server_id,
                "mcp_transport": connection.get("transport"),
                "mcp_protocol_version": connection.get("protocol_version"),
                "mcp_lifecycle": connection.get("lifecycle"),
                "sandbox_profile": connection.get("sandbox_profile"),
                "sandbox_os_enforced": connection.get("sandbox_os_enforced"),
            },
        )

    def _run_skill(self, call: ToolCall) -> ToolExecution:
        name = str(call.args["name"])
        path = str(call.args["path"])
        if call.name == "skill.read":
            self.permissions.require_allowed(
                PermissionRequest(tool="skills", action="read", target=f"{name}/{path}"),
                approved=True,
            )
            return ToolExecution(
                call=call,
                ok=True,
                output=self.skills.read_supporting_file(name, path),
                metadata={"skill_name": name, "skill_digest": self.skills.get(name).digest},
            )
        if call.name != "skill.run_script":
            raise ValueError("Skill tool must be skill.read or skill.run_script.")
        if not self.config.skills.allow_scripts:
            raise PermissionError("Skill script execution is disabled by managed configuration.")
        metadata = self.skills.get(name, require_enabled=True)
        if metadata.allowed_tools and not {
            "shell.run",
            "skill.run_script",
        }.intersection(metadata.allowed_tools):
            raise PermissionError("Skill allowed-tools metadata does not permit script execution.")
        script = self.skills.supporting_path(name, path)
        if script.parent.name != "scripts":
            raise ValueError("Only files directly under scripts/ may execute.")
        raw_args = call.args.get("args", [])
        if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
            raise ValueError("skill.run_script args must be a list of strings.")
        command = (
            [sys.executable, str(script), *raw_args]
            if script.suffix == ".py"
            else ["/bin/sh", str(script), *raw_args]
        )
        resource = shell_resource(command, operation="run skill script")
        self._authorize(
            call,
            PermissionRequest(
                tool="shell",
                action="run skill script",
                target=resource.target,
                resource=resource,
            ),
            approval_target=resource.target,
            risk_reason="Execute a reviewed skill script without granting the skill authority.",
        )
        result = self.shell.run(
            command,
            timeout=int(call.args.get("timeout", 120)),
            profile=self.config.sandbox.skill_profile,
            cwd=Path.cwd(),
        )
        return ToolExecution(
            call=call,
            ok=result.returncode == 0,
            output=_format_process_output(
                returncode=result.returncode, stdout=result.stdout, stderr=result.stderr
            ),
            metadata={
                "skill_name": name,
                "skill_digest": metadata.digest,
                "sandbox_profile": result.profile,
                "sandbox_os_enforced": result.os_enforced,
                "output_truncated": result.output_truncated,
            },
        )

    def _run_session_message(self, call: ToolCall) -> ToolExecution:
        if self.active_session_id is None:
            raise ValueError("Session messaging requires an active Loro session.")
        if call.name == "session.inbox":
            messages = self.mailbox.list(self.active_session_id)
            return ToolExecution(
                call=call,
                ok=True,
                output=json.dumps([message.to_payload() for message in messages], sort_keys=True),
            )
        if call.name != "session.send":
            raise ValueError("Session tool must be session.send or session.inbox.")
        recipient = str(call.args["recipient_session_id"])
        content = str(call.args["content"])
        self._assert_safe_write(
            content, allow_sensitive=bool(call.args.get("allow_sensitive", False))
        )
        resource = session_message_resource(
            operation="send",
            sender_session_id=self.active_session_id,
            recipient_session_id=recipient,
            message_digest=message_digest(content),
        )
        self._authorize(
            call,
            PermissionRequest(
                tool="session_message",
                action="send",
                target=resource.target,
                resource=resource,
            ),
            approval_target=resource.target,
            risk_reason="Send untrusted coordination context to another Loro session.",
        )
        message = self.mailbox.send(
            sender_session_id=self.active_session_id,
            recipient_session_id=recipient,
            content=content,
            validate_sender=False,
            allow_sensitive=bool(call.args.get("allow_sensitive", False)),
        )
        return ToolExecution(
            call=call,
            ok=True,
            output=json.dumps(message.to_payload(), sort_keys=True),
            metadata={
                "message_id": message.message_id,
                "recipient_session_id": recipient,
                "carries_user_authority": False,
            },
        )

    async def _execute_mcp_request(
        self,
        *,
        operation: str,
        server_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == "call":
            return await self.mcp.call_tool(server_id, name, arguments)
        if operation == "tools":
            return await self.mcp.list_tools(server_id)
        if operation == "resources":
            return await self.mcp.list_resources(server_id)
        if operation == "read":
            return await self.mcp.read_resource(server_id, name)
        if operation == "prompts":
            return await self.mcp.list_prompts(server_id)
        if operation == "prompt":
            return await self.mcp.get_prompt(server_id, name, arguments)
        if operation == "task_start":
            return await self.mcp.start_task(server_id, name, arguments)
        if operation == "task_get":
            return await self.mcp.get_task(server_id, name)
        if operation == "task_update":
            return await self.mcp.update_task(server_id, name, arguments, user_approved=True)
        return await self.mcp.cancel_task(server_id, name, user_approved=True)

    def _assert_safe_write(self, content: str, *, allow_sensitive: bool) -> None:
        decision = self.protection.evaluate(content, "artifact", allow_sensitive=allow_sensitive)
        if decision.blocked:
            detail = _data_protection_detail(decision)
            raise ValueError(
                f"Sensitive content detected ({detail}). "
                "Set allow_sensitive only if policy allows persistence."
            )

    def _authorize(
        self,
        call: ToolCall,
        permission_request: PermissionRequest,
        *,
        approval_target: str,
        risk_reason: str,
    ) -> None:
        result = self.permissions.evaluate(permission_request)
        if result.decision == "deny":
            raise PermissionError(
                f"{permission_request.tool} is denied by policy: {permission_request.action}"
            )
        if result.decision == "allow":
            return
        request = self.approvals.request(
            action=f"{permission_request.tool}.{permission_request.action}",
            target=approval_target,
            arguments=call.args,
            policy_decision=result.decision,
            policy_version=result.policy_version,
            policy_source=result.policy_source,
            policy_reason=result.reason,
            risk_reason=risk_reason,
        )
        if self.approvals.consume_matching_session(request) is not None:
            return
        if call.origin == "user" and bool(call.args.get("approved", False)):
            try:
                record = self.approvals.grant(request, scope="once", method="non_interactive")
                self.approvals.consume(request, record.approval_id)
            except PermissionError:
                self.approvals.deny(request)
                raise
            return
        if self.approval_provider is not None and self.config.approvals.interactive:
            try:
                scope = self.approval_provider(request)
            except Exception:
                self.approvals.deny(request)
                raise
            if scope is not None:
                try:
                    record = self.approvals.grant(request, scope=scope, method="interactive")
                    self.approvals.consume(request, record.approval_id)
                except PermissionError:
                    self.approvals.deny(request)
                    raise
                return
            self.approvals.deny(request)
            raise PermissionError(f"Approval denied for {request.action}.")
        if call.origin == "model" and bool(call.args.get("approved", False)):
            self.approvals.deny(request)
            raise PermissionError("Model-provided approval arguments are not trusted.")
        self.approvals.deny(request)
        raise PermissionError(f"{permission_request.tool} requires approval from a trusted user.")


def parse_tool_calls(
    prompt: str,
    *,
    origin: Literal["user", "model"] = "user",
) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@tool "):
            continue
        directive = stripped.removeprefix("@tool ").strip()
        if directive.startswith("{"):
            calls.append(_parse_json_tool_call(directive, origin=origin))
            continue
        name, _, raw_args = directive.partition(" ")
        if not name or not raw_args.strip():
            calls.append(ToolCall(name=name or "unknown", args={}, origin=origin))
            continue
        data = json.loads(raw_args)
        if not isinstance(data, dict):
            raise ValueError("Tool call arguments must be a JSON object.")
        calls.append(ToolCall(name=name, args=data, origin=origin))
    return calls


def _parse_json_tool_call(
    raw: str,
    *,
    origin: Literal["user", "model"],
) -> ToolCall:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Tool directive must be a JSON object.")
    name = data.get("name")
    args = data.get("args", {})
    if not isinstance(name, str) or not name:
        raise ValueError("Tool directive requires a non-empty string name.")
    if not isinstance(args, dict):
        raise ValueError("Tool directive args must be a JSON object.")
    return ToolCall(name=name, args=args, origin=origin)


def _string_list(value: Any, message: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(message)
    return value


def _format_process_output(*, returncode: int, stdout: str, stderr: str) -> str:
    sections = [f"returncode: {returncode}"]
    if stdout:
        sections.append(f"stdout:\n{stdout.rstrip()}")
    if stderr:
        sections.append(f"stderr:\n{stderr.rstrip()}")
    return "\n".join(sections)


def _artifact_factory(kind: str) -> Callable[[str, Path], ArtifactResult]:
    factories: dict[str, Callable[[str, Path], ArtifactResult]] = {
        "document": create_document_artifact,
        "presentation": create_presentation_artifact,
        "spreadsheet": create_spreadsheet_artifact,
    }
    try:
        return factories[kind]
    except KeyError as error:
        raise ValueError(
            "artifact.create kind must be one of: document, presentation, spreadsheet, brief."
        ) from error


def _data_protection_detail(decision: DataProtectionDecision) -> str:
    kinds = sorted({finding.kind for finding in decision.findings})
    return ", ".join(kinds) if kinds else decision.classification


def _format_artifact_output(result: ArtifactResult, provenance_path: Path) -> str:
    return "\n".join(
        [
            result.summary,
            "paths:",
            *[f"- {path}" for path in result.paths],
            f"provenance: {provenance_path}",
        ]
    )
