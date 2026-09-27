"""Loro command-line interface: the root app assembled from command-family modules."""

from __future__ import annotations

import loro.cli.core  # noqa: F401 - registers commands
import loro.cli.setup_services  # noqa: F401 - registers commands
from loro.cli._apps import app
from loro.cli._common import _runtime
from loro.cli.agents import agents_app
from loro.cli.artifacts import artifacts_app, brief_app, docs_app, sheets_app, slides_app
from loro.cli.audit import audit_app
from loro.cli.config_cmds import config_app
from loro.cli.credentials import credentials_app
from loro.cli.data import data_app
from loro.cli.files import file_app, shell_app
from loro.cli.gateway import gateway_app
from loro.cli.graph import graph_app
from loro.cli.guardrails import identity_app, policy_app, safety_app, sandbox_app
from loro.cli.mcp import mcp_app
from loro.cli.memory import memory_app
from loro.cli.operations import operations_app
from loro.cli.ops import approvals_app
from loro.cli.providers import providers_app
from loro.cli.sessions import sessions_app
from loro.cli.setup import setup_app
from loro.cli.skills import skills_app
from loro.webui.cli import web_app

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
app.add_typer(providers_app, name="providers")
app.add_typer(setup_app, name="setup")
app.add_typer(identity_app, name="identity")
app.add_typer(policy_app, name="policy")
app.add_typer(audit_app, name="audit")
app.add_typer(mcp_app, name="mcp")
app.add_typer(skills_app, name="skills")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(credentials_app, name="credentials")
app.add_typer(gateway_app, name="gateway")
app.add_typer(graph_app, name="graph")
app.add_typer(config_app, name="config")
app.add_typer(approvals_app, name="approvals")
app.add_typer(operations_app, name="operations")
app.add_typer(artifacts_app, name="artifacts")
app.add_typer(web_app, name="web")
app.add_typer(agents_app, name="agents")

__all__ = ["_runtime", "app"]
