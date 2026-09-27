"""The root Typer application object."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="loro",
    help=(
        "Enterprise agent harness for coding, governed data, and productivity work. "
        "Start with `loro get-started`, then run `loro plan` or `loro run`."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
)
