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

function Card({ node, onEdit }: { node: GraphNode; onEdit: () => void }) {
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
      <button className="secondary-action graph-card-edit" type="button" onClick={onEdit}>
        Edit card
      </button>
    </article>
  );
}

// Bounded: a server that has actually gone away should say so rather than
// leave the board reconnecting forever.
const MAX_RECONNECTS = 5;
const RECONNECT_DELAY_MS = 1200;
const GRAPH_DRAFT_KEY = "loro:graph-draft";

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
  const [editing, setEditing] = useState("");
  const [profiles, setProfiles] = useState<string[]>([]);
  const [operationStarted, setOperationStarted] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [generationActivity, setGenerationActivity] = useState("");
  const [runActivity, setRunActivity] = useState("");
  const stream = useRef<EventSource | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const found = await request<GraphFile[]>("/api/graphs");
        const graphFiles = Array.isArray(found) ? found : [];
        setFiles(graphFiles);
        if (graphFiles[0]) setPath(graphFiles[0].path);
        const available = await request<Array<{ name: string }>>("/api/profiles").catch(() => []);
        setProfiles((Array.isArray(available) ? available : []).map((item) => item.name));
        const saved = JSON.parse(sessionStorage.getItem(GRAPH_DRAFT_KEY) || "null") as { document?: Record<string, unknown>; path?: string; goal?: string } | null;
        if (saved?.document) { setDraft(saved.document); setDraftPath(saved.path || "workflow.agraph.yaml"); setGoal(saved.goal || ""); adoptDraft(saved.document, "Unsaved draft restored"); }
      } catch (problem) {
        setError((problem as Error).message);
      }
    })();
    return () => stream.current?.close();
  }, [setError]);

  useEffect(() => { if (draft) sessionStorage.setItem(GRAPH_DRAFT_KEY, JSON.stringify({ document: draft, path: draftPath, goal })); }, [draft, draftPath, goal]);
  useEffect(() => { if (!operationStarted) { setElapsed(0); return; } const tick = () => setElapsed(Math.floor((Date.now() - operationStarted) / 1000)); const timer = window.setInterval(tick, 1000); return () => window.clearInterval(timer); }, [operationStarted]);

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
        setRunActivity(`Waiting for approval: ${data.prompt}`);
      });
      source.addEventListener("gate.resolved", (event) => {
        track(event);
        setGate(null);
        setStatus("Running");
        setRunActivity("Approval received; finding the next ready card.");
      });
      source.addEventListener("node.started", (event) => {
        track(event);
        const data = JSON.parse((event as MessageEvent).data);
        setNodes((current) =>
          current.map((node) => (node.id === data.node_id ? { ...node, state: "running" } : node)),
        );
        note(`▶ ${data.node_id}`);
        setRunActivity(`Running ${data.title || data.node_id}.`);
      });
      source.addEventListener("node.attempt.started", (event) => {
        track(event);
        const data = JSON.parse((event as MessageEvent).data);
        const message = `${data.title || data.node_id} · model attempt ${data.attempt || 1}: ${data.activity || "executing the card"}`;
        note(`… ${message}`);
        setRunActivity(message);
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
        setRunActivity(`${data.title || data.node_id} finished with status ${data.status || "succeeded"}.`);
      });
      source.addEventListener("run.finished", (event) => {
        track(event);
        const data = JSON.parse((event as MessageEvent).data);
        setStatus(data.status || "finished");
        note(`Run ${data.status || "finished"}`);
        setRunActivity(`Graph run finished with status ${data.status || "finished"}.`);
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

  async function editCard(id: string) {
    if (runId) return;
    try {
      if (!draft) {
        if (!path) return;
        const loaded = await request<{ document: Record<string, unknown> }>(
          `/api/graphs/document?path=${encodeURIComponent(path)}`,
        );
        adoptDraft(loaded.document, "Unsaved changes");
        setDraftPath(path);
      }
      setEditing(id);
    } catch (problem) {
      setError((problem as Error).message);
    }
  }

  function patchCard(id: string, patch: Record<string, unknown>) {
    if (!draft) return;
    const raw = (draft.nodes ?? {}) as Record<string, Record<string, unknown>>;
    adoptDraft(
      { ...draft, nodes: { ...raw, [id]: { ...raw[id], ...patch } } },
      "Unsaved changes",
    );
  }

  function deleteCard(id: string) {
    if (!draft) return;
    const raw = { ...((draft.nodes ?? {}) as Record<string, Record<string, unknown>>) };
    delete raw[id];
    for (const node of Object.values(raw)) {
      if (Array.isArray(node.depends_on))
        node.depends_on = node.depends_on.filter((item) => item !== id);
    }
    adoptDraft({ ...draft, nodes: raw }, "Unsaved changes");
    setEditing("");
  }

  async function generate() {
    if (!goal.trim()) return;
    setBusy("generate");
    setOperationStarted(Date.now());
    setStatus("Drafting…");
    try {
      const started = await request<{ job_id: string; activity: string }>("/api/graphs/generate/start", {
        method: "POST",
        body: JSON.stringify({ goal, use_ai: true }),
      });
      setGenerationActivity(started.activity);
      let created: { status: string; activity: string; document?: Record<string, unknown>; error?: string };
      do {
        await new Promise((resolve) => window.setTimeout(resolve, 800));
        created = await request(`/api/graphs/generate/status/${started.job_id}`);
        setGenerationActivity(created.activity);
      } while (created.status === "running");
      if (created.status !== "completed" || !created.document) throw new Error(created.error || "Graph authoring failed.");
      adoptDraft(created.document, "Unsaved draft");
      setDraftPath(draftPath || "workflow.agraph.yaml");
      setPath("");
    } catch (problem) {
      setError((problem as Error).message);
      setStatus("Ready");
    } finally {
      setBusy("");
      setOperationStarted(null);
      setGenerationActivity("");
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
      sessionStorage.removeItem(GRAPH_DRAFT_KEY);
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

      {busy === "generate" && <section className="operation-health" role="status" aria-live="polite"><span className="operation-spinner" aria-hidden="true"/><div><b>Generating and validating the graph</b><p>{elapsed >= 90 ? "This is taking longer than usual; Loro's configured model request timeout remains authoritative. " : ""}{generationActivity || "Starting the graph authoring job…"} You may switch sections; this reports lifecycle progress, not private model reasoning.</p></div><span>{elapsed}s</span></section>}

      {runId && <section className="operation-health" role="status" aria-live="polite"><span className="operation-spinner" aria-hidden="true"/><div><b>Graph runner is active</b><p><strong>Most recent activity:</strong> {runActivity || "Waiting for the first ready card."} This is safe lifecycle visibility, not private model reasoning.</p></div><span>{status}</span></section>}

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
            <button className="secondary-action" type="button" onClick={() => { setDraft(null); sessionStorage.removeItem(GRAPH_DRAFT_KEY); void loadPlan(path); }}>
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
                  cards.map((node) => (
                    <Card key={node.id} node={node} onEdit={() => void editCard(node.id)} />
                  ))
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
      {editing && draft && (() => {
        const raw = (draft.nodes ?? {}) as Record<string, Record<string, any>>;
        const node = raw[editing];
        if (!node) return null;
        const requirements = (node.requirements ?? {}) as Record<string, any>;
        return <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={`Edit ${String(node.title || editing)}`}>
          <div className="modal graph-node-editor">
            <button className="modal-close" type="button" onClick={() => setEditing("")}>×</button>
            <small>GRAPH CARD</small><h2>Edit {editing}</h2>
            <label>Title<input value={String(node.title ?? "")} onChange={(event) => patchCard(editing, { title: event.target.value })} /></label>
            <label>Instructions<textarea rows={4} value={String(node.description ?? "")} onChange={(event) => patchCard(editing, { description: event.target.value })} /></label>
            <label>Agent profile<select value={String(node["x-agent-profile"] ?? "")} onChange={(event) => patchCard(editing, { "x-agent-profile": event.target.value || undefined })}><option value="">Run default</option>{profiles.map((name) => <option key={name} value={name}>@{name}</option>)}</select></label>
            <fieldset><legend>Dependencies</legend><div className="graph-dependency-list">{Object.keys(raw).filter((id) => id !== editing).map((id) => { const selected = (node.depends_on ?? []).includes(id); return <label key={id}><input type="checkbox" checked={selected} onChange={() => patchCard(editing, { depends_on: selected ? node.depends_on.filter((item: string) => item !== id) : [...(node.depends_on ?? []), id] })} />{id}</label>; })}</div></fieldset>
            <label>Required tools <small>comma-separated logical capabilities</small><input value={(requirements.tools ?? []).join(", ")} onChange={(event) => patchCard(editing, { requirements: { ...requirements, tools: csv(event.target.value) } })} placeholder="file_read, file_write, shell_exec" /></label>
            <label>Permissions <small>comma-separated portable requirements</small><input value={(requirements.permissions ?? []).join(", ")} onChange={(event) => patchCard(editing, { requirements: { ...requirements, permissions: csv(event.target.value) } })} placeholder="fs:read:**, fs:write:src/**" /></label>
            <div className="graph-editor-actions"><button className="secondary-action danger" type="button" onClick={() => deleteCard(editing)}>Delete card</button><button className="primary-action" type="button" onClick={() => setEditing("")}>Apply changes</button></div>
          </div>
        </div>;
      })()}
    </div>
  );
}

function csv(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}
