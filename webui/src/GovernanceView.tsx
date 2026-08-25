import { useCallback, useEffect, useState } from "react";
import { request } from "./api";

/**
 * Governance.
 *
 * Loro's reason to exist is evidence: who ran this, under whose identity, with
 * whose approval, against which policy, and can you prove the record has not
 * been edited. All of it lived in the CLI, so a screenshot of the Web UI could
 * have been any chat app.
 *
 * Everything on this screen is read-only. Verification recomputes the chain,
 * policy explanation evaluates a hypothetical request without performing it.
 */

type Status = {
  identity?: {
    ok?: boolean;
    actor?: string;
    tenant_id?: string;
    roles?: string[];
    organization?: string;
    auth_method?: string;
    issues?: string[];
  };
  budgets?: Record<string, number | null>;
  sandbox?: { profile?: string; enforced?: boolean; max_runtime_seconds?: number | null; max_output_bytes?: number | null };
  approvals?: { mode?: string; allow_session_scope?: boolean };
  audit?: { sink?: string; path?: string };
};

type AuditEvent = Record<string, unknown>;

type AuditPayload = {
  ok?: boolean;
  sink?: string;
  path?: string;
  note?: string;
  error?: string;
  events?: AuditEvent[];
  verification?: Record<string, unknown> | null;
  total?: number;
  event_types?: Record<string, number>;
};

type Explanation = {
  decision?: string;
  reason?: string;
  policy_version?: string;
  policy_source?: string;
  matched_rule?: number | null;
  normalized_resource?: Record<string, unknown> | null;
};

const SAMPLE = JSON.stringify(
  { tool: "shell", action: "run command", resource: { kind: "shell", executable_name: "python" } },
  null,
  2,
);

