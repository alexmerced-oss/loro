import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "./api";

/**
 * Agentic Graphs.
 *
 * Loro is an AGS level 3 harness whose graph runtime was unreachable from its
 * own UI. This view loads a graph, shows the validated plan as a dependency
 * board, runs it through the same governed executor the CLI drives, and holds
 * human gates for a decision instead of auto-approving them.
 */

type GraphFile = { path: string; name: string; size_bytes: number };

type Finding = { severity?: string; message?: string; code?: string; pointer?: string };

type GraphNode = {
  id: string;
  title: string;
  type: string;
  description: string;
  depends_on: string[];
  profile: string;
  tier: string;
  state: string;
};

type Plan = {
  ok: boolean;
  path: string;
  graph_id: string;
  title: string;
  objective: string;
  digest: string;
  nodes: GraphNode[];
  gates: string[];
  node_count: number;
  max_parallel: number;
  worst_case_executions: number;
  estimated_cost_usd: number | null;
  findings: Finding[];
};

type Gate = { request_id: string; prompt: string; roles: string[] };

/**
 * Every card starts pending, moves to in progress while the executor works it,
 * and lands in complete when it finishes, whichever way it finished.
 */
const LANES: { id: string; title: string; note: string; states: string[] }[] = [
  { id: "pending", title: "Pending", note: "Not started yet", states: ["pending", "blocked", "ready", ""] },
  { id: "progress", title: "In progress", note: "Being worked right now", states: ["running", "active"] },
  { id: "complete", title: "Complete", note: "Finished, with the outcome", states: ["succeeded", "failed", "skipped", "cancelled"] },
];

function laneFor(node: GraphNode): string {
  const state = (node.state || "").toLowerCase();
  return LANES.find((lane) => lane.states.includes(state))?.id ?? "pending";
}

function Card({ node }: { node: GraphNode }) {
  return (
    <article className="graph-card">
      <div className="graph-card-top">
        <span className="chip">{(node.type || "task").toUpperCase()}</span>
        <span className={`chip state-${(node.state || "pending").toLowerCase()}`}>
          {(node.state || "pending").toUpperCase()}
        </span>
      </div>
      <b>{node.title}</b>
      <small>{node.id}</small>
      {node.description && <p>{node.description}</p>}
      <div className="graph-card-foot">
        <span>
          {node.depends_on.length ? `Depends on ${node.depends_on.join(", ")}` : "Entry card"}
        </span>
        {node.tier && <span>tier {node.tier}</span>}
        {node.profile && <span>@{node.profile}</span>}
      </div>
    </article>
  );
}

// Bounded: a server that has actually gone away should say so rather than
// leave the board reconnecting forever.
const MAX_RECONNECTS = 5;
const RECONNECT_DELAY_MS = 1200;

