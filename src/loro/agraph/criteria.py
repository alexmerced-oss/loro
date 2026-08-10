from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

import jsonschema

from loro.agraph.expressions import evaluate
from loro.config import LoroConfig
from loro.models import ModelMessage, create_model_client
from loro.sandbox import SandboxRunner


@dataclass(frozen=True)
class CriterionResult:
    id: str
    kind: str
    severity: str
    passed: bool
    evidence: str = ""

    def payload(self) -> dict[str, Any]:
        return asdict(self)


ExternalChecker = Callable[[dict[str, Any], Mapping[str, Any]], tuple[bool, str]]


class CriteriaEvaluator:
    def __init__(
        self,
        config: LoroConfig,
        *,
        workspace: Path,
        external: Mapping[str, ExternalChecker] | None = None,
        human: Callable[[str, tuple[str, ...]], bool] | None = None,
    ) -> None:
        self.config = config
        self.workspace = workspace.resolve()
        self.external = dict(external or {})
        self.human = human

    def evaluate(
        self, criterion: dict[str, Any], outputs: Mapping[str, Any], scope: Mapping[str, Any]
    ) -> CriterionResult:
        kind = str(criterion["kind"])
        try:
            passed, evidence = getattr(self, f"_{kind}")(criterion, outputs, scope)
        except Exception as error:
            passed, evidence = False, str(error)
        return CriterionResult(
            str(criterion["id"]), kind, str(criterion.get("severity", "required")), passed, evidence
        )

    def _command(
        self, item: dict[str, Any], _outputs: Mapping[str, Any], _scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        if not self.config.agraph.allow_command_criteria:
            return False, "command criteria are denied by managed policy"
        cwd = self._path(str(item.get("cwd", ".")))
        result = SandboxRunner(
            self.config.sandbox, workspace_roots=self.config.permissions.workspace_roots
        ).run(
            shlex.split(str(item["run"])),
            profile_name=self.config.sandbox.shell_profile,
            cwd=cwd,
            timeout=int(item.get("timeout_seconds", 60)),
        )
        passed = result.returncode == int(item.get("expect_exit_code", 0))
        pattern = item.get("expect_stdout_matches")
        if pattern:
            passed = passed and re.search(str(pattern), result.stdout) is not None
        return passed, (result.stdout or result.stderr)[-4000:]

    def _file_exists(
        self, item: dict[str, Any], _outputs: Mapping[str, Any], _scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        matches = [
            path.resolve()
            for path in self.workspace.glob(str(item["path"]))
            if self.workspace in path.resolve().parents
        ]
        minimum = int(item.get("min_bytes", 0))
        valid = [path for path in matches if path.is_file() and path.stat().st_size >= minimum]
        return bool(valid), ", ".join(str(path.relative_to(self.workspace)) for path in valid)

    def _artifact_present(
        self, item: dict[str, Any], outputs: Mapping[str, Any], _scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        value = outputs.get(str(item["output"]))
        path = self._path(str(value)) if value else None
        return bool(path and path.is_file()), str(value or "output is missing")

    def _json_schema(
        self, item: dict[str, Any], outputs: Mapping[str, Any], _scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        value = outputs.get(str(item["output"]))
        if isinstance(value, str) and self._path(value).is_file():
            value = json.loads(self._path(value).read_text(encoding="utf-8"))
        schema = item.get("schema")
        if schema is None:
            schema = json.loads(self._path(str(item["schema_ref"])).read_text(encoding="utf-8"))
        jsonschema.validate(value, schema)
        return True, "JSON Schema validation passed"

    def _regex(
        self, item: dict[str, Any], outputs: Mapping[str, Any], scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        value = (
            evaluate(str(item["target"]), scope)
            if "target" in item
            else outputs.get(str(item["output"]))
        )
        flags = sum((getattr(re, flag.upper()) for flag in str(item.get("flags", ""))), re.NOFLAG)
        matched = re.search(str(item["pattern"]), str(value), flags) is not None
        passed = not matched if item.get("negate") else matched
        return passed, f"pattern {'matched' if matched else 'did not match'}"

    def _expression(
        self, item: dict[str, Any], _outputs: Mapping[str, Any], scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        value = evaluate(str(item["expr"]), scope)
        if not isinstance(value, bool):
            raise ValueError("criterion expression did not return a boolean")
        return value, f"expression returned {value}"

    def _human(
        self, item: dict[str, Any], _outputs: Mapping[str, Any], _scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        if self.human is None:
            return False, "human review is unavailable"
        passed = self.human(str(item["prompt"]), tuple(item.get("roles", ())))
        return passed, "approved" if passed else "rejected"

    def _external(
        self, item: dict[str, Any], _outputs: Mapping[str, Any], scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        name = str(item["check"])
        if (
            not self.config.agraph.allow_external_criteria
            or name not in self.config.agraph.external_criteria
        ):
            return False, f"external checker {name!r} is not allowed"
        checker = self.external.get(name)
        if checker is None:
            return False, f"external checker {name!r} is not registered"
        return checker(dict(item.get("params", {})), scope)

    def _llm_judge(
        self, item: dict[str, Any], outputs: Mapping[str, Any], scope: Mapping[str, Any]
    ) -> tuple[bool, str]:
        material = [evaluate(str(value), scope) for value in item.get("inputs", [])]
        prompt = (
            "Return only JSON with a numeric score from 0 to 1 and a short reason. "
            f"Rubric: {item['rubric']}\nMaterial: " + json.dumps(material or [outputs], default=str)
        )
        scores: list[float] = []
        reasons: list[str] = []
        for _ in range(int(item.get("samples", 1))):
            response = create_model_client(self.config.model).complete(
                [ModelMessage(role="user", content=prompt)]
            )
            payload = _json_object(response.content)
            score = float(payload["score"])
            if not 0 <= score <= 1:
                raise ValueError("judge score is outside 0..1")
            scores.append(score)
            reasons.append(str(payload.get("reason", "")))
        result = median(scores)
        return result >= float(item.get("threshold", 0.8)), (
            f"median score={result:.3f}; " + "; ".join(reasons)
        )

    def _path(self, value: str) -> Path:
        path = (self.workspace / value).resolve()
        if path != self.workspace and self.workspace not in path.parents:
            raise ValueError("criterion path escapes the workspace")
        return path


def _json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("judge did not return a JSON object")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("judge response must be a JSON object")
    return payload
