import { useCallback, useEffect, useState } from "react";
import { request } from "./api";
import { Markdown } from "./Markdown";

/**
 * Memory.
 *
 * Loro keeps three related things: local memories written for this workspace,
 * a queue of proposals the agent has raised for a human to decide, and governed
 * shared memory behind a Postgres or Iceberg backend. None of it was reachable
 * from the browser, so the memory shaping every reply was invisible and the
 * proposal queue could only be reviewed from a terminal.
 *
 * Reading is free. The one thing this screen changes is a proposal's decision,
 * which is the point of a queue, and every decision is audited.
 */

type Overview = {
  ok?: boolean;
  error?: string;
  local?: { path?: string; count?: number; scopes?: string[] };
  proposals?: { total?: number; pending?: number; statuses?: string[] };
  shared?: { backend?: string; ok?: boolean; messages?: string[] };
};

type Memory = { memory_id: string; content: string; scope: string; created_at: string };

type Proposal = {
  proposal_id: string;
  content: string;
  target: string;
  rationale: string;
  status: string;
  created_at: string;
  decidable: boolean;
};

type SharedRecord = {
  memory_id: string;
  content: string;
  summary: string;
  classification: string;
  created_by: string;
  created_at: string;
  status: string;
  citation: string;
};

type Tab = "proposals" | "local" | "shared";

