import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loro.artifacts.briefs import create_brief_artifact
from loro.artifacts.common import ArtifactResult, write_provenance
from loro.artifacts.documents import create_document_artifact
from loro.artifacts.presentations import create_presentation_artifact
from loro.artifacts.spreadsheets import create_spreadsheet_artifact
from loro.audit import prompt_preview
from loro.config import LoroConfig
from loro.identity import IdentityContext, resolve_identity
from loro.memory.local import LocalMemoryStore
from loro.memory.operations import search_shared_memories
from loro.permissions import PermissionEngine, PermissionRequest
from loro.polaris import PolarisClient
from loro.safety import SafetyScanner
from loro.tools.files import FileTools
from loro.tools.git import GitTools
from loro.tools.shell import ShellTools


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolExecution:
    call: ToolCall
    ok: bool
    output: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "tool": self.call.name,
            "args": self.call.args,
            "ok": self.ok,
            "output": self.output,
        }


class ToolRegistry:
    """Executes explicit, typed tool calls for the runtime loop."""

    def __init__(
        self,
        config: LoroConfig,
        identity: IdentityContext | None = None,
    ) -> None:
        self.config = config
        self.identity = identity or resolve_identity(config.identity)
        self.permissions = PermissionEngine(config.permissions)
        self.files = FileTools()
        self.git = GitTools()
        self.shell = ShellTools()
        self.safety = SafetyScanner(config.safety)

    def execute(self, call: ToolCall) -> ToolExecution:
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
            return ToolExecution(call=call, ok=False, output=f"Unknown tool: {call.name}")
        except Exception as error:
            return ToolExecution(call=call, ok=False, output=str(error))

    def _read_file(self, call: ToolCall) -> ToolExecution:
        path = Path(str(call.args["path"]))
        limit = int(call.args.get("limit", 20000))
        self.permissions.require_allowed(
            PermissionRequest(tool="edit", action="read file", target=str(path)),
            approved=True,
        )
        return ToolExecution(
            call=call,
            ok=True,
            output=self.files.read_text(path, limit=limit),
        )

    def _search_files(self, call: ToolCall) -> ToolExecution:
        query = str(call.args["query"])
        root = Path(str(call.args.get("root", ".")))
        limit = int(call.args.get("limit", 50))
        self.permissions.require_allowed(
            PermissionRequest(tool="edit", action="search files", target=str(root)),
            approved=True,
        )
        matches = self.files.search(root=root, query=query, limit=limit)
        output = "\n".join(
            f"{match.path}:{match.line_number}: {match.line}" for match in matches
        )
        return ToolExecution(call=call, ok=True, output=output or "No matches.")

    def _write_file(self, call: ToolCall) -> ToolExecution:
        path = Path(str(call.args["path"]))
        content = str(call.args["content"])
        append = bool(call.args.get("append", False))
        allow_sensitive = bool(call.args.get("allow_sensitive", False))
        approved = bool(call.args.get("approved", False))
        self.permissions.require_allowed(
            PermissionRequest(tool="edit", action="write file", target=str(path)),
            approved=approved,
        )
        self._assert_safe_write(content, allow_sensitive=allow_sensitive)
        written = self.files.write_text(path, content, append=append)
        action = "appended" if append else "wrote"
        return ToolExecution(call=call, ok=True, output=f"{action}: {written}")

    def _replace_file(self, call: ToolCall) -> ToolExecution:
        path = Path(str(call.args["path"]))
        old = str(call.args["old"])
        new = str(call.args["new"])
        count = int(call.args.get("count", -1))
        allow_sensitive = bool(call.args.get("allow_sensitive", False))
        approved = bool(call.args.get("approved", False))
        self.permissions.require_allowed(
            PermissionRequest(tool="edit", action="replace file", target=str(path)),
            approved=approved,
        )
        self._assert_safe_write(new, allow_sensitive=allow_sensitive)
        replacements = self.files.replace_text(path, old, new, count=count)
        return ToolExecution(call=call, ok=True, output=f"replacements: {replacements}")

    def _run_shell(self, call: ToolCall) -> ToolExecution:
        args = call.args.get("args")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError("shell.run requires args as a list of strings.")
        timeout = int(call.args.get("timeout", 120))
        approved = bool(call.args.get("approved", False))
        self.permissions.require_allowed(
            PermissionRequest(tool="shell", action="run command", target=" ".join(args)),
            approved=approved,
        )
        result = self.shell.run(args, timeout=timeout)
        output = _format_process_output(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return ToolExecution(call=call, ok=result.returncode == 0, output=output)

    def _run_git(self, call: ToolCall) -> ToolExecution:
        cwd = Path(str(call.args.get("cwd", ".")))
        timeout = int(call.args.get("timeout", 120))
        approved = bool(call.args.get("approved", False))
        action = call.name.removeprefix("git.")
        if action == "status":
            self.permissions.require_allowed(
                PermissionRequest(tool="git", action="status", target=str(cwd)),
                approved=True,
            )
            result = self.git.status(cwd=cwd, timeout=timeout)
        elif action == "diff":
            self.permissions.require_allowed(
                PermissionRequest(tool="git", action="diff", target=str(cwd)),
                approved=True,
            )
            result = self.git.diff(cwd=cwd, timeout=timeout)
        elif action == "show":
            revision = str(call.args.get("revision", "HEAD"))
            self.permissions.require_allowed(
                PermissionRequest(tool="git", action="show", target=revision),
                approved=True,
            )
            result = self.git.show(revision, cwd=cwd, timeout=timeout)
        elif action == "add":
            paths = _string_list(call.args.get("paths"), "git.add requires paths.")
            self.permissions.require_allowed(
                PermissionRequest(tool="git", action="add", target=" ".join(paths)),
                approved=approved,
            )
            result = self.git.add(paths, cwd=cwd, timeout=timeout)
        elif action == "commit":
            message = str(call.args["message"])
            self.permissions.require_allowed(
                PermissionRequest(tool="git", action="commit", target=message),
                approved=approved,
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
        )

    def _create_artifact(self, call: ToolCall) -> ToolExecution:
        kind = str(call.args.get("kind", "document"))
        prompt = str(call.args["prompt"])
        output_dir = Path(str(call.args.get("output_dir", "artifacts")))
        allow_sensitive = bool(call.args.get("allow_sensitive", False))
        findings = self.safety.scan(prompt)
        if findings and self.config.safety.block_on_findings and not allow_sensitive:
            kinds = ", ".join(sorted({finding.kind for finding in findings}))
            return ToolExecution(
                call=call,
                ok=False,
                output=(
                    f"Sensitive content detected ({kinds}). "
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
        store = LocalMemoryStore.from_config(self.config.memory.local)
        memories = store.search(query)[:limit]
        output = "\n".join(
            f"{memory.memory_id}: {memory.content}" for memory in memories
        )
        return ToolExecution(call=call, ok=True, output=output or "No matching local memories.")

    def _search_shared_memory(self, call: ToolCall) -> ToolExecution:
        query = str(call.args["query"])
        tenant_id = str(call.args.get("tenant_id", self.identity.tenant))
        limit = int(call.args.get("limit", 10))
        execute = bool(call.args.get("execute", True))
        if not self.config.memory.shared.enabled:
            return ToolExecution(call=call, ok=False, output="Shared memory is disabled.")
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
        result = PolarisClient(self.config.polaris).run_readonly(args)
        output = _format_process_output(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return ToolExecution(call=call, ok=result.returncode == 0, output=output)

    def _assert_safe_write(self, content: str, *, allow_sensitive: bool) -> None:
        findings = self.safety.scan(content)
        if findings and self.config.safety.block_on_findings and not allow_sensitive:
            kinds = ", ".join(sorted({finding.kind for finding in findings}))
            raise ValueError(
                f"Sensitive content detected ({kinds}). "
                "Set allow_sensitive only if policy allows persistence."
            )


def parse_tool_calls(prompt: str) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@tool "):
            continue
        directive = stripped.removeprefix("@tool ").strip()
        if directive.startswith("{"):
            calls.append(_parse_json_tool_call(directive))
            continue
        name, _, raw_args = directive.partition(" ")
        if not name or not raw_args.strip():
            calls.append(ToolCall(name=name or "unknown", args={}))
            continue
        data = json.loads(raw_args)
        if not isinstance(data, dict):
            raise ValueError("Tool call arguments must be a JSON object.")
        calls.append(ToolCall(name=name, args=data))
    return calls


def _parse_json_tool_call(raw: str) -> ToolCall:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Tool directive must be a JSON object.")
    name = data.get("name")
    args = data.get("args", {})
    if not isinstance(name, str) or not name:
        raise ValueError("Tool directive requires a non-empty string name.")
    if not isinstance(args, dict):
        raise ValueError("Tool directive args must be a JSON object.")
    return ToolCall(name=name, args=args)


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


def _format_artifact_output(result: ArtifactResult, provenance_path: Path) -> str:
    return "\n".join(
        [
            result.summary,
            "paths:",
            *[f"- {path}" for path in result.paths],
            f"provenance: {provenance_path}",
        ]
    )
