"""Compatibility types and AGX tokens retained from AGS commit 6bf105f (MIT).

Document validation is supplied by the agentic-graph-spec support library.
Only these small interfaces are used by the Loro evaluator and diagnostics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Finding:
    code: str
    severity: str  # "error" | "warning"
    message: str
    pointer: str = ""

    def __str__(self) -> str:
        loc = f" at {self.pointer}" if self.pointer else ""
        return f"[{self.severity.upper():7}] {self.code}: {self.message}{loc}"


TOKEN_RE = re.compile(
    r"""
    \s*(?:
      (?P<number>-?\d+\.\d+|-?\d+)
    | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
    | (?P<op>&&|\|\||==|!=|<=|>=|[-+*/%<>!()\[\],.])
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    )
    """,
    re.VERBOSE,
)


class AgxError(ValueError):
    pass
