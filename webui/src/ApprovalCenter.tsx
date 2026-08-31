import { useCallback, useEffect, useState } from "react";
import { request } from "./api";

type Choice = { decision: "approve" | "deny" | "cancel"; scope: "once" | "session" | "persistent"; label: string };
type Pending = {
  id: string;
  origin: Record<string, string>;
  action: { name: string; summary: string; arguments: Record<string, unknown>; resource?: string; working_directory?: string; effects?: string[] };
  action_digest: string;
  risk: { level: string; reasons: string[] };
  choices: Choice[];
};

export function ApprovalCenter({ setError }: { setError: (message: string) => void }) {
  const [pending, setPending] = useState<Pending[]>([]);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const result = await request<{ snapshot: { pending: Pending[] } }>("/api/approvals/snapshot");
      setPending(result.snapshot?.pending ?? []);
    } catch { /* run streams remain the fallback */ }
  }, []);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 800);
    return () => window.clearInterval(timer);
  }, [refresh]);
  const decide = useCallback(async (approval: Pending, choice: Choice) => {
    setBusy(true);
    try {
      await request("/api/approvals/decisions", {
        method: "POST",
        body: JSON.stringify({
          request_id: approval.id,
          decision: choice.decision,
          scope: choice.scope,
          decision_id: `dec_web_${crypto.randomUUID().replaceAll("-", "")}`,
        }),
      });
      await refresh();
    } catch (reason) {
      setError(String(reason));
      await refresh();
    } finally { setBusy(false); }
  }, [refresh, setError]);
  const approval = pending[0];
  if (!approval) return null;
  return <div className="modal-backdrop approval-backdrop"><section className="modal approval-modal" role="alertdialog" aria-modal="true" aria-label="Permission required">
    <small>AAIS PERMISSION REQUIRED · {pending.length} PENDING</small>
    <h2>{approval.action.summary}</h2>
    <div className={`approval-risk ${approval.risk.level}`}><b>{approval.risk.level} risk</b>{approval.risk.reasons.map((reason) => <span key={reason}>{reason}</span>)}</div>
    <dl className="approval-detail"><div><dt>Action</dt><dd>{approval.action.name}</dd></div>{approval.action.resource && <div><dt>Resource</dt><dd>{approval.action.resource}</dd></div>}<div><dt>Exact arguments</dt><dd><pre>{JSON.stringify(approval.action.arguments, null, 2)}</pre></dd></div><div><dt>Digest</dt><dd><code>{approval.action_digest}</code></dd></div></dl>
    <div className="approval-actions">{approval.choices.map((choice) => <button key={`${choice.decision}:${choice.scope}`} disabled={busy} className={choice.decision === "approve" ? "primary-action" : "secondary-action"} onClick={() => void decide(approval, choice)}>{choice.label}</button>)}</div>
    <small>Loro revalidates identity, policy, scope, and the exact action before execution.</small>
  </section></div>;
}
