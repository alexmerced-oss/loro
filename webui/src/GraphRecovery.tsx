import { useState } from "react";
import { request } from "./api";

type SavedRun = { run_id: string; status: string; graph_id: string };
type Recovery = {
  completed_nodes: string[];
  pending_gates: string[];
  uncertain_nodes: string[];
  graph_changed: boolean | null;
  source_error?: string;
};

export function GraphRecovery({ setError }: { setError: (message: string) => void }) {
  const [runs, setRuns] = useState<SavedRun[] | null>(null);
  const [report, setReport] = useState<Recovery | null>(null);
  async function inspect(id: string) {
    setReport(null);
    if (!id) return;
    try { setReport(await request<Recovery>(`/api/graphs/runs/${encodeURIComponent(id)}/recovery`)); }
    catch (error) { setError(String(error)); }
  }
  return <section className="graph-author">
    <button className="secondary-action" type="button" onClick={() => {
      void request<SavedRun[]>("/api/graphs/runs").then(setRuns).catch((error) => setError(String(error)));
    }}>Inspect saved runs before recovery</button>
    {runs && <label>Saved run <select defaultValue="" onChange={(event) => void inspect(event.target.value)}>
      <option value="">{runs.length ? "Choose a run" : "No saved runs"}</option>
      {runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.graph_id} · {run.status} · {run.run_id}</option>)}
    </select></label>}
    {report && <div role="status">
      <p>Completed: {report.completed_nodes.join(", ") || "none"}</p>
      <p>Waiting for a gate: {report.pending_gates.join(", ") || "none"}</p>
      <p>Uncertain effects: {report.uncertain_nodes.join(", ") || "none recorded"}</p>
      <p>Graph: {report.graph_changed === null ? "could not verify" : report.graph_changed ? "changed since execution" : "digest matches"}</p>
      {report.source_error && <p>{report.source_error}</p>}
      <p>Review current profiles and uncertain effects before explicit resume. Inspection does not restart work.</p>
    </div>}
  </section>;
}
