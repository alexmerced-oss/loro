"""Per-run evidence bundles: export one agent run to a zip and verify it offline.

A run is one ``AgentRuntime.run`` call. Its id is the audit ``trace_id`` that every audit
event written during the run carries. The bundle format is documented in
``docs/run-evidence.md``; ``BUNDLE_FORMAT`` names its version.

Integrity model: ``manifest.json`` lists the SHA-256 of every other member, and
``manifest.sha256`` holds the SHA-256 of the manifest itself, which is the bundle digest that
export prints. Changing any byte of any member is detected. The audit slice is additionally
bound to the hash-chained audit log: every run event's hash is recomputed from its content,
and the chain links of the contiguous log segment the run spans (content omitted for other
runs' events) must connect. Without an external anchor (``--expect-digest`` or the original
audit log) a party able to rewrite the whole bundle consistently is not detected; the bundle
is not signed.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loro import __version__
from loro.audit.sinks import _event_hash, verify_jsonl_audit
from loro.config import LoroConfig, config_digest
from loro.data_protection import DataProtectionEngine
from loro.sandbox import SandboxRunner
from loro.sessions import SessionStore

BUNDLE_FORMAT = "loro.run-evidence.v1"
MANIFEST = "manifest.json"
MANIFEST_DIGEST = "manifest.sha256"
MEMBERS = (
    "run.json",
    "audit-slice.jsonl",
    "audit-chain.json",
    "approvals.json",
    "tool-calls.json",
    "digests.json",
    "sandbox.json",
)
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class EvidenceError(ValueError):
    """A user-correctable problem exporting or reading a bundle."""


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    started_at: str | None
    completed_at: str | None
    session_id: str | None
    mode: str | None
    provider: str | None
    model: str | None
    stop_reason: str | None
    events: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "session_id": self.session_id,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "events": self.events,
        }


@dataclass(frozen=True)
class ExportResult:
    path: Path
    run_id: str
    digest: str
    audit_events: int
    tool_calls: int
    approvals: int
    warnings: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "run_id": self.run_id,
            "bundle_digest": self.digest,
            "audit_events": self.audit_events,
            "tool_calls": self.tool_calls,
            "approvals": self.approvals,
            "warnings": self.warnings,
        }


@dataclass
class VerifyResult:
    path: Path
    run_id: str | None = None
    digest: str | None = None
    checks: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "run_id": self.run_id,
            "bundle_digest": self.digest,
            "checks": self.checks,
            "issues": self.issues,
        }


# ---------------------------------------------------------------------------------------------
# Reading the audit log


def _audit_path(config: LoroConfig) -> Path:
    if config.audit.sink != "jsonl":
        raise EvidenceError(
            "Run evidence export reads the local JSONL audit log, but audit.sink is "
            f"{config.audit.sink!r}. Export from the collector instead, or set audit.sink = "
            '"jsonl".'
        )
    path = Path(config.audit.path).expanduser()
    if not path.exists():
        raise EvidenceError(
            f"No audit log at {path}. Runs are only exportable when audit is enabled; check "
            "`loro audit doctor`."
        )
    return path


def _audit_lines(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("rb") as handle:
        for number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise EvidenceError(
                    f"Audit log line {number} is not valid JSON; run `loro audit verify`."
                ) from error
            if isinstance(payload, dict):
                yield number, payload


def list_runs(config: LoroConfig, *, limit: int = 20) -> list[RunSummary]:
    """Most recent runs first, from ``runtime.task_started`` events in the audit log."""

    runs: dict[str, dict[str, Any]] = {}
    for _number, event in _audit_lines(_audit_path(config)):
        trace = event.get("trace_id")
        event_type = event.get("event_type")
        if not isinstance(trace, str):
            continue
        if event_type == "runtime.task_started":
            details = event.get("details") or {}
            runs[trace] = {
                "started_at": event.get("timestamp"),
                "mode": details.get("mode"),
                "provider": details.get("model_provider"),
                "model": details.get("model"),
                "events": 0,
            }
        entry = runs.get(trace)
        if entry is None:
            continue
        entry["events"] += 1
        if event_type == "runtime.task_completed":
            details = event.get("details") or {}
            entry["completed_at"] = event.get("timestamp")
            entry["session_id"] = details.get("session_id")
            entry["stop_reason"] = details.get("stop_reason")
    ordered = sorted(runs.items(), key=lambda item: str(item[1]["started_at"]), reverse=True)
    return [
        RunSummary(
            run_id=run_id,
            started_at=entry.get("started_at"),
            completed_at=entry.get("completed_at"),
            session_id=entry.get("session_id"),
            mode=entry.get("mode"),
            provider=entry.get("provider"),
            model=entry.get("model"),
            stop_reason=entry.get("stop_reason"),
            events=entry["events"],
        )
        for run_id, entry in ordered[:limit]
    ]


def _chain_segment(
    path: Path, run_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return (run events, chain links of the spanned segment, segment facts)."""

    run_events: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    first_line: int | None = None
    last_line: int | None = None
    for number, event in _audit_lines(path):
        integrity = event.get("integrity") if isinstance(event.get("integrity"), dict) else None
        in_run = event.get("trace_id") == run_id
        if in_run and integrity is None:
            raise EvidenceError(
                f"Run {run_id} has audit events written before hash chaining was enabled; "
                "they cannot be exported as verifiable evidence."
            )
        if first_line is None and not in_run:
            continue
        link = {
            "line": number,
            "event_hash": integrity.get("event_hash") if integrity else None,
            "previous_hash": integrity.get("previous_hash") if integrity else None,
            "run_event": in_run,
        }
        if first_line is None:
            first_line = number
        pending.append(link)
        if in_run:
            run_events.append(event)
            links.extend(pending)
            pending = []
            last_line = number
    if not run_events:
        raise EvidenceError(
            f"No audit events carry run id {run_id}. List exportable runs with `loro run list`."
        )
    facts = {
        "first_line": first_line,
        "last_line": last_line,
        "start_previous_hash": links[0]["previous_hash"],
        "end_hash": links[-1]["event_hash"],
    }
    return run_events, links, facts


