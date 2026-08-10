from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

import jsonschema

from loro import __version__
from loro.agraph import CONFORMANCE_LEVEL, SUPPORTED_FEATURES
from loro.agraph.criteria import CriteriaEvaluator, ExternalChecker
from loro.agraph.document import GraphDocument, graph_digest, load_graph
from loro.agraph.expressions import evaluate, interpolate
from loro.agraph.mapping import missing_tools, permission_resource
from loro.agraph.plan import topological_order
from loro.agraph.policy import evaluate_policy
from loro.agraph.record import aggregate_usage, validate_run_record
from loro.agraph.routing import RoutingError, route_model
from loro.agraph.schedule import ready_nodes
from loro.agraph.state import bind_self as _bind_self
from loro.agraph.state import empty_usage as _empty_usage
from loro.agraph.state import matches_type as _matches_type
from loro.agraph.state import now as _now
from loro.agraph.store import GraphRunStore
from loro.agraph.validate import validate_graph
from loro.approvals import ApprovalManager
from loro.audit import AuditLogger
from loro.config import LoroConfig
from loro.data_protection import DataProtectionEngine
from loro.identity import resolve_identity
from loro.permissions import PermissionEngine, PermissionRequest
from loro.runtime import AgentRuntime
from loro.skills import SkillError, SkillRegistry


class RuntimeResult(Protocol):
    summary: str
    session_id: str
    stop_reason: str
    usage: dict[str, int | float]
    emitted_outputs: dict[str, object]


RuntimeFactory = Callable[[LoroConfig], AgentRuntime]
GateResponse = bool | Mapping[str, Any]
GateProvider = Callable[[str, tuple[str, ...]], GateResponse]


class GraphExecutionError(RuntimeError):
    pass