function Facts({ title, rows }: { title: string; rows: [string, unknown][] }) {
  return (
    <section className="gov-card">
      <small>{title}</small>
      <dl>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {value === null || value === undefined || value === ""
                ? "—"
                : typeof value === "boolean"
                  ? value ? "yes" : "no"
                  : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function GovernanceView({ setError }: { setError: (message: string) => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [audit, setAudit] = useState<AuditPayload | null>(null);
  const [verification, setVerification] = useState<Record<string, unknown> | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [probe, setProbe] = useState(SAMPLE);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    try {
      const [state, events] = await Promise.all([
        request<Status>("/api/governance/status"),
        request<AuditPayload>(`/api/governance/audit?limit=100${filter ? `&event_type=${encodeURIComponent(filter)}` : ""}`),
      ]);
      setStatus(state);
      setAudit(events);
    } catch (problem) {
      setError((problem as Error).message);
    }
  }, [filter, setError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function verify() {
    setVerifying(true);
    try {
      setVerification(await request("/api/governance/verify", { method: "POST", body: JSON.stringify({ anchor: "" }) }));
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setVerifying(false);
    }
  }

  async function explain() {
    try {
      const parsed = JSON.parse(probe);
      setExplanation(await request("/api/governance/explain", { method: "POST", body: JSON.stringify({ request: parsed }) }));
    } catch (problem) {
      setExplanation(null);
      setError(
        problem instanceof SyntaxError
          ? "That is not valid JSON. Try the sample shape shown in the box."
          : (problem as Error).message,
      );
    }
  }

  const chainOk = verification?.ok ?? audit?.verification?.ok;

  return (
    <div className="page gov-page">
      <header className="page-header">
        <div>
          <small>EVIDENCE</small>
          <h1>Governance</h1>
          <p>Who you are resolved as, what policy would decide, and whether the audit record is intact.</p>
        </div>
        <button className="secondary-action" type="button" onClick={() => void load()}>Refresh</button>
      </header>

      <div className="gov-grid">
        <Facts
          title="IDENTITY"
          rows={[
            ["Actor", status?.identity?.actor],
            ["Tenant", status?.identity?.tenant_id],
            ["Roles", (status?.identity?.roles || []).join(", ")],
            ["Signed in via", status?.identity?.auth_method],
            ["Resolved", status?.identity?.ok],
          ]}
        />
        <Facts
          title="BUDGETS"
          rows={Object.entries(status?.budgets || {}).map(([key, value]) => [
            key.replace(/^max_/, "").replace(/_/g, " "),
            value,
          ])}
        />
        <Facts
          title="SANDBOX"
          rows={[
            ["Profile", status?.sandbox?.profile],
            ["Bubblewrap enforced", status?.sandbox?.enforced],
            ["Max runtime (s)", status?.sandbox?.max_runtime_seconds],
            ["Max output (bytes)", status?.sandbox?.max_output_bytes],
          ]}
        />
        <Facts
          title="APPROVALS & AUDIT"
          rows={[
            ["Approval mode", status?.approvals?.mode],
            ["Session scope allowed", status?.approvals?.allow_session_scope],
            ["Audit sink", status?.audit?.sink],
            ["Audit path", status?.audit?.path],
          ]}
        />
      </div>

      {status?.identity?.issues?.length ? (
        <div className="gov-issues" role="alert">
          <b>Identity is not fully resolved</b>
          <ul>{status.identity.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        </div>
      ) : null}

      <section className="gov-section">
        <div className="gov-section-head">
          <div>
            <small>POLICY</small>
            <h2>Explain a decision</h2>
            <p>Evaluates the rules against a hypothetical request. Nothing is executed.</p>
          </div>
          <button className="primary-action" type="button" onClick={() => void explain()}>Explain</button>
        </div>
        <label className="sr-only" htmlFor="policyProbe">Permission request</label>
        <textarea id="policyProbe" rows={6} value={probe} onChange={(event) => setProbe(event.target.value)} spellCheck={false} />
        {explanation && (
          <div className={`gov-decision decision-${String(explanation.decision || "").toLowerCase()}`}>
            <b>{String(explanation.decision || "—").toUpperCase()}</b>
            <p>{explanation.reason}</p>
            <small>
              policy {explanation.policy_version || "—"} from {explanation.policy_source || "—"}
              {explanation.matched_rule !== null && explanation.matched_rule !== undefined
                ? ` · matched rule ${explanation.matched_rule}`
                : " · no rule matched"}
            </small>
          </div>
        )}
      </section>

      <section className="gov-section">
        <div className="gov-section-head">
          <div>
            <small>AUDIT</small>
            <h2>The record</h2>
            <p>{audit?.path || "No audit sink configured."}</p>
          </div>
          <button className="primary-action" type="button" onClick={() => void verify()} disabled={verifying}>
            {verifying ? "Verifying…" : "Verify chain"}
          </button>
        </div>

        {audit?.error && <div className="gov-issues" role="alert">{audit.error}</div>}
        {audit?.note && <p className="gov-note">{audit.note}</p>}

        {chainOk !== undefined && (
          <div className={`gov-chain ${chainOk ? "ok" : "broken"}`} role="status">
            <b>{chainOk ? "Chain intact" : "Chain verification failed"}</b>
            <span>
              {chainOk
                ? "Every event hashes onto its predecessor."
                : "The record does not hash cleanly; treat it as tampered until explained."}
            </span>
          </div>
        )}

        {audit?.event_types && Object.keys(audit.event_types).length > 0 && (
          <div className="gov-filters">
            <button className={`chip ${!filter ? "active" : ""}`} type="button" onClick={() => setFilter("")}>
              all
            </button>
            {Object.entries(audit.event_types).slice(0, 12).map(([type, count]) => (
              <button key={type} type="button" className={`chip ${filter === type ? "active" : ""}`} onClick={() => setFilter(type)}>
                {type} <i>{count}</i>
              </button>
            ))}
          </div>
        )}

        {audit?.events?.length ? (
          <div className="gov-events">
            {audit.events.map((event, index) => (
              <article key={index}>
                <div className="gov-event-top">
                  <b>{String(event.event_type ?? "event")}</b>
                  <time>{String(event.timestamp ?? event.created_at ?? "")}</time>
                </div>
                <div className="gov-event-facts">
                  {["actor", "tenant_id", "decision", "tool", "target"].map((key) =>
                    event[key] ? <span key={key}>{key}: {String(event[key])}</span> : null,
                  )}
                </div>
              </article>
            ))}
          </div>
        ) : (
          !audit?.note && !audit?.error && <p className="gov-note">No events match this filter.</p>
        )}
      </section>
    </div>
  );
}