# ---------------------------------------------------------------------------------------------
# Export


def _details(events: Iterable[dict[str, Any]], event_type: str) -> dict[str, Any]:
    for event in events:
        if event.get("event_type") == event_type:
            details = event.get("details")
            return details if isinstance(details, dict) else {}
    return {}


def _canonical(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _redact(protection: DataProtectionEngine, value: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Apply the audit surface's classification policy to every string in ``value``."""

    decisions: list[dict[str, Any]] = []

    def visit(item: Any) -> Any:
        if isinstance(item, str):
            try:
                decision = protection.enforce(item, "audit")
            except ValueError:
                decisions.append({"action": "withheld", "surface": "audit"})
                return "[withheld by data-classification policy]"
            if decision.findings or decision.redacted:
                decisions.append(decision.metadata())
            return decision.content
        if isinstance(item, dict):
            return {key: visit(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(nested) for nested in item]
        return item

    return visit(value), decisions


def export_run(
    config: LoroConfig,
    run_id: str,
    out: Path,
    *,
    project_root: Path | None = None,
    overwrite: bool = False,
) -> ExportResult:
    """Write the evidence bundle for ``run_id`` to ``out`` and return its digest."""

    out = out.expanduser()
    if out.exists() and not overwrite:
        raise EvidenceError(f"{out} already exists. Choose another --out path or pass --force.")
    audit_path = _audit_path(config)
    run_events, links, facts = _chain_segment(audit_path, run_id)
    warnings: list[str] = []
    protection = DataProtectionEngine(config.safety)

    started = _details(run_events, "runtime.task_started")
    completed = _details(run_events, "runtime.task_completed")
    if not completed:
        warnings.append("The run has no runtime.task_completed event; it may still be running.")
    session_id = completed.get("session_id")

    # Tool calls and results: the session record holds full results for its latest run only.
    session_record: dict[str, Any] | None = None
    if isinstance(session_id, str):
        try:
            candidate = SessionStore(config.sessions).get(session_id)
        except (FileNotFoundError, ValueError):
            candidate = None
        if candidate is not None and candidate.get("run_id") == run_id:
            session_record = candidate
        else:
            warnings.append(
                "Tool arguments and results are unavailable: the session record has advanced to "
                "a later run or was removed. Tool metadata from the audit slice is included."
            )
    tool_events = [
        event["details"]
        for event in run_events
        if event.get("event_type") == "runtime.tool_executed"
        and isinstance(event.get("details"), dict)
    ]
    tool_results: list[dict[str, Any]] = []
    redactions: list[dict[str, Any]] = []
    if session_record is not None:
        for execution in session_record.get("tool_executions") or []:
            protected, decisions = _redact(protection, execution)
            tool_results.append(protected)
            redactions.extend(decisions)
    tool_calls = {
        "source": "session-record" if session_record is not None else "audit-metadata-only",
        "redaction": {
            "surface": "audit",
            "policy": "Strings pass the audit data-protection surface; content above its "
            "maximum classification is redacted or withheld.",
            "decisions": redactions,
        },
        "audit": tool_events,
        "results": tool_results,
    }

    approval_events = [
        event for event in run_events if str(event.get("event_type", "")).startswith("approval.")
    ]
    request_ids = sorted(
        {
            str(event["details"]["request_id"])
            for event in approval_events
            if isinstance(event.get("details"), dict) and event["details"].get("request_id")
        }
    )
    receipts: dict[str, Any] = {}
    if request_ids:
        from loro.aais_bridge import AAISBridge

        try:
            receipts = AAISBridge(project_root or Path.cwd()).receipts(request_ids)
        except Exception as error:  # noqa: BLE001 - evidence export reports, never aborts
            warnings.append(f"AAIS receipts could not be read: {error}")
    approvals = {
        "request_ids": request_ids,
        "audit_events": [event.get("event_type") for event in approval_events],
        "aais_receipts": receipts,
        "note": (
            "AAIS receipts exist for approvals resolved through the AAIS authority (Web UI or "
            "--approval-stdio). Terminal prompts are recorded in the audit slice only."
        ),
    }

    sandbox_calls = [
        {
            "tool": details.get("tool"),
            "sandbox_profile": details.get("sandbox_profile"),
            "sandbox_os_enforced": details.get("sandbox_os_enforced"),
        }
        for details in tool_events
        if "sandbox_profile" in details or "sandbox_os_enforced" in details
    ]
    sandbox = {
        "tool_calls": sandbox_calls,
        "any_process_only": any(item["sandbox_os_enforced"] is False for item in sandbox_calls),
        "diagnosis_at_export": SandboxRunner(config.sandbox).diagnose(),
        "note": "Per-call status comes from the run's audit events; the diagnosis describes "
        "this machine at export time, not necessarily at run time.",
    }

    profile_loaded = _details(run_events, "agent_profile.loaded")
    current_digest = config_digest(config)
    run_digest = started.get("config_digest")
    digests = {
        "config_digest_at_run": run_digest,
        "config_digest_at_export": current_digest,
        "config_changed_since_run": bool(run_digest) and run_digest != current_digest,
        "agent_profile": (
            {
                "name": profile_loaded.get("name"),
                "revision": profile_loaded.get("revision"),
                "spec_digest": profile_loaded.get("spec_digest"),
                "trust": profile_loaded.get("trust"),
            }
            if profile_loaded
            else None
        ),
    }
    if not run_digest:
        warnings.append("The run predates config digests in audit events (Loro < 0.22).")

    run = {
        "run_id": run_id,
        "session_id": session_id,
        "mode": started.get("mode"),
        "started_at": next(
            (
                e.get("timestamp")
                for e in run_events
                if e.get("event_type") == "runtime.task_started"
            ),
            run_events[0].get("timestamp"),
        ),
        "completed_at": next(
            (
                e.get("timestamp")
                for e in run_events
                if e.get("event_type") == "runtime.task_completed"
            ),
            None,
        ),
        "provider": started.get("model_provider"),
        "model": started.get("model"),
        "stop_reason": completed.get("stop_reason"),
        "steps": completed.get("steps"),
        "usage": completed.get("usage"),
        "latency_ms": completed.get("latency_ms"),
        "actor": run_events[0].get("actor"),
        "tenant_id": run_events[0].get("tenant_id"),
    }

    chain = {
        "algorithm": "sha256",
        "audit_log": str(audit_path),
        **facts,
        "links": links,
        "verified_at_export": _verify_log(audit_path),
    }
    members = {
        "run.json": _canonical(run),
        "audit-slice.jsonl": b"".join(
            (json.dumps(event, sort_keys=True, default=str) + "\n").encode("utf-8")
            for event in run_events
        ),
        "audit-chain.json": _canonical(chain),
        "approvals.json": _canonical(approvals),
        "tool-calls.json": _canonical(tool_calls),
        "digests.json": _canonical(digests),
        "sandbox.json": _canonical(sandbox),
    }
    manifest = {
        "format": BUNDLE_FORMAT,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "loro_version": __version__,
        "files": {
            name: {"sha256": _sha256(content), "bytes": len(content)}
            for name, content in members.items()
        },
        "warnings": warnings,
    }
    manifest_bytes = _canonical(manifest)
    digest = _sha256(manifest_bytes)
    members[MANIFEST] = manifest_bytes
    members[MANIFEST_DIGEST] = (digest + "\n").encode("utf-8")

    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(f".{out.name}.partial")
    temporary.write_bytes(_archive(members))
    temporary.replace(out)
    return ExportResult(
        path=out,
        run_id=run_id,
        digest=digest,
        audit_events=len(run_events),
        tool_calls=len(tool_events),
        approvals=len(request_ids),
        warnings=warnings,
    )


def _archive(members: dict[str, bytes]) -> bytes:
    """The canonical v1 layout: stored (uncompressed) members in a fixed order with fixed
    header fields, so the same members always produce the same bytes. Verify rebuilds this
    layout and compares, which catches changes to zip headers that no digest covers."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in (MANIFEST, MANIFEST_DIGEST, *MEMBERS):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, members[name])
    return buffer.getvalue()


def _verify_log(path: Path) -> dict[str, Any]:
    result = verify_jsonl_audit(path)
    return {
        "ok": result.ok,
        "events": result.events,
        "final_hash": result.final_hash,
        "issue": result.issue,
    }


# ---------------------------------------------------------------------------------------------
# Verify


def _read_members(path: Path, result: VerifyResult) -> dict[str, bytes] | None:
    try:
        if path.stat().st_size > MAX_BUNDLE_BYTES:
            result.issues.append(f"The bundle exceeds {MAX_BUNDLE_BYTES} bytes.")
            return None
        with zipfile.ZipFile(path) as archive:
            members: dict[str, bytes] = {}
            infos = archive.infolist()
            # Check the directory before reading anything: a hostile archive with many or huge
            # members must not be decompressed into memory.
            if len(infos) > len(MEMBERS) + 2:
                result.issues.append(f"The archive has {len(infos)} members; v1 has 9.")
                return None
            if sum(info.file_size for info in infos) > MAX_BUNDLE_BYTES:
                result.issues.append("The archive's members are larger than any v1 bundle.")
                return None
            for info in infos:
                if info.file_size > MAX_MEMBER_BYTES:
                    result.issues.append(f"{info.filename} exceeds {MAX_MEMBER_BYTES} bytes.")
                    return None
                if info.filename in members:
                    result.issues.append(f"Duplicate member {info.filename}.")
                    return None
                members[info.filename] = archive.read(info)
            return members
    except FileNotFoundError:
        raise EvidenceError(f"No bundle at {path}.") from None
    except Exception as error:  # noqa: BLE001 - any unreadable archive is a verification failure
        result.issues.append(f"The archive is damaged or altered ({type(error).__name__}).")
        return None


def verify_bundle(
    path: Path,
    *,
    expected_digest: str | None = None,
    audit_log: Path | None = None,
) -> VerifyResult:
    """Check a bundle's integrity; return every problem found rather than the first."""

    path = path.expanduser()
    result = VerifyResult(path=path)
    members = _read_members(path, result)
    if members is None:
        return result

    expected_names = {MANIFEST, MANIFEST_DIGEST, *MEMBERS}
    missing = sorted(expected_names - set(members))
    extra = sorted(set(members) - expected_names)
    if missing:
        result.issues.append("Missing members: " + ", ".join(missing))
    if extra:
        result.issues.append("Unexpected members: " + ", ".join(extra))
    if MANIFEST not in members or MANIFEST_DIGEST not in members:
        return result
    result.checks.append("canonical archive layout")
    if not missing and not extra and _archive(members) != path.read_bytes():
        result.issues.append(
            "The archive bytes differ from the canonical layout (headers or order were altered)."
        )

    digest = _sha256(members[MANIFEST])
    result.digest = digest
    recorded = members[MANIFEST_DIGEST].decode("utf-8", errors="replace").strip()
    result.checks.append("manifest digest")
    if recorded != digest:
        result.issues.append("manifest.json does not match manifest.sha256.")
    if expected_digest is not None:
        result.checks.append("expected bundle digest")
        if expected_digest.strip() != digest:
            result.issues.append(
                f"Bundle digest {digest} does not match the expected {expected_digest.strip()}."
            )
    try:
        manifest = json.loads(members[MANIFEST])
    except (json.JSONDecodeError, UnicodeDecodeError):
        result.issues.append("manifest.json is not valid JSON.")
        return result
    if manifest.get("format") != BUNDLE_FORMAT:
        result.issues.append(f"Unsupported bundle format {manifest.get('format')!r}.")
        return result
    result.run_id = str(manifest.get("run_id") or "") or None

    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    result.checks.append("member digests")
    for name in MEMBERS:
        entry = files.get(name)
        content = members.get(name)
        if content is None:
            continue
        if not isinstance(entry, dict) or entry.get("sha256") != _sha256(content):
            result.issues.append(f"{name} does not match its manifest digest.")
    for name in sorted(set(files) - set(MEMBERS)):
        result.issues.append(f"Manifest lists an unknown member {name}.")

    _verify_chain(members, result)
    _verify_consistency(members, result)
    if audit_log is not None:
        _verify_against_log(members, audit_log.expanduser(), result)
    return result


def _json_member(members: dict[str, bytes], name: str, result: VerifyResult) -> Any:
    try:
        return json.loads(members[name])
    except KeyError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        result.issues.append(f"{name} is not valid JSON.")
        return None


def _slice_events(members: dict[str, bytes], result: VerifyResult) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    raw = members.get("audit-slice.jsonl", b"")
    for number, line in enumerate(raw.splitlines(), 1):
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            result.issues.append(f"audit-slice.jsonl line {number} is not valid JSON.")
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _verify_chain(members: dict[str, bytes], result: VerifyResult) -> None:
    chain = _json_member(members, "audit-chain.json", result)
    events = _slice_events(members, result)
    if not isinstance(chain, dict) or not events:
        result.issues.append("The audit slice or its chain record is empty.")
        return
    result.checks.append("audit event hashes")
    for index, event in enumerate(events, 1):
        raw_integrity = event.get("integrity")
        integrity: dict[str, Any] = raw_integrity if isinstance(raw_integrity, dict) else {}
        expected = _event_hash(
            event, integrity.get("previous_hash"), integrity.get("legacy_prefix_hash")
        )
        if integrity.get("event_hash") != expected:
            result.issues.append(f"Audit slice event {index} does not match its hash.")
        if event.get("trace_id") != result.run_id:
            result.issues.append(f"Audit slice event {index} belongs to another run.")

    result.checks.append("audit chain links")
    raw_links = chain.get("links")
    links: list[Any] = raw_links if isinstance(raw_links, list) else []
    previous: str | None = None
    for position, link in enumerate(links):
        if not isinstance(link, dict):
            result.issues.append("audit-chain.json has a malformed link.")
            return
        if position and link.get("previous_hash") != previous:
            result.issues.append(f"Audit chain breaks at log line {link.get('line')}.")
        previous = link.get("event_hash")
    run_links = [link for link in links if isinstance(link, dict) and link.get("run_event")]
    if [link.get("event_hash") for link in run_links] != [
        (event.get("integrity") or {}).get("event_hash") for event in events
    ]:
        result.issues.append("The audit slice does not match the run events in the chain.")
    if links and (
        chain.get("start_previous_hash") != links[0].get("previous_hash")
        or chain.get("end_hash") != links[-1].get("event_hash")
    ):
        result.issues.append("audit-chain.json segment bounds do not match its links.")


def _verify_consistency(members: dict[str, bytes], result: VerifyResult) -> None:
    run = _json_member(members, "run.json", result)
    if not isinstance(run, dict):
        return
    result.checks.append("run record consistency")
    if run.get("run_id") != result.run_id:
        result.issues.append("run.json names a different run than the manifest.")
    completed = next(
        (
            event.get("details") or {}
            for event in _slice_events(members, VerifyResult(path=result.path))
            if event.get("event_type") == "runtime.task_completed"
        ),
        None,
    )
    if completed is not None and completed.get("usage") != run.get("usage"):
        result.issues.append("run.json usage differs from the audited task_completed event.")


def _verify_against_log(members: dict[str, bytes], audit_log: Path, result: VerifyResult) -> None:
    result.checks.append("original audit log")
    chain = _json_member(members, "audit-chain.json", VerifyResult(path=result.path))
    if not isinstance(chain, dict):
        return
    if not audit_log.exists():
        result.issues.append(f"No audit log at {audit_log}.")
        return
    log = verify_jsonl_audit(audit_log)
    if not log.ok:
        result.issues.append(f"The audit log itself fails verification: {log.issue}")
    hashes = {
        link.get("line"): link.get("event_hash")
        for link in chain.get("links", [])
        if isinstance(link, dict)
    }
    seen = {
        number: (event.get("integrity") or {}).get("event_hash")
        for number, event in _audit_lines(audit_log)
        if number in hashes
    }
    if seen != hashes:
        result.issues.append("The bundle's chain segment is not present in this audit log.")