class GraphExecutor:
    """Governed AGS level-3 executor built on Loro's bounded task runtime."""

    def __init__(
        self,
        config: LoroConfig,
        *,
        workspace: Path | None = None,
        gate_provider: GateProvider | None = None,
        runtime_factory: RuntimeFactory | None = None,
        external_checkers: Mapping[str, ExternalChecker] | None = None,
    ) -> None:
        self.config = config
        self.workspace = (workspace or Path.cwd()).resolve()
        self.gate_provider = gate_provider
        self.runtime_factory = runtime_factory or (lambda routed: AgentRuntime(routed))
        self.protection = DataProtectionEngine(config.safety)
        self.identity = resolve_identity(config.identity)
        self.audit = AuditLogger(config.audit, self.identity, safety_config=config.safety)
        self.approvals = ApprovalManager(
            config.approvals,
            self.identity,
            event_handler=lambda event_type, payload: self.audit.write(event_type, **dict(payload)),
        )
        self.store = GraphRunStore(config.agraph, self.protection)
        self.criteria = CriteriaEvaluator(
            config,
            workspace=self.workspace,
            human=gate_provider,
            external=external_checkers,
        )
        # Built once per executor rather than per node execution: both read immutable
        # config and were rebuilt for every node in the graph.
        self.skills = SkillRegistry(config.skills)
        self.permissions = PermissionEngine(config.permissions)
        self.executions = 0
        self._execution_lock = Lock()
        self._run_started = monotonic()
        self._graph_cost_limit: float | None = None
        self._graph_token_limit: int | None = None
        self._subgraph_depth = 0
        self._budget_failed = False
        # Completed loop iterations per composite node, restored from the run record on
        # resume so max_iterations is a whole-run budget, not a per-attempt one.
        self._loop_state: dict[str, int] = {}
        self._active_record: dict[str, Any] | None = None

    def run(
        self,
        source: str | Path | GraphDocument,
        *,
        params: Mapping[str, Any] | None = None,
        dry_run: bool = False,
        run_id: str | None = None,
        plan_approved: bool = False,
    ) -> dict[str, Any]:
        document = (
            source
            if isinstance(source, GraphDocument)
            else load_graph(source, max_bytes=self.config.agraph.max_document_bytes)
        )
        report = validate_graph(document)
        policy = evaluate_policy(document.data, self.config.agraph)
        errors = [f"{item.code}: {item.message}" for item in report.errors] + [
            f"{item.code}: {item.message}" for item in policy if item.severity == "error"
        ]
        if errors:
            self.audit.write("agraph.policy_denied", graph_id=document.graph_id, diagnostics=errors)
            raise GraphExecutionError("graph rejected:\n" + "\n".join(errors))
        self.audit.write(
            "agraph.validated",
            graph_id=document.graph_id,
            graph_digest=document.digest,
            finding_count=len(report.findings),
        )
        if not dry_run and not plan_approved:
            raise GraphExecutionError(
                f"graph digest {document.digest} requires explicit plan approval"
            )
        if not dry_run:
            self._consume_approval(
                action="agraph.run",
                target=document.graph_id,
                arguments={"graph_digest": document.digest},
                approved=True,
                non_interactive=self.gate_provider is not None,
            )
        bound = self._bind_params(document.data, params or {})
        now = _now()
        record: dict[str, Any] = {
            "ags_version": "1.0",
            "kind": "AgenticGraphRun",
            "run_id": run_id or str(uuid4()),
            "graph_id": document.graph_id,
            "graph_digest": document.digest,
            "harness": {
                "name": "loro",
                "version": __version__,
                "conformance_level": CONFORMANCE_LEVEL,
                "supported_features": list(SUPPORTED_FEATURES),
            },
            "status": "running",
            "started_at": now,
            "nodes": {
                node_id: {"node_id": node_id, "type": node.get("type", "task"), "status": "pending"}
                for node_id, node in document.data["nodes"].items()
            },
            "usage": _empty_usage(),
            "diagnostics": [],
            "metadata": {
                "source": str(document.path),
                "params": {
                    name: "[redacted]" if spec.get("redact", False) else bound.get(name)
                    for name, spec in document.data.get("params", {}).items()
                    if name in bound
                },
                "conformance_level": 3,
                "dry_run": dry_run,
            },
        }
        self.audit.bind_context(trace_id=record["run_id"])
        if dry_run:
            planned = deepcopy(record)
            planned["nodes"] = list(planned["nodes"].values())
            validate_run_record(planned)
            self.store.save(planned)
            return planned
        self.store.save(record)
        return self._execute_document(document.data, record, bound)

    def resume(
        self,
        run_id: str,
        *,
        force: bool = False,
        force_approved: bool = False,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.store.get(run_id)
        if bool(record.get("metadata", {}).get("dry_run", False)):
            # A dry-run record is a plan, not a paused execution: resuming it would run
            # real tools under the guise of the plan the operator asked not to execute.
            raise GraphExecutionError(
                "run was created with --dry-run; start a real run instead of resuming a plan"
            )
        if isinstance(record.get("nodes"), list):
            record["nodes"] = {node["node_id"]: node for node in record["nodes"]}
        for node in record["nodes"].values():
            if node["status"] == "awaiting_human":
                node["status"] = "pending"
        source = Path(str(record.get("metadata", {}).get("source", "")))
        document = load_graph(source, max_bytes=self.config.agraph.max_document_bytes)
        report = validate_graph(document)
        policy = evaluate_policy(document.data, self.config.agraph)
        errors = [f"{item.code}: {item.message}" for item in report.errors] + [
            f"{item.code}: {item.message}" for item in policy if item.severity == "error"
        ]
        if errors:
            self.audit.write(
                "agraph.policy_denied",
                graph_id=document.graph_id,
                resume=True,
                diagnostics=errors,
            )
            raise GraphExecutionError("graph rejected on resume:\n" + "\n".join(errors))
        if record.get("graph_digest") != document.digest and not force:
            raise GraphExecutionError("graph digest changed; use --force to resume intentionally")
        if force:
            if not force_approved:
                raise GraphExecutionError("forced resume requires approval of the new digest")
            self._consume_approval(
                action="agraph.resume_force",
                target=document.graph_id,
                arguments={"old_digest": record.get("graph_digest"), "new_digest": document.digest},
                approved=True,
                non_interactive=self.gate_provider is not None,
            )
            self.audit.write(
                "agraph.resume_forced", graph_id=document.graph_id, graph_digest=document.digest
            )
            record["graph_digest"] = document.digest
        record["status"] = "running"
        resumed_params = dict(record["metadata"].get("params", {}))
        resumed_params.update(params or {})
        if "[redacted]" in resumed_params.values():
            raise GraphExecutionError(
                "redacted graph params must be supplied again with --params when resuming"
            )
        bound = self._bind_params(document.data, resumed_params)
        return self._execute_document(document.data, record, bound)

    def _execute_document(
        self, graph: dict[str, Any], record: dict[str, Any], params: Mapping[str, Any]
    ) -> dict[str, Any]:
        constraints = graph.get("constraints", {})
        cost_limits = [
            value
            for value in (constraints.get("max_cost_usd"), self.config.agraph.max_cost_usd)
            if value is not None
        ]
        self._graph_cost_limit = min(map(float, cost_limits)) if cost_limits else None
        self._graph_token_limit = (
            int(constraints["max_total_tokens"])
            if constraints.get("max_total_tokens") is not None
            else None
        )
        self._run_started = monotonic()
        self._budget_failed = False
        self._active_record = record
        self._loop_state = {
            node_id: int(node_record["iterations"])
            for node_id, node_record in record["nodes"].items()
            if isinstance(node_record.get("iterations"), int)
        }
        scope: dict[str, Any] = {
            "graph": {"id": graph["id"]},
            "params": dict(params),
            "context": dict(graph.get("context", {})),
            "attachments": {},
            "env": {},
            "nodes": {},
        }
        for node_id, node_record in record["nodes"].items():
            status = node_record["status"]
            scope["nodes"][node_id] = {
                "status": status,
                "outputs": dict(node_record.get("outputs", {})),
            }
        self._execute_scope(graph, record, scope)
        states = {node_id: value["status"] for node_id, value in record["nodes"].items()}
        if "awaiting_human" in states.values():
            record["status"] = "awaiting_human"
        elif self._budget_failed or any(
            status in {"failed", "blocked"} for status in states.values()
        ):
            record["status"] = "failed"
            self._compensate(graph, record, scope)
        else:
            record["status"] = "succeeded"
            record["outputs"] = {
                name: self._graph_output_value(graph, spec, scope)
                for name, spec in graph.get("outputs", {}).items()
            }
        if record["status"] != "awaiting_human":
            record["finished_at"] = _now()
        else:
            record.pop("finished_at", None)
        record["usage"] = aggregate_usage(record)
        record["nodes"] = list(record["nodes"].values())
        validate_run_record(record)
        self.store.save(record)
        return record

    def _execute_scope(
        self, graph: dict[str, Any], record: dict[str, Any], scope: dict[str, Any]
    ) -> None:
        states = {node_id: value["status"] for node_id, value in record["nodes"].items()}
        while True:
            candidates = ready_nodes(graph, states, scope)
            if not candidates:
                break
            runnable: list[str] = []
            active_groups: set[str] = set()
            for node_id in candidates[: self._parallel_limit(graph)]:
                node = graph["nodes"][node_id]
                if "when" in node and not bool(evaluate(str(node["when"]), scope)):
                    self._finish(record, scope, states, node_id, "skipped", {})
                    continue
                group = str((node.get("constraints") or {}).get("concurrency_group", ""))
                if group and group in active_groups:
                    continue
                active_groups.add(group)
                runnable.append(node_id)
                self.audit.write("agraph.node_started", graph_node_id=node_id)
            snapshot = deepcopy(scope)
            with ThreadPoolExecutor(max_workers=max(1, len(runnable))) as pool:
                futures = {
                    node_id: pool.submit(
                        self._run_node,
                        node_id,
                        graph["nodes"][node_id],
                        graph,
                        deepcopy(snapshot),
                    )
                    for node_id in runnable
                }
                outcomes = {node_id: futures[node_id].result() for node_id in runnable}
            for node_id in runnable:
                outcome, outputs, details = outcomes[node_id]
                node_record = record["nodes"][node_id]
                node_record.update(details)
                self._finish(record, scope, states, node_id, outcome, outputs)
                # `node` above leaks from the candidate loop and holds the *last*
                # candidate, so with max_parallel_nodes > 1 outputs were filtered and
                # redacted against another node's output contract.
                node_record["outputs"] = {
                    name: "[redacted]" if spec.get("redact", False) else outputs[name]
                    for name, spec in graph["nodes"][node_id].get("outputs", {}).items()
                    if name in outputs
                }
                self.audit.write(
                    "agraph.node_completed",
                    graph_node_id=node_id,
                    status=outcome,
                    output_names=sorted(outputs),
                )
                if record.get("run_id"):
                    self.store.save(record)
                if outcome == "awaiting_human":
                    return
                if self._budget_exceeded(graph, record):
                    for pending_id, status in states.items():
                        if status == "pending":
                            self._finish(record, scope, states, pending_id, "blocked", {})
                    return

    def _run_node(
        self, node_id: str, node: dict[str, Any], graph: dict[str, Any], scope: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        with self._execution_lock:
            self.executions += 1
            execution_number = self.executions
        maximum = min(
            int(
                graph.get("constraints", {}).get(
                    "max_node_executions", self.config.agraph.max_node_executions
                )
            ),
            self.config.agraph.max_node_executions,
        )
        if execution_number > maximum:
            return "failed", {}, {}
        node_type = node.get("type", "task")
        if node_type == "gate":
            return self._run_gate(node)
        if node_type == "decision":
            return self._run_decision(node, scope)
        if node_type in {"loop", "map", "subgraph"}:
            return self._run_composite(node_id, node, graph, scope)
        result = self._run_task(node_id, node, graph, scope)
        if result[0] not in {"failed", "blocked"}:
            return result
        for fallback in (node.get("failure") or {}).get("fallback", []):
            if "when" in fallback and not bool(evaluate(str(fallback["when"]), scope)):
                continue
            strategy = fallback["strategy"]
            if strategy == "skip":
                return "skipped", result[1], {**result[2], "fallback_used": "skip"}
            if strategy == "degrade_outputs" and all(
                name in result[1] for name in fallback["outputs"]
            ):
                outputs = {name: result[1][name] for name in fallback["outputs"]}
                return (
                    "succeeded",
                    outputs,
                    {
                        **result[2],
                        "fallback_used": "degrade_outputs",
                    },
                )
            if strategy == "relax_criteria":
                attempts = result[2].get("attempts", [])
                latest = attempts[-1].get("success", {}) if attempts else {}
                relaxed = set(fallback["criteria"])
                blocking = {
                    item["id"]
                    for item in latest.get("results", [])
                    if item["severity"] == "required" and not item["passed"]
                }
                if blocking and blocking.issubset(relaxed):
                    return (
                        "succeeded",
                        result[1],
                        {**result[2], "fallback_used": "relax_criteria"},
                    )
            if strategy == "human_takeover" and self.gate_provider is not None:
                approved = self.gate_provider(
                    str(fallback.get("description", "Approve human takeover?")), ()
                )
                if approved:
                    return (
                        "succeeded",
                        result[1],
                        {
                            **result[2],
                            "fallback_used": "human_takeover",
                        },
                    )
            if strategy == "alternate_node":
                alternate = str(fallback["node"])
                outcome, outputs, details = self._run_node(
                    alternate, graph["nodes"][alternate], graph, scope
                )
                return (
                    outcome,
                    outputs,
                    {
                        **details,
                        "fallback_used": f"alternate_node:{alternate}",
                    },
                )
        return result

    def _run_task(
        self,
        node_id: str,
        node: dict[str, Any],
        graph: dict[str, Any],
        scope: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        missing = missing_tools((node.get("requirements") or {}).get("tools", ()))
        if missing:
            return "blocked", {}, {}
        requirements = node.get("requirements") or {}
        declarations = {item["name"]: item for item in graph.get("secrets", [])}
        for name in requirements.get("secrets", ()):
            declaration = declarations.get(str(name), {"required": True})
            if declaration.get("required", True) and str(name) not in os.environ:
                return "blocked", {}, {}
        configured_mcp = set(self.config.mcp.servers) if self.config.mcp.enabled else set()
        if set(requirements.get("mcp_servers", ())) - configured_mcp:
            return "blocked", {}, {}
        try:
            for name in requirements.get("skills", ()):
                self.skills.get(str(name), require_enabled=True)
        except SkillError:
            return "blocked", {}, {}
        for permission in requirements.get("permissions", ()):
            tool, action, target = permission_resource(str(permission))
            try:
                self.permissions.require_allowed(
                    PermissionRequest(tool=tool, action=action, target=target), approved=False
                )
            except PermissionError as error:
                # A node whose declared permissions are denied or need approval blocks that
                # node; it must not raise out of the scheduler and abandon the whole run
                # with no record of which node was responsible.
                self.audit.write(
                    "agraph.node_blocked",
                    graph_node_id=node_id,
                    reason=str(error),
                    permission=str(permission),
                )
                record = self._active_record
                if record is not None and isinstance(record.get("diagnostics"), list):
                    record["diagnostics"].append(
                        {
                            "code": "RT021",
                            "severity": "error",
                            "message": f"permission {permission!r} is not granted: {error}",
                            "node_id": node_id,
                        }
                    )
                return "blocked", {}, {}
        node_scope = deepcopy(scope)
        node_scope["env"] = {
            name: os.environ[name]
            for name in requirements.get("environment", ())
            if name in os.environ
        }
        inputs = self._resolve_inputs(node.get("inputs", {}), node_scope)
        _bind_self(node_scope, node_id=node_id, attempt=1, inputs=inputs, status="running")
        checkpoints = node.get("human", [])
        if not self._checkpoints(checkpoints, "before_start", scope):
            return "awaiting_human", {}, {}
        side_effecting = requirements.get("workspace") == "read_write" or any(
            any(token in str(permission) for token in (":write:", ":push:", ":commit:"))
            for permission in requirements.get("permissions", ())
        )
        if side_effecting and not self._checkpoints(checkpoints, "before_side_effects", scope):
            return "awaiting_human", {}, {}
        retry = (node.get("failure") or {}).get("retry", {})
        maximum = int(retry.get("max_attempts", 1))
        attempts: list[dict[str, Any]] = []
        outputs: dict[str, Any] = {}
        feedback = ""
        for attempt_number in range(1, maximum + 1):
            if attempt_number > 1 and not self._checkpoints(checkpoints, "on_escalation", scope):
                return "awaiting_human", outputs, {"attempts": attempts}
            try:
                decision = route_model(self.config, node.get("intelligence"), attempt_number)
            except RoutingError as error:
                # An unroutable intelligence block must block this node, not abort the
                # whole run with an unhandled error out of the scheduler.
                self._record_diagnostic(node_id, error)
                return "blocked", {}, {"attempts": attempts}
            routed = self._node_config(
                decision.model_config(self.config.model), node.get("constraints", {})
            )
            prompt = self._prompt(node_id, node, inputs, feedback)
            started = _now()
            try:
                result: RuntimeResult = self.runtime_factory(routed).run(prompt, mode="run")
                outputs = dict(result.emitted_outputs)
                self._discover_outputs(outputs, node.get("outputs", {}))
                missing_output = self._validate_outputs(outputs, node.get("outputs", {}))
                criteria = self._evaluate_criteria(
                    node,
                    outputs,
                    scope,
                    node_id,
                    attempt=attempt_number,
                    inputs=inputs,
                )
                required = [item for item in criteria if item["severity"] == "required"]
                mode = (node.get("success") or {}).get("mode", "all")
                count = int((node.get("success") or {}).get("count", len(required)))
                passed_count = sum(bool(item["passed"]) for item in required)
                success = not missing_output and (
                    passed_count == len(required)
                    if mode == "all"
                    else passed_count > 0
                    if mode == "any"
                    else passed_count >= count
                )
                failure_class = "output_missing" if missing_output else "criteria_failed"
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "status": "succeeded" if success else "failed",
                        "started_at": started,
                        "finished_at": _now(),
                        "routed": decision.__dict__,
                        "usage": result.usage,
                        "success": {"passed": success, "mode": mode, "results": criteria},
                        **({} if success else {"error": f"{failure_class}: node contract failed"}),
                    }
                )
                if success and self._checkpoints(
                    checkpoints, "after_outputs", {**scope, "outputs": outputs}
                ):
                    return "succeeded", outputs, {"attempts": attempts}
                if not self._retry_allowed(retry, failure_class):
                    break
                if not self._checkpoints(
                    checkpoints, "on_criteria_failure", {**scope, "outputs": outputs}
                ):
                    return "awaiting_human", outputs, {"attempts": attempts}
                feedback = "Failed criteria:\n" + "\n".join(
                    f"- {item['id']}: {item['evidence']}" for item in criteria if not item["passed"]
                )
            except Exception as error:
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "status": "failed",
                        "started_at": started,
                        "finished_at": _now(),
                        "error": f"model_error: {error}",
                    }
                )
                feedback = str(error)
                if not self._retry_allowed(retry, "model_error"):
                    break
        disposition = (node.get("failure") or {}).get("on_exhausted", "fail")
        if disposition in {"skip", "succeed_degraded"}:
            return (
                ("skipped" if disposition == "skip" else "succeeded"),
                outputs,
                {"attempts": attempts},
            )
        if not self._checkpoints(checkpoints, "on_failure", scope):
            return "awaiting_human", outputs, {"attempts": attempts}
        return "failed", outputs, {"attempts": attempts}

    def _run_gate(self, node: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        gate = node["gate"]
        roles = tuple(gate.get("roles", ()))
        if roles and not set(roles).intersection(resolve_identity(self.config.identity).roles):
            return "blocked", {}, {}
        if gate.get("mode") == "notify":
            return (
                "succeeded",
                {},
                {
                    "human_events": [
                        {
                            "at_stage": "gate",
                            "mode": "notify",
                            "outcome": "notified",
                            "at": _now(),
                        }
                    ]
                },
            )
        if self.gate_provider is None:
            return "awaiting_human", {}, {}
        response = self.gate_provider(str(gate.get("prompt") or node["description"]), roles)
        collected = dict(response) if isinstance(response, Mapping) else {}
        if gate.get("mode") == "input":
            for name, spec in gate.get("collect", {}).items():
                if name not in collected and "default" in spec:
                    collected[name] = spec["default"]
                if name not in collected and spec.get("required", True):
                    return "awaiting_human", {}, {}
                if name in collected and not _matches_type(collected[name], str(spec["type"])):
                    return "failed", {}, {}
        approved = bool(response)
        if approved:
            self._consume_approval(
                action="agraph.gate",
                target=str(node.get("title", "gate")),
                arguments={"prompt": str(gate.get("prompt") or node["description"])},
                approved=True,
                non_interactive=True,
            )
        outcome = "approved" if approved else "rejected"
        self.audit.write("agraph.gate_decided", outcome=outcome, roles=list(roles))
        return (
            ("succeeded" if approved else "failed"),
            {"decision": outcome, **collected},
            {
                "decision": outcome,
                "human_events": [
                    {
                        "at_stage": "gate",
                        "mode": gate["mode"],
                        "outcome": outcome,
                        "at": _now(),
                    }
                ],
            },
        )

    def _run_decision(
        self, node: dict[str, Any], scope: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        decision = node["decision"]
        selected = None
        if decision.get("evaluator", "agent") == "expression":
            for branch in decision["branches"]:
                if bool(evaluate(str(branch["when"]), scope)):
                    selected = branch["label"]
                    break
        else:
            labels = [str(branch["label"]) for branch in decision["branches"]]
            try:
                routed = route_model(self.config, node.get("intelligence"))
            except RoutingError as error:
                self._record_diagnostic(node.get("id", ""), error)
                return "blocked", {}, {}
            result = self.runtime_factory(
                self.config.model_copy(update={"model": routed.model_config(self.config.model)})
            ).run(
                f"{decision.get('question') or node['description']}\n"
                f"Choose exactly one branch label: {', '.join(labels)}. "
                "Emit it as graph output named decision.",
                mode="run",
            )
            emitted = result.emitted_outputs.get("decision")
            if emitted in labels:
                selected = str(emitted)
            else:
                selected = next((label for label in labels if label in result.summary), None)
        selected = selected or decision.get("default_branch")
        if selected is None:
            return "failed", {}, {}
        return "succeeded", {"decision": selected}, {"decision": selected}

    def _run_composite(
        self, node_id: str, node: dict[str, Any], graph: dict[str, Any], scope: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        node_type = node["type"]
        block = node[node_type]
        if node_type == "subgraph":
            maximum_depth = int(graph.get("constraints", {}).get("max_subgraph_depth", 5))
            if self._subgraph_depth >= maximum_depth:
                return "failed", {}, {}
            fragment = self._fragment(block, graph)
            child_scope = (
                deepcopy(scope)
                if block.get("inherit_context", False)
                else {
                    "graph": scope["graph"],
                    "params": {},
                    "context": {},
                    "attachments": {},
                    "env": {},
                    "nodes": {},
                }
            )
            supplied_params = {
                name: evaluate(str(expr), scope) for name, expr in block.get("params", {}).items()
            }
            if block.get("inherit_context", False):
                child_scope["params"].update(supplied_params)
            else:
                child_scope["params"] = supplied_params
            child_scope["context"].update(block.get("context", {}))
            self._subgraph_depth += 1
            try:
                child = self._run_fragment(fragment, child_scope)
            finally:
                self._subgraph_depth -= 1
            bindings = block.get("outputs_from") or {
                name: spec["from"] for name, spec in fragment.get("outputs", {}).items()
            }
            outputs = {name: evaluate(str(expr), child_scope) for name, expr in bindings.items()}
            return child, outputs, {"child_run_id": f"fragment:{node_id}"}
        fragment = self._fragment(block, graph)
        if node_type == "map":
            items = evaluate(str(block["over"]), scope)
            if not isinstance(items, list):
                return "failed", {}, {}
            if len(items) > int(block["max_items"]):
                if block.get("on_over_limit", "fail") == "fail":
                    return "failed", {}, {}
                items = items[: int(block["max_items"])]
            scopes = []
            successes = 0
            for index, item in enumerate(items):
                child_scope = deepcopy(scope)
                child_scope[block["as"]] = item
                child_scope[block.get("index_as", "index")] = index
                item_status = self._run_fragment(fragment, child_scope)
                successes += int(item_status == "succeeded")
                if (
                    item_status != "succeeded"
                    and block.get("on_item_failure", "fail_fast") == "fail_fast"
                ):
                    return "failed", {}, {"items": len(scopes) + 1}
                scopes.append(child_scope)
            if block.get("on_item_failure") == "threshold" and successes < int(
                block["min_successes"]
            ):
                return "failed", {}, {"items": len(scopes)}
            outputs = {
                name: [evaluate(str(expr), item_scope) for item_scope in scopes]
                for name, expr in block.get("collect", {}).items()
            }
            return "succeeded", outputs, {"items": len(scopes)}
        iterations: list[dict[str, Any]] = []
        condition_reached = block["mode"] == "repeat"
        # Iterations already consumed by an earlier run of this node. Without it a resumed
        # bounded loop restarts at 0 and can exceed max_iterations across the boundary.
        consumed = self._consumed_iterations(node_id)
        maximum = int(block["max_iterations"])
        previous_outputs: dict[str, Any] = {}
        child_scope: dict[str, Any] = deepcopy(scope)
        for index in range(consumed, maximum):
            loop_binding = {
                "index": index,
                "iteration": index + 1,
                "previous": dict(previous_outputs),
            }
            scope["loop"] = loop_binding
            if block["mode"] == "while" and not bool(evaluate(str(block["condition"]), scope)):
                condition_reached = True
                break
            child_scope = deepcopy(scope)
            child_scope["iteration"] = index
            child_scope["loop"] = loop_binding
            status = self._run_fragment(fragment, child_scope)
            iterations.append({"iteration": index, "status": status})
            self._record_iterations(node_id, index + 1)
            scope.update({key: value for key, value in child_scope.items() if key not in {"nodes"}})
            fragment_outputs = {
                name: evaluate(str(spec["from"]), child_scope)
                for name, spec in fragment.get("outputs", {}).items()
            }
            previous_outputs = fragment_outputs
            for output_name, input_name in block.get("carry", {}).items():
                if output_name in fragment_outputs:
                    scope.setdefault("params", {})[input_name] = fragment_outputs[output_name]
            if status != "succeeded":
                return "failed", {}, {"iterations": consumed + len(iterations)}
            if block["mode"] == "until" and bool(evaluate(str(block["condition"]), child_scope)):
                condition_reached = True
                break
        if not condition_reached and block.get("on_max_iterations", "fail") == "fail":
            return "failed", {}, {"iterations": consumed + len(iterations)}
        outputs = (
            {
                name: evaluate(str(expr), child_scope)
                for name, expr in block.get("collect", {}).items()
            }
            if iterations
            else {}
        )
        return "succeeded", outputs, {"iterations": consumed + len(iterations)}

    def _consumed_iterations(self, node_id: str) -> int:
        return int(self._loop_state.get(node_id, 0))

    def _record_iterations(self, node_id: str, completed: int) -> None:
        with self._execution_lock:
            self._loop_state[node_id] = max(self._loop_state.get(node_id, 0), completed)
            record = self._active_record
            if record is not None:
                node_record = record.get("nodes", {}).get(node_id)
                if isinstance(node_record, dict):
                    node_record["iterations"] = self._loop_state[node_id]

    def _run_fragment(self, fragment: dict[str, Any], scope: dict[str, Any]) -> str:
        fragment_record = {
            "nodes": {
                node_id: {"node_id": node_id, "type": node.get("type", "task"), "status": "pending"}
                for node_id, node in fragment["nodes"].items()
            }
        }
        scope["nodes"] = {}
        self._execute_scope(fragment, fragment_record, scope)
        return (
            "failed"
            if any(node["status"] == "failed" for node in fragment_record["nodes"].values())
            else "succeeded"
        )

    def _fragment(self, block: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
        if "inline" in block:
            return block["inline"]
        if "body" in block:
            return block["body"]
        if "use" in block:
            return graph["subgraphs"][block["use"]]
        ref = block["ref"]
        uri = str(ref["uri"])
        if "://" in uri:
            raise GraphExecutionError("external subgraph retrieval is not enabled in this runtime")
        source = (self.workspace / uri).resolve()
        if source != self.workspace and self.workspace not in source.parents:
            raise GraphExecutionError("subgraph reference escapes the workspace")
        document = load_graph(source, max_bytes=self.config.agraph.max_document_bytes)
        if ref.get("integrity") and ref["integrity"] != graph_digest(document.data):
            raise GraphExecutionError("subgraph integrity check failed")
        if ref.get("expected_id") and ref["expected_id"] != document.graph_id:
            raise GraphExecutionError("subgraph id does not match expected_id")
        return document.data

    def _evaluate_criteria(
        self,
        node: dict[str, Any],
        outputs: dict[str, Any],
        scope: dict[str, Any],
        node_id: str,
        *,
        attempt: int = 1,
        inputs: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        local_scope = deepcopy(scope)
        local_scope["nodes"][node_id] = {"status": "running", "outputs": outputs}
        # The validator accepts `self.outputs.*` and `nodes.self.*`, but nothing bound
        # them, so every documented criterion using them failed as "unknown binding".
        _bind_self(
            local_scope,
            node_id=node_id,
            attempt=attempt,
            inputs=inputs or {},
            outputs=outputs,
            status="running",
        )
        results = [
            self.criteria.evaluate(item, outputs, local_scope).payload()
            for item in (node.get("success") or {}).get("criteria", [])
        ]
        self.audit.write(
            "agraph.criteria_evaluated",
            graph_node_id=node_id,
            passed=all(item["passed"] or item["severity"] == "advisory" for item in results),
            criterion_ids=[item["id"] for item in results],
        )
        return results

    def _resolve_inputs(
        self, specs: Mapping[str, dict[str, Any]], scope: Mapping[str, Any]
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, spec in specs.items():
            if "from" in spec:
                value = evaluate(str(spec["from"]), scope)
            elif "template" in spec:
                value = interpolate(str(spec["template"]), scope)
            elif "value" in spec:
                value = spec["value"]
            else:
                value = spec.get("default")
            if value is None and spec.get("required", True):
                raise GraphExecutionError(f"required input {name!r} is missing")
            values[name] = value
        return values

    @staticmethod
    def _graph_output_value(
        graph: Mapping[str, Any], spec: Mapping[str, Any], scope: Mapping[str, Any]
    ) -> Any:
        expression = str(spec["from"])
        match = re.fullmatch(r"nodes\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)", expression)
        if match:
            node = graph.get("nodes", {}).get(match.group(1), {})
            output = node.get("outputs", {}).get(match.group(2), {})
            if output.get("redact", False):
                return "[redacted]"
        return evaluate(expression, scope)

    def _prompt(
        self, node_id: str, node: dict[str, Any], inputs: dict[str, Any], feedback: str
    ) -> str:
        output_contract = node.get("outputs", {})
        return (
            f"Execute AGS node {node_id}: {node['description']}\n\n"
            f"Instructions: {node.get('instructions', '')}\n"
            f"Inputs: {json.dumps(inputs, default=str)}\n"
            f"Output contract: {json.dumps(output_contract)}\n"
            "Emit structured outputs with the graph.emit_output tool. "
            "Pass name and value arguments.\n"
            f"{feedback}"
        )

    def _discover_outputs(
        self, outputs: dict[str, Any], specs: Mapping[str, dict[str, Any]]
    ) -> None:
        for name, spec in specs.items():
            hint = spec.get("path_hint")
            if name not in outputs and hint:
                path = (self.workspace / str(hint)).resolve()
                if (path == self.workspace or self.workspace in path.parents) and path.is_file():
                    outputs[name] = str(hint)

    @staticmethod
    def _validate_outputs(
        outputs: Mapping[str, Any], specs: Mapping[str, dict[str, Any]]
    ) -> list[str]:
        missing: list[str] = []
        for name, spec in specs.items():
            if spec.get("required", True) and name not in outputs:
                missing.append(name)
                continue
            if name in outputs and not _matches_type(outputs[name], str(spec["type"])):
                missing.append(name)
            if name in outputs and spec.get("schema"):
                jsonschema.validate(outputs[name], spec["schema"])
        return missing

    def _checkpoints(
        self, checkpoints: list[dict[str, Any]], at: str, scope: Mapping[str, Any]
    ) -> bool:
        for checkpoint in checkpoints:
            if checkpoint["at"] != at or (
                "when" in checkpoint and not evaluate(str(checkpoint["when"]), scope)
            ):
                continue
            if checkpoint.get("mode") == "notify":
                continue
            if self.gate_provider is None or not self.gate_provider(
                str(checkpoint.get("prompt", "Approve?")), tuple(checkpoint.get("roles", ()))
            ):
                return False
        return True

    def _node_config(self, model: Any, constraints: Mapping[str, Any]) -> LoroConfig:
        def bounded(current: int | float | None, requested: object) -> int | float | None:
            value = current if requested is None else requested
            if current is None:
                return value  # type: ignore[return-value]
            return min(current, value)  # type: ignore[type-var,return-value]

        runtime = self.config.runtime.model_copy(
            update={
                "max_steps": min(
                    self.config.runtime.max_steps,
                    int(constraints.get("max_agent_steps", self.config.runtime.max_steps)),
                ),
                "max_tool_calls": min(
                    self.config.runtime.max_tool_calls,
                    int(constraints.get("max_tool_calls", self.config.runtime.max_tool_calls)),
                ),
                "max_input_tokens": bounded(
                    self.config.runtime.max_input_tokens, constraints.get("max_input_tokens")
                ),
                "max_output_tokens": bounded(
                    self.config.runtime.max_output_tokens, constraints.get("max_output_tokens")
                ),
                "max_cost_usd": bounded(
                    self.config.runtime.max_cost_usd, constraints.get("max_cost_usd")
                ),
            }
        )
        parallel = max(1, self.config.agraph.max_parallel_nodes)
        if self._graph_cost_limit is not None:
            runtime.max_cost_usd = bounded(runtime.max_cost_usd, self._graph_cost_limit / parallel)
        if self._graph_token_limit is not None:
            runtime.max_input_tokens = bounded(
                runtime.max_input_tokens, self._graph_token_limit // parallel
            )
        return self.config.model_copy(update={"model": model, "runtime": runtime})

    def _parallel_limit(self, graph: Mapping[str, Any]) -> int:
        requested = int(graph.get("constraints", {}).get("max_parallel_nodes", 1))
        return max(1, min(requested, self.config.agraph.max_parallel_nodes))

    def _budget_exceeded(self, graph: Mapping[str, Any], record: dict[str, Any]) -> bool:
        usage = aggregate_usage(record)
        record["usage"] = usage
        exceeded = False
        if self._graph_cost_limit is not None and usage["cost_usd"] > self._graph_cost_limit:
            exceeded = True
        if self._graph_token_limit is not None and usage["total_tokens"] > self._graph_token_limit:
            exceeded = True
        wall_limit = graph.get("constraints", {}).get("max_wall_clock_seconds")
        if wall_limit is not None and monotonic() - self._run_started > float(wall_limit):
            exceeded = True
        if exceeded:
            self._budget_failed = True
            record["diagnostics"].append(
                {
                    "code": "RT031",
                    "severity": "error",
                    "message": "graph execution budget exceeded",
                }
            )
        return exceeded

    def _record_diagnostic(self, node_id: str, error: RoutingError) -> None:
        """Record a node-level routing failure on the run record and the audit chain."""

        message = str(error)
        code = message.split(":", 1)[0].strip() if ":" in message else "RT011"
        self.audit.write("agraph.node_blocked", graph_node_id=node_id, reason=message)
        record = self._active_record
        if record is not None and isinstance(record.get("diagnostics"), list):
            entry = {"code": code, "severity": "error", "message": message}
            if node_id:
                entry["node_id"] = node_id
            record["diagnostics"].append(entry)

    @staticmethod
    def _retry_allowed(retry: Mapping[str, Any], failure_class: str) -> bool:
        allowed = set(retry.get("retry_on", ["transient", "tool_error", "criteria_failed"]))
        return "any" in allowed or failure_class in allowed

    def _compensate(
        self, graph: dict[str, Any], record: dict[str, Any], scope: dict[str, Any]
    ) -> None:
        compensated: list[str] = []
        for node_id in reversed(topological_order(graph)):
            node_record = record["nodes"][node_id]
            compensation = (graph["nodes"][node_id].get("failure") or {}).get("compensation")
            if node_record["status"] != "succeeded" or not compensation:
                continue
            target = str(compensation)
            outcome, outputs, details = self._run_node(target, graph["nodes"][target], graph, scope)
            record["nodes"][target].update(details)
            record["nodes"][target].update({"status": outcome, "outputs": outputs})
            scope["nodes"][target] = {"status": outcome, "outputs": outputs}
            compensated.append(node_id)
        if compensated:
            record["metadata"]["compensated_nodes"] = compensated

    def _consume_approval(
        self,
        *,
        action: str,
        target: str,
        arguments: Mapping[str, Any],
        approved: bool,
        non_interactive: bool,
    ) -> None:
        request = self.approvals.request(
            action=action,
            target=target,
            arguments=arguments,
            policy_decision="ask",
            policy_version=self.config.permissions.version,
            policy_source="agraph",
            policy_reason="Agentic Graph approvals bind reviewed arguments and identity.",
            risk_reason="Authorize a reviewed graph plan or human checkpoint.",
        )
        if not approved:
            self.approvals.deny(request)
            raise GraphExecutionError("approval denied")
        record = self.approvals.grant(
            request,
            method="non_interactive" if non_interactive else "interactive",
        )
        self.approvals.consume(request, record.approval_id)

    @staticmethod
    def _bind_params(graph: Mapping[str, Any], supplied: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(supplied) - set(graph.get("params", {}))
        if unknown:
            raise GraphExecutionError("unknown graph params: " + ", ".join(sorted(unknown)))
        values = dict(supplied)
        for name, spec in graph.get("params", {}).items():
            if name not in values and "default" in spec:
                values[name] = spec["default"]
            if name not in values and spec.get("required", True):
                raise GraphExecutionError(f"required graph param {name!r} is missing")
            if name in values and not _matches_type(values[name], str(spec["type"])):
                raise GraphExecutionError(
                    f"graph param {name!r} does not match type {spec['type']!r}"
                )
            if name in values and spec.get("schema"):
                jsonschema.validate(values[name], spec["schema"])
        return values

    @staticmethod
    def _finish(
        record: dict[str, Any],
        scope: dict[str, Any],
        states: dict[str, str],
        node_id: str,
        status: str,
        outputs: dict[str, Any],
    ) -> None:
        states[node_id] = status
        record["nodes"][node_id].update({"status": status, "outputs": outputs})
        scope["nodes"][node_id] = {"status": status, "outputs": outputs}
