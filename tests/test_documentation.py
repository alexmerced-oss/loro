import re
import shlex
from pathlib import Path

from typer.main import get_command
from typer.testing import CliRunner

from loro.cli import app

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def test_relative_markdown_links_resolve() -> None:
    broken: list[str] = []
    for path in MARKDOWN_FILES:
        for target in re.findall(r"(?<!!)\[[^]]*\]\(([^)]+)\)", path.read_text()):
            if not target or target.startswith(("http://", "https://", "mailto:", "#", "<")):
                continue
            relative_target = target.split("#", maxsplit=1)[0]
            if not (path.parent / relative_target).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)}: {target}")
    assert not broken, "Broken relative Markdown links:\n" + "\n".join(broken)


def test_documented_loro_command_paths_exist() -> None:
    root = get_command(app)
    broken: list[str] = []
    for path in MARKDOWN_FILES:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            command = line.strip().removesuffix("\\").strip()
            if not command.startswith("loro ") or re.match(
                r"^loro(?: [a-z-]+)?: ", command
            ):
                continue
            try:
                tokens = shlex.split(command)
            except ValueError:
                continue
            if len(tokens) < 2 or tokens[1].startswith("-"):
                continue
            top_level = root.commands.get(tokens[1])
            location = f"{path.relative_to(ROOT)}:{line_number}"
            if top_level is None:
                broken.append(f"{location}: unknown command {tokens[1]!r}")
                continue
            subcommands = getattr(top_level, "commands", None)
            candidates = [token for token in tokens[2:] if not token.startswith("-")]
            if subcommands and candidates and candidates[0] not in subcommands:
                broken.append(f"{location}: unknown subcommand {candidates[0]!r}")
    assert not broken, "Invalid documented Loro commands:\n" + "\n".join(broken)


def test_cli_command_map_matches_registered_commands() -> None:
    root = get_command(app)
    guide = (ROOT / "docs" / "cli.md").read_text()
    documented = {
        path: {command.strip() for command in commands.split(",")}
        for path, commands in re.findall(r"^(loro(?: [a-z-]+)?): (.+)$", guide, re.MULTILINE)
    }
    assert documented["loro"] == set(root.commands)
    command_groups = {
        f"loro {name}": set(command.commands)
        for name, command in root.commands.items()
        if getattr(command, "commands", None)
    }
    assert documented.keys() == {"loro", *command_groups}
    assert {path: documented[path] for path in command_groups} == command_groups


def test_every_cli_help_surface_renders() -> None:
    root = get_command(app)
    paths = [[name] for name in root.commands]
    paths.extend(
        [name, subcommand]
        for name, command in root.commands.items()
        for subcommand in getattr(command, "commands", {})
    )
    runner = CliRunner()
    failures: list[str] = []
    for path in paths:
        result = runner.invoke(app, [*path, "--help"])
        if result.exit_code != 0 or "Usage:" not in result.output:
            failures.append(f"{' '.join(path)}: exit {result.exit_code}: {result.exception}")
    assert not failures, "Broken CLI help surfaces:\n" + "\n".join(failures)
