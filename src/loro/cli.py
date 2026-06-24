from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from loro import __version__
from loro.artifacts.briefs import create_brief_artifact
from loro.artifacts.common import ArtifactResult, write_provenance
from loro.artifacts.documents import create_document_artifact
from loro.artifacts.presentations import create_presentation_artifact
from loro.artifacts.spreadsheets import create_spreadsheet_artifact
from loro.audit import AuditLogger, prompt_preview
from loro.config import load_config
from loro.memory.base import SharedMemoryDraft
from loro.memory.local import LocalMemoryStore
from loro.memory.shared import SharedMemoryDraftStore, shared_memory_schema
from loro.permissions import PermissionEngine, PermissionRequest
from loro.polaris import PolarisClient
from loro.runtime import AgentRuntime
from loro.safety import SafetyScanner
from loro.sessions import SessionStore
from loro.tools.files import FileTools
from loro.tools.shell import ShellTools

app = typer.Typer(
    name="loro",
    help="Enterprise agent harness for coding, governed data, and productivity work.",
    no_args_is_help=True,
    invoke_without_command=True,
)
memory_app = typer.Typer(help="Inspect and write Loro memories.")
docs_app = typer.Typer(help="Create and transform documents.")
slides_app = typer.Typer(help="Create and transform presentations.")
sheets_app = typer.Typer(help="Create and transform spreadsheets.")
brief_app = typer.Typer(help="Create enterprise briefs.")
data_app = typer.Typer(help="Discover governed enterprise data.")
sessions_app = typer.Typer(help="Inspect saved Loro sessions.")
file_app = typer.Typer(help="Read and search local files.")
shell_app = typer.Typer(help="Run permission-gated shell commands.")
safety_app = typer.Typer(help="Scan content for obvious secrets.")

app.add_typer(memory_app, name="memory")
app.add_typer(docs_app, name="docs")
app.add_typer(slides_app, name="slides")
app.add_typer(sheets_app, name="sheets")
app.add_typer(brief_app, name="brief")
app.add_typer(data_app, name="data")
app.add_typer(sessions_app, name="sessions")
app.add_typer(file_app, name="file")
app.add_typer(shell_app, name="shell")
app.add_typer(safety_app, name="safety")

console = Console()
DEFAULT_ARTIFACT_DIR = Path("artifacts")


def _runtime() -> AgentRuntime:
    return AgentRuntime(load_config())


def _audit() -> AuditLogger:
    return AuditLogger(load_config().audit)


def _permissions() -> PermissionEngine:
    return PermissionEngine(load_config().permissions)


def _safety() -> SafetyScanner:
    return SafetyScanner(load_config().safety)


def _enforce_safe_content(content: str, context: str, allow_sensitive: bool = False) -> None:
    findings = _safety().scan(content)
    if not findings:
        return
    _audit().write(
        "safety.findings_detected",
        context=context,
        finding_kinds=sorted({finding.kind for finding in findings}),
    )
    if load_config().safety.block_on_findings and not allow_sensitive:
        kinds = ", ".join(sorted({finding.kind for finding in findings}))
        raise typer.BadParameter(
            f"Sensitive content detected ({kinds}). Re-run with --allow-sensitive "
            "only if policy allows storing this content."
        )


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


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", help="Show Loro version.")] = False,
) -> None:
    if version:
        console.print(f"loro {__version__}")
        raise typer.Exit()


@app.command()
def run(prompt: Annotated[str, typer.Argument(help="Task prompt for Loro.")]) -> None:
    """Run a one-shot agent task."""
    result = _runtime().run(prompt, mode="run")
    console.print(result.summary)


@app.command()
def plan(prompt: Annotated[str, typer.Argument(help="Planning prompt for Loro.")]) -> None:
    """Run a read-only planning task."""
    result = _runtime().run(prompt, mode="plan")
    console.print(result.summary)


@app.command("config")
def show_config() -> None:
    """Show resolved configuration."""
    console.print_json(load_config().model_dump_json(indent=2))