export function GraphsView({ setError }: { setError: (message: string) => void }) {
  const [files, setFiles] = useState<GraphFile[]>([]);
  const [path, setPath] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  // The last event sequence this browser actually saw, so a reconnect asks for
  // what it missed rather than replaying the whole run or nothing at all.
  const cursor = useRef(-1);
  const retries = useRef(0);
  const [status, setStatus] = useState("Ready");
  const [gate, setGate] = useState<Gate | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [draftPath, setDraftPath] = useState("");
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState("");
  const stream = useRef<EventSource | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const found = await request<GraphFile[]>("/api/graphs");
        setFiles(found);
        if (found[0]) setPath(found[0].path);
      } catch (problem) {
        setError((problem as Error).message);
      }
    })();
    return () => stream.current?.close();
  }, [setError]);

  const loadPlan = useCallback(
    async (target: string) => {
      if (!target) {
        setPlan(null);
        setNodes([]);
        return;
      }
      try {
        const data = await request<Plan>(`/api/graphs/plan?path=${encodeURIComponent(target)}`);
        setPlan(data);
        setNodes(data.nodes);
        setStatus("Ready");
        setLog([]);
      } catch (problem) {
        setPlan(null);
        setNodes([]);
        setError((problem as Error).message);
      }
    },
    [setError],
  );

  useEffect(() => {
    // An unsaved draft owns the board. Without this guard, clearing `path`
    // when a draft is adopted re-runs loadPlan("") and wipes the draft's cards.
    if (draft) return;
    void loadPlan(path);
  }, [path, loadPlan, draft]);

  /**
   * Subscribe to a run's event stream from a cursor.
   *
   * Split out of `start` because starting a run is not the only way to end up
   * watching one: a browser that reloads mid-run, or whose connection drops,
   * has to pick the same stream back up from where it stopped.
   */
  const attach = useCallback(
    (id: string, after: number) => {
      const note = (line: string) => setLog((entries) => [...entries.slice(-80), line]);
      const source = new EventSource(`/api/graphs/runs/${id}/events?after=${after}`);
      stream.current = source;

      // Every event carries its sequence as the SSE id, so a reconnect can ask
      // for exactly what it has not seen. Not tracking it is what made the
      // cursor decorative: `after` was hardcoded and nothing ever resumed.
      const track = (event: Event) => {
        const seq = Number((event as MessageEvent).lastEventId);
        if (Number.isFinite(seq)) cursor.current = seq;
      };

      source.addEventListener("gate.requested", (event) => {
        track(event);
        const data = JSON.parse((event as MessageEvent).data);
        setGate({ request_id: data.request_id, prompt: data.prompt, roles: data.roles || [] });
        setStatus("Waiting on a gate");
      });
      source.addEventListener("gate.resolved", (event) => {
        track(event);
        setGate(null);
        setStatus("Running");
      });
      source.addEventListener("node.started", (event) => {
        track(event);
        const data = JSON.parse((event as MessageEvent).data);
        setNodes((current) =>
          current.map((node) => (node.id === data.node_id ? { ...node, state: "running" } : node)),
        );
        note(`▶ ${data.node_id}`);
      });
      source.addEventListener("node.finished", (event) => {
        track(event);
        const data = JSON.parse((event as MessageEvent).data);
        setNodes((current) =>
          current.map((node) =>
            node.id === data.node_id ? { ...node, state: data.status || "succeeded" } : node,
          ),
        );
        note(`✓ ${data.node_id} ${data.status || ""}`);
      });
      source.addEventListener("run.finished", (event) => {
        track(event);
        const data = JSON.parse((event as MessageEvent).data);
        setStatus(data.status || "finished");
        note(`Run ${data.status || "finished"}`);
      });
      source.addEventListener("run.failed", (event) => {
        track(event);
        const data = JSON.parse((event as MessageEvent).data);
        setStatus("failed");
        setError(data.error || "The graph run failed.");
      });
      source.addEventListener("run.closed", (event) => {
        track(event);
        source.close();
        stream.current = null;
        retries.current = 0;
        setRunId(null);
        setGate(null);
      });
      source.onerror = () => {
        source.close();
        stream.current = null;
        // A dropped connection is not a dead run. Resume from the cursor
        // rather than abandoning it, which is what used to happen: the run
        // carried on server-side while the board went blank.
        if (retries.current >= MAX_RECONNECTS) {
          retries.current = 0;
          setRunId(null);
          setStatus("disconnected");
          setError(
            "Lost the connection to this run. It may still be going; reload to pick it back up.",
          );
          return;
        }
        retries.current += 1;
        note(`Reconnecting (${retries.current}/${MAX_RECONNECTS})…`);
        window.setTimeout(() => attach(id, cursor.current), RECONNECT_DELAY_MS);
      };
    },
    [setError],
  );

  const start = useCallback(
    async (dryRun: boolean) => {
      if (!path || runId) return;
      try {
        const started = await request<{ run_id: string }>("/api/graphs/runs", {
          method: "POST",
          body: JSON.stringify({ path, dry_run: dryRun }),
        });
        setRunId(started.run_id);
        setStatus(dryRun ? "Dry run" : "Running");
        setLog([]);
        cursor.current = -1;
        retries.current = 0;
        attach(started.run_id, -1);
      } catch (problem) {
        setError((problem as Error).message);
      }
    },
    [path, runId, setError, attach],
  );

  // Reattach to a run that was still going when this view last went away.
  useEffect(() => {
    if (stream.current) return;
    let abandoned = false;
    (async () => {
      try {
        const live = await request<{ run_id: string; path: string; status: string }[]>(
          "/api/graphs/runs/active",
        );
        const running = live.find((item) => item.status === "running");
        if (abandoned || !running || stream.current) return;
        setRunId(running.run_id);
        setPath(running.path);
        setStatus("Running");
        setLog(["Picked up a run already in progress."]);
        // From the start: the board has no state from a run it never watched.
        cursor.current = -1;
        attach(running.run_id, -1);
      } catch {
        /* nothing in flight is the normal case, not an error worth showing */
      }
    })();
    return () => {
      abandoned = true;
    };
    // Deliberately once per mount: this is recovery, not a subscription.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Show a freshly drafted document on the board without saving it yet. */
  const adoptDraft = useCallback((document: Record<string, unknown>, note: string) => {
    setDraft(document);
    setPlan(null);
    const raw = (document.nodes ?? {}) as Record<string, Record<string, unknown>>;
    setNodes(
      Object.entries(raw).map(([id, node]) => ({
        id,
        title: String(node.title ?? id),
        type: String(node.type ?? "task"),
        description: String(node.description ?? ""),
        depends_on: (node.depends_on as string[]) ?? [],
        profile: String(node["x-agent-profile"] ?? ""),
        tier: String((node.intelligence as { tier?: string } | undefined)?.tier ?? ""),
        state: "pending",
      })),
    );
    setStatus(note);
  }, []);

  async function startBlank() {
    setBusy("blank");
    try {
      const created = await request<{ document: Record<string, unknown> }>("/api/graphs/blank", {
        method: "POST",
        body: JSON.stringify({ title: "New workflow" }),
      });
      adoptDraft(created.document, "Unsaved draft");
      setDraftPath("workflow.agraph.yaml");
      setPath("");
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function addCard() {
    const document = draft ?? (path ? await request<{ document: Record<string, unknown> }>(
      `/api/graphs/document?path=${encodeURIComponent(path)}`,
    ).then((data) => data.document) : null);
    if (!document) return;
    setBusy("card");
    try {
      const updated = await request<{ node_id: string; document: Record<string, unknown> }>(
        "/api/graphs/card",
        { method: "POST", body: JSON.stringify({ document }) },
      );
      adoptDraft(updated.document, "Unsaved draft");
      if (!draftPath) setDraftPath(path || "workflow.agraph.yaml");
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function generate() {
    if (!goal.trim()) return;
    setBusy("generate");
    setStatus("Drafting…");
    try {
      const created = await request<{ document: Record<string, unknown> }>("/api/graphs/generate", {
        method: "POST",
        body: JSON.stringify({ goal, use_ai: true }),
      });
      adoptDraft(created.document, "Unsaved draft");
      setDraftPath(draftPath || "workflow.agraph.yaml");
      setPath("");
    } catch (problem) {
      setError((problem as Error).message);
      setStatus("Ready");
    } finally {
      setBusy("");
    }
  }

  async function saveDraft() {
    if (!draft || !draftPath.trim()) return;
    setBusy("save");
    try {
      const saved = await request<Plan>("/api/graphs/document", {
        method: "POST",
        body: JSON.stringify({ path: draftPath.trim(), document: draft }),
      });
      setDraft(null);
      setPlan(saved);
      setNodes(saved.nodes);
      setStatus("Saved");
      const found = await request<GraphFile[]>("/api/graphs");
      setFiles(found);
      setPath(saved.path);
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy("");
    }
  }

  /** Export the current graph as a file the user can keep or share. */
  async function exportGraph() {
    try {
      const document =
        draft ??
        (await request<{ document: Record<string, unknown> }>(
          `/api/graphs/document?path=${encodeURIComponent(path)}`,
        ).then((data) => data.document));
      const name = (draftPath || path || "workflow.agraph.yaml").split("/").pop()!;
      const body = JSON.stringify(document, null, 2);
      const url = URL.createObjectURL(new Blob([body], { type: "application/json" }));
      const anchor = window.document.createElement("a");
      anchor.href = url;
      anchor.download = name.replace(/\.ya?ml$/, ".json");
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (problem) {
      setError((problem as Error).message);
    }
  }

  async function decide(approved: boolean) {
    if (!runId || !gate) return;
    try {
      await request(`/api/graphs/runs/${runId}/gates/${gate.request_id}`, {
        method: "POST",
        body: JSON.stringify({ approved }),
      });
      setGate(null);
    } catch (problem) {
      setError((problem as Error).message);
    }
  }

  const errors = (plan?.findings || []).filter((item) => item.severity === "error");

  return (
    <div className="page graphs-page">
      <div className="page-header">
        <div>
          <small>AGENTIC GRAPH</small>
          <h1>Graphs</h1>
          <p>Validate a portable AGS 1.0 workflow, then run it under the same governed runtime as the CLI.</p>
        </div>
        <div className="graph-actions">
          <button className="secondary-action" type="button" onClick={() => void loadPlan(path)} disabled={!path}>
            Revalidate
          </button>
          <button className="secondary-action" type="button" onClick={() => void start(true)} disabled={!plan?.ok || Boolean(runId)}>
            Dry run
          </button>
          <button className="primary-action" type="button" onClick={() => void start(false)} disabled={!plan?.ok || Boolean(runId)}>
            ▶ Run graph
          </button>
        </div>
      </div>

      <section className="graph-author">
        <div className="graph-author-head">
          <small>BUILD A GRAPH</small>
          <p>Start from a file, from a blank board, or from a goal the model turns into a draft.</p>
        </div>
        <div className="graph-author-row">
          <label className="sr-only" htmlFor="graphGoal">What should this graph accomplish?</label>
          <textarea
            id="graphGoal"
            rows={2}
            placeholder="Audit promotion handling, add regression tests, and draft release notes…"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
          />
          <div className="graph-author-buttons">
            <button className="primary-action" type="button" onClick={() => void generate()}
                    disabled={Boolean(busy) || !goal.trim()}>
              {busy === "generate" ? "Drafting…" : "✦ Generate with AI"}
            </button>
            <button className="secondary-action" type="button" onClick={() => void startBlank()} disabled={Boolean(busy)}>
              ＋ Blank graph
            </button>
            <button className="secondary-action" type="button" onClick={() => void addCard()}
                    disabled={Boolean(busy) || (!draft && !path)}>
              ＋ Add card
            </button>
            <button className="secondary-action" type="button" onClick={() => void exportGraph()}
                    disabled={Boolean(busy) || (!draft && !path)}>
              ↓ Export
            </button>
          </div>
        </div>
      </section>

      {draft && (
        <section className="graph-draft" role="status">
          <div>
            <b>Unsaved draft</b>
            <span>{nodes.length} card{nodes.length === 1 ? "" : "s"}. Nothing is written until you save.</span>
          </div>
          <div className="graph-draft-save">
            <label className="sr-only" htmlFor="draftPath">Save as</label>
            <input
              id="draftPath"
              value={draftPath}
              placeholder="workflow.agraph.yaml"
              onChange={(event) => setDraftPath(event.target.value)}
            />
            <button className="primary-action" type="button" onClick={() => void saveDraft()}
                    disabled={busy === "save" || !draftPath.trim()}>
              {busy === "save" ? "Saving…" : "Save graph"}
            </button>
            <button className="secondary-action" type="button" onClick={() => { setDraft(null); void loadPlan(path); }}>
              Discard
            </button>
          </div>
        </section>
      )}

      <div className="graph-picker">
        <label htmlFor="graphFile">Graph file</label>
        <select id="graphFile" value={path} onChange={(event) => setPath(event.target.value)}>
          <option value="">Choose a graph…</option>
          {files.map((file) => (
            <option key={file.path} value={file.path}>{file.path}</option>
          ))}
        </select>
        {!files.length && (
          <p className="graph-empty-hint">
            No <code>.agraph.yaml</code> found in this workspace. Create one with{" "}
            <code>loro graph generate</code>.
          </p>
        )}
      </div>

      {errors.length > 0 && (
        <div className="graph-errors" role="alert">
          <b>This graph is not valid.</b>
          <ul>
            {errors.map((finding, index) => (
              <li key={index}>{finding.code ? `${finding.code}: ` : ""}{finding.message}</li>
            ))}
          </ul>
        </div>
      )}

      {plan && (
        <>
          <div className="graph-overview">
            <div><small>GRAPH</small><b>{plan.graph_id}</b></div>
            <div><small>JOBS</small><b>{plan.node_count}</b></div>
            <div><small>MAX PARALLEL</small><b>{plan.max_parallel}</b></div>
            <div><small>WORST CASE</small><b>{plan.worst_case_executions}</b></div>
            <div><small>COST CEILING</small><b>{plan.estimated_cost_usd === null ? "—" : `$${plan.estimated_cost_usd.toFixed(2)}`}</b></div>
            <div className="graph-status"><small>STATUS</small><b>{status}</b></div>
          </div>
          {plan.objective && <p className="graph-objective">{plan.objective}</p>}
          <p className="graph-digest">digest {plan.digest}</p>
        </>
      )}

      {gate && (
        <div className="gate-card" role="alertdialog" aria-label="Human gate">
          <small>HUMAN GATE</small>
          <h3>{gate.prompt}</h3>
          {gate.roles.length > 0 && <p>Roles: {gate.roles.join(", ")}</p>}
          <div>
            <button className="secondary-action" type="button" onClick={() => void decide(false)}>Reject</button>
            <button className="primary-action" type="button" onClick={() => void decide(true)}>Approve</button>
          </div>
        </div>
      )}

      {(plan || draft) && (
        <div className="graph-board">
          {LANES.map((lane) => {
            const cards = nodes.filter((node) => laneFor(node) === lane.id);
            return (
              <section className="graph-lane" key={lane.id}>
                <header>
                  <div><b>{lane.title}</b><small>{lane.note}</small></div>
                  <span className="lane-count">{cards.length}</span>
                </header>
                {cards.length ? (
                  cards.map((node) => <Card key={node.id} node={node} />)
                ) : (
                  <div className="lane-empty">
                    {lane.id === "pending" ? "Load a graph, start a blank one, or generate one from a goal." : "Nothing here yet."}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}

      {log.length > 0 && (
        <section className="graph-log">
          <small>RUN ACTIVITY</small>
          <pre>{log.join("\n")}</pre>
        </section>
      )}
    </div>
  );
}