export function MemoryView({ setError }: { setError: (message: string) => void }) {
  const [tab, setTab] = useState<Tab>("proposals");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [shared, setShared] = useState<SharedRecord[] | null>(null);
  const [sharedNote, setSharedNote] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const [state, local, queue] = await Promise.all([
        request<Overview>("/api/memory/overview"),
        request<{ memories: Memory[] }>("/api/memory/memories"),
        request<{ proposals: Proposal[] }>("/api/memory/proposals"),
      ]);
      setOverview(state);
      setMemories(local.memories || []);
      setProposals(queue.proposals || []);
    } catch (problem) {
      setError((problem as Error).message);
    }
  }, [setError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function searchLocal() {
    try {
      const found = await request<{ memories: Memory[] }>(
        `/api/memory/memories?q=${encodeURIComponent(query)}`,
      );
      setMemories(found.memories || []);
    } catch (problem) {
      setError((problem as Error).message);
    }
  }

  async function searchShared() {
    try {
      const found = await request<{ records: SharedRecord[]; messages?: string[]; error?: string }>(
        `/api/memory/shared?q=${encodeURIComponent(query)}`,
      );
      setShared(found.records || []);
      setSharedNote(found.error || (found.messages || []).join(" · "));
    } catch (problem) {
      setError((problem as Error).message);
    }
  }

  async function decide(proposal: Proposal, accept: boolean) {
    setBusy(proposal.proposal_id);
    try {
      await request(
        `/api/memory/proposals/${encodeURIComponent(proposal.proposal_id)}/${accept ? "accept" : "reject"}`,
        { method: "POST", body: JSON.stringify({ reason: "" }) },
      );
      await load();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy("");
    }
  }

  const pending = proposals.filter((item) => item.decidable);
  const decided = proposals.filter((item) => !item.decidable);

  return (
    <div className="page memory-page">
      <header className="page-header">
        <div>
          <small>KNOWLEDGE</small>
          <h1>Memory</h1>
          <p>What this workspace remembers, and what the agent is asking to remember.</p>
        </div>
        <button className="secondary-action" type="button" onClick={() => void load()}>
          Refresh
        </button>
      </header>

      {overview?.error && <div className="gov-issues" role="alert">{overview.error}</div>}

      <div className="memory-stats">
        <div>
          <span>LOCAL MEMORIES</span>
          <strong>{overview?.local?.count ?? "—"}</strong>
        </div>
        <div>
          <span>AWAITING A DECISION</span>
          <strong>{overview?.proposals?.pending ?? "—"}</strong>
        </div>
        <div>
          <span>PROPOSALS TOTAL</span>
          <strong>{overview?.proposals?.total ?? "—"}</strong>
        </div>
        <div>
          <span>SHARED BACKEND</span>
          <strong>
            {overview?.shared?.backend ?? "—"}
            {overview?.shared ? (overview.shared.ok ? " · ready" : " · unavailable") : ""}
          </strong>
        </div>
      </div>

      <div className="memory-tabs" role="tablist">
        {(["proposals", "local", "shared"] as Tab[]).map((item) => (
          <button
            key={item}
            role="tab"
            type="button"
            aria-selected={tab === item}
            className={`chip ${tab === item ? "active" : ""}`}
            onClick={() => setTab(item)}
          >
            {item === "proposals"
              ? `Proposals${pending.length ? ` (${pending.length})` : ""}`
              : item === "local"
                ? "Local memories"
                : "Shared memory"}
          </button>
        ))}
      </div>

      {tab === "proposals" && (
        <section className="memory-section">
          {pending.length === 0 && (
            <p className="gov-note">
              Nothing is waiting on you. The agent raises a proposal when it thinks something is
              worth keeping.
            </p>
          )}
          {pending.map((proposal) => (
            <article key={proposal.proposal_id} className="proposal-card">
              <div className="proposal-head">
                <span className="chip">{proposal.target}</span>
                <time>{proposal.created_at.slice(0, 19).replace("T", " ")}</time>
              </div>
              <Markdown>{proposal.content}</Markdown>
              {proposal.rationale && <p className="proposal-why">Why: {proposal.rationale}</p>}
              <div className="proposal-actions">
                <button
                  className="primary-action"
                  type="button"
                  disabled={busy === proposal.proposal_id}
                  onClick={() => void decide(proposal, true)}
                >
                  {proposal.target === "shared" ? "Accept as shared draft" : "Remember this"}
                </button>
                <button
                  className="secondary-action"
                  type="button"
                  disabled={busy === proposal.proposal_id}
                  onClick={() => void decide(proposal, false)}
                >
                  Decline
                </button>
              </div>
            </article>
          ))}

          {decided.length > 0 && (
            <>
              <h2 className="memory-subhead">Already decided</h2>
              <div className="memory-list">
                {decided.map((proposal) => (
                  <div key={proposal.proposal_id} className="memory-row static">
                    <span className={`chip ${proposal.status.startsWith("accepted") ? "ok" : ""}`}>
                      {proposal.status}
                    </span>
                    <small>{proposal.content}</small>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {tab === "local" && (
        <section className="memory-section">
          <form
            className="memory-search"
            onSubmit={(event) => {
              event.preventDefault();
              void searchLocal();
            }}
          >
            <label className="sr-only" htmlFor="memoryQuery">Search memories</label>
            <input
              id="memoryQuery"
              value={query}
              placeholder="Search what this workspace remembers…"
              onChange={(event) => setQuery(event.target.value)}
            />
            <button className="primary-action" type="submit">Search</button>
          </form>
          <p className="gov-note">{overview?.local?.path}</p>
          {memories.length === 0 && <p className="gov-note">No memories match.</p>}
          <div className="memory-list">
            {memories.map((memory) => (
              <div key={memory.memory_id} className="memory-row static">
                <span className="chip">{memory.scope}</span>
                <div>
                  <Markdown>{memory.content}</Markdown>
                  <time>{memory.created_at.slice(0, 19).replace("T", " ")}</time>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === "shared" && (
        <section className="memory-section">
          <form
            className="memory-search"
            onSubmit={(event) => {
              event.preventDefault();
              void searchShared();
            }}
          >
            <label className="sr-only" htmlFor="sharedQuery">Search shared memory</label>
            <input
              id="sharedQuery"
              value={query}
              placeholder="Search governed shared memory…"
              onChange={(event) => setQuery(event.target.value)}
            />
            <button className="primary-action" type="submit">Search</button>
          </form>

          {overview?.shared && !overview.shared.ok && (
            <div className="gov-issues" role="status">
              <b>The {overview.shared.backend} backend is not answering</b>
              <ul>
                {(overview.shared.messages || []).map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            </div>
          )}

          {sharedNote && <p className="gov-note">{sharedNote}</p>}
          {shared?.length === 0 && <p className="gov-note">No shared memories match.</p>}
          <div className="memory-list">
            {(shared || []).map((record) => (
              <div key={record.memory_id} className="memory-row static">
                <span className="chip">{record.classification}</span>
                <div>
                  <Markdown>{record.summary || record.content}</Markdown>
                  {/* The citation is how a shared memory is referred to
                      elsewhere; showing it is most of the point. */}
                  <code className="citation">{record.citation}</code>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