@app.command()
def doctor() -> None:
    """Validate provider, permission, memory, Polaris, and artifact configuration."""
    config = load_config()
    console.print("[bold green]Loro doctor[/bold green]")
    console.print(f"Model provider: {config.model.provider}")
    console.print(f"Default permission: {config.permissions.default}")
    console.print(f"Local memory: {'enabled' if config.memory.local.enabled else 'disabled'}")
    console.print(f"Shared memory: {'enabled' if config.memory.shared.enabled else 'disabled'}")
    console.print(f"Polaris: {'enabled' if config.polaris.enabled else 'disabled'}")
    console.print(f"Audit log: {'enabled' if config.audit.enabled else 'disabled'}")
    console.print(f"Session path: {config.sessions.path}")
    console.print(f"Safety scanner: {'enabled' if config.safety.enabled else 'disabled'}")


@memory_app.command("list")
def memory_list() -> None:
    """List local memories."""
    store = LocalMemoryStore.from_config(load_config().memory.local)
    memories = store.list()
    if not memories:
        console.print("No local memories yet.")
        return
    for memory in memories:
        console.print(
            f"- [bold]{memory.memory_id}[/bold] "
            f"({memory.created_at.date().isoformat()}): {memory.content}"
        )


@memory_app.command("search")
def memory_search(query: Annotated[str, typer.Argument(help="Search query.")]) -> None:
    """Search local memories."""
    store = LocalMemoryStore.from_config(load_config().memory.local)
    memories = store.search(query)
    if not memories:
        console.print("No matching local memories.")
        return
    for memory in memories:
        console.print(f"- [bold]{memory.memory_id}[/bold]: {memory.content}")


@memory_app.command("remember")
def remember_local(
    content: Annotated[str, typer.Argument(help="Memory content.")],
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    """Explicitly write a local memory."""
    _enforce_safe_content(content, context="memory.local", allow_sensitive=allow_sensitive)
    store = LocalMemoryStore.from_config(load_config().memory.local)
    memory = store.remember(content)
    _audit().write(
        "memory.local_written",
        memory_id=memory.memory_id,
        scope=memory.scope,
        content_preview=prompt_preview(content),
    )
    console.print(f"Saved local memory: {memory.memory_id}")


@memory_app.command("drafts")
def memory_drafts() -> None:
    """List staged shared memory drafts."""
    store = SharedMemoryDraftStore(Path(load_config().memory.local.path))
    drafts = store.list()
    if not drafts:
        console.print("No shared memory drafts yet.")
        return
    for draft in drafts:
        console.print(
            f"- [bold]{draft.draft_id}[/bold] "
            f"({draft.tenant_id}/{draft.scope_type}/{draft.scope_key}): {draft.summary}"
        )


@memory_app.command("schema")
def memory_schema(
    backend: Annotated[
        str,
        typer.Option("--backend", help="Shared memory backend: postgres or iceberg."),
    ] = "postgres",
) -> None:
    """Print shared memory backend schema SQL."""
    try:
        console.print(shared_memory_schema(backend))  # type: ignore[arg-type]
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@app.command("remember")
def remember(
    content: Annotated[str, typer.Argument(help="Memory content.")],
    local: Annotated[bool, typer.Option("--local", help="Write local memory.")] = False,
    shared: Annotated[
        bool, typer.Option("--shared", help="Write shared enterprise memory.")
    ] = False,
    tenant_id: Annotated[
        str, typer.Option("--tenant-id", help="Shared memory tenant.")
    ] = "default",
    scope_type: Annotated[
        str, typer.Option("--scope-type", help="Shared memory scope type.")
    ] = "org",
    scope_key: Annotated[
        str, typer.Option("--scope-key", help="Shared memory scope key.")
    ] = "default",
    memory_type: Annotated[str, typer.Option("--memory-type", help="Shared memory type.")] = "fact",
    classification: Annotated[
        str, typer.Option("--classification", help="Shared memory classification.")
    ] = "public-internal",
    created_by: Annotated[
        str, typer.Option("--created-by", help="Shared memory author.")
    ] = "local-user",
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    """Explicitly write a local or shared memory."""
    _enforce_safe_content(
        content,
        context="memory.shared" if shared else "memory.local",
        allow_sensitive=allow_sensitive,
    )
    if shared:
        draft = SharedMemoryDraft(
            content=content,
            summary=prompt_preview(content, limit=120),
            tenant_id=tenant_id,
            scope_type=scope_type,
            scope_key=scope_key,
            memory_type=memory_type,
            classification=classification,
            created_by=created_by,
        )
        SharedMemoryDraftStore(Path(load_config().memory.local.path)).stage(draft)
        _audit().write(
            "memory.shared_draft_staged",
            draft_id=draft.draft_id,
            tenant_id=draft.tenant_id,
            scope_type=draft.scope_type,
            scope_key=draft.scope_key,
            prompt_preview=prompt_preview(content),
        )
        console.print(
            f"Staged shared memory draft: {draft.draft_id}\n"
            "Live shared backend commits are not enabled yet."
        )
        return
    if local or not shared:
        store = LocalMemoryStore.from_config(load_config().memory.local)
        memory = store.remember(content)
        _audit().write(
            "memory.local_written",
            memory_id=memory.memory_id,
            scope=memory.scope,
            content_preview=prompt_preview(content),
        )
        console.print(f"Saved local memory: {memory.memory_id}")


@docs_app.command("create")
def docs_create(
    prompt: Annotated[str, typer.Argument(help="Document prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _enforce_safe_content(prompt, context="artifact.document", allow_sensitive=allow_sensitive)
    result = create_document_artifact(prompt, output_dir)
    _print_artifact_result(result, prompt)


@slides_app.command("create")
def slides_create(
    prompt: Annotated[str, typer.Argument(help="Presentation prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _enforce_safe_content(prompt, context="artifact.presentation", allow_sensitive=allow_sensitive)
    result = create_presentation_artifact(prompt, output_dir)
    _print_artifact_result(result, prompt)


@sheets_app.command("analyze")
def sheets_analyze(
    prompt: Annotated[str, typer.Argument(help="Spreadsheet prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _enforce_safe_content(prompt, context="artifact.spreadsheet", allow_sensitive=allow_sensitive)
    result = create_spreadsheet_artifact(prompt, output_dir)
    _print_artifact_result(result, prompt)


@sheets_app.command("create")
def sheets_create(
    prompt: Annotated[str, typer.Argument(help="Spreadsheet prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _enforce_safe_content(prompt, context="artifact.spreadsheet", allow_sensitive=allow_sensitive)
    result = create_spreadsheet_artifact(prompt, output_dir)
    _print_artifact_result(result, prompt)


@brief_app.command("meeting")
def brief_meeting(
    prompt: Annotated[str, typer.Argument(help="Meeting brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _enforce_safe_content(prompt, context="artifact.brief", allow_sensitive=allow_sensitive)
    result = create_brief_artifact(prompt, output_dir, brief_type="meeting")
    _print_artifact_result(result, prompt)


@brief_app.command("project")
def brief_project(
    prompt: Annotated[str, typer.Argument(help="Project brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _enforce_safe_content(prompt, context="artifact.brief", allow_sensitive=allow_sensitive)
    result = create_brief_artifact(prompt, output_dir, brief_type="project")
    _print_artifact_result(result, prompt)


@brief_app.command("incident")
def brief_incident(
    prompt: Annotated[str, typer.Argument(help="Incident brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _enforce_safe_content(prompt, context="artifact.brief", allow_sensitive=allow_sensitive)
    result = create_brief_artifact(prompt, output_dir, brief_type="incident")
    _print_artifact_result(result, prompt)


@brief_app.command("executive")
def brief_executive(
    prompt: Annotated[str, typer.Argument(help="Executive brief prompt.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for generated artifacts.")
    ] = DEFAULT_ARTIFACT_DIR,
    allow_sensitive: Annotated[
        bool,
        typer.Option("--allow-sensitive", help="Allow sensitive content if policy permits."),
    ] = False,
) -> None:
    _enforce_safe_content(prompt, context="artifact.brief", allow_sensitive=allow_sensitive)
    result = create_brief_artifact(prompt, output_dir, brief_type="executive")
    _print_artifact_result(result, prompt)


@data_app.command("catalogs")
def data_catalogs() -> None:
    config = load_config()
    if not config.polaris.enabled:
        console.print(
            "Polaris catalog discovery is disabled. Enable [polaris] to connect.",
            markup=False,
        )
        return
    result = PolarisClient(config.polaris).run_readonly(["catalogs", "list"])
    _audit().write(
        "polaris.readonly_executed",
        command=result.command,
        returncode=result.returncode,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    raise typer.Exit(code=result.returncode)


@data_app.command("polaris")
def data_polaris(
    args: Annotated[list[str], typer.Argument(help="Read-only Polaris CLI arguments.")],
) -> None:
    """Run a read-only Polaris CLI operation through Loro's wrapper."""
    config = load_config()
    if not config.polaris.enabled:
        console.print(
            "Polaris is disabled. Enable [polaris] before using this command.",
            markup=False,
        )
        raise typer.Exit(code=2)
    result = PolarisClient(config.polaris).run_readonly(args)
    _audit().write(
        "polaris.readonly_executed",
        command=result.command,
        returncode=result.returncode,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    raise typer.Exit(code=result.returncode)


@file_app.command("read")
def file_read(
    path: Annotated[Path, typer.Argument(help="File path to read.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum characters to print.")] = 20000,
) -> None:
    """Read a text file."""
    _permissions().require_allowed(
        PermissionRequest(tool="edit", action="read file", target=str(path)),
        approved=True,
    )
    text = FileTools().read_text(path, limit=limit)
    _audit().write("file.read", path=str(path), limit=limit)
    console.print(text)


@file_app.command("search")
def file_search(
    query: Annotated[str, typer.Argument(help="Text to search for.")],
    root: Annotated[Path, typer.Option("--root", "-r", help="Directory to search.")] = Path("."),
    limit: Annotated[int, typer.Option("--limit", help="Maximum matches to print.")] = 50,
) -> None:
    """Search local text files."""
    _permissions().require_allowed(
        PermissionRequest(tool="edit", action="search files", target=str(root)),
        approved=True,
    )
    matches = FileTools().search(root=root, query=query, limit=limit)
    _audit().write("file.search", query=query, root=str(root), match_count=len(matches))
    if not matches:
        console.print("No matches.")
        return
    for match in matches:
        console.print(f"{match.path}:{match.line_number}: {match.line}")


@shell_app.command("run")
def shell_run(
    args: Annotated[list[str], typer.Argument(help="Command and arguments to execute.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Approve ask-gated shell execution."),
    ] = False,
    timeout: Annotated[int, typer.Option("--timeout", help="Timeout in seconds.")] = 120,
) -> None:
    """Run a shell command without invoking a shell interpreter."""
    _permissions().require_allowed(
        PermissionRequest(tool="shell", action="run command", target=" ".join(args)),
        approved=yes,
    )
    result = ShellTools().run(args, timeout=timeout)
    _audit().write(
        "shell.executed",
        args=result.args,
        returncode=result.returncode,
        timeout=timeout,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    raise typer.Exit(code=result.returncode)


@safety_app.command("scan")
def safety_scan(
    text: Annotated[str | None, typer.Argument(help="Text to scan.")] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f", help="File to scan.")] = None,
) -> None:
    """Scan text or a file for obvious secrets."""
    if text is None and file is None:
        raise typer.BadParameter("Provide text or --file.")
    content = file.read_text(encoding="utf-8") if file else text or ""
    findings = _safety().scan(content)
    _audit().write(
        "safety.scan",
        source=str(file) if file else "argument",
        finding_count=len(findings),
        finding_kinds=sorted({finding.kind for finding in findings}),
    )
    if not findings:
        console.print("No obvious secrets detected.")
        return
    for finding in findings:
        console.print(f"- {finding.kind}: {finding.snippet} ({finding.start}-{finding.end})")
    raise typer.Exit(code=1)


@sessions_app.command("list")
def sessions_list() -> None:
    """List saved sessions."""
    store = SessionStore(load_config().sessions)
    records = store.list()
    if not records:
        console.print("No saved sessions yet.")
        return
    for record in records:
        console.print(
            f"- [bold]{record['session_id']}[/bold] "
            f"({record['mode']}, {record['created_at']}): {record['prompt']}"
        )


@sessions_app.command("show")
def sessions_show(session_id: Annotated[str, typer.Argument(help="Session ID.")]) -> None:
    """Show a saved session."""
    store = SessionStore(load_config().sessions)
    try:
        record = store.get(session_id)
    except FileNotFoundError as error:
        raise typer.BadParameter(str(error)) from error
    console.print_json(data=record)
