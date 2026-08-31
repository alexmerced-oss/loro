import { FirstRun } from "./FirstRun";
import { GovernanceView } from "./GovernanceView";
import { MemoryView } from "./MemoryView";
import { GraphsView } from "./GraphsView";
import { ExtensionsView } from "./ExtensionsView";
import { RunCenterView } from "./RunCenterView";
import { WorkspaceView } from "./WorkspaceView";
import { registerShortcuts, chord, type Shortcut } from "./shortcuts";
import { applyTheme, initTheme, nextTheme, storeTheme, themeGlyph, themeLabel, type ThemeChoice } from "./theme";
import { Markdown } from "./Markdown";
import { ApprovalCenter } from "./ApprovalCenter";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { activeRun, initialize, request, streamRun } from "./api";
import type { Conversation, Message, Profile, Settings } from "./types";

type View = "chat" | "runs" | "workspace" | "graphs" | "bots" | "profiles" | "extensions" | "memory" | "governance" | "settings";
type Approval = { runId: string; request_id: string; action: string; target: string; arguments_preview: string; scopes: string[] };

const icons: Record<View, string> = { chat: "⌁", runs: "↻", workspace: "▱", graphs: "⌘", bots: "◉", profiles: "◇", extensions: "⬡", memory: "❖", governance: "⚖", settings: "⚙" };

// Bounded: a server that has actually gone away should say so rather than
// leave the transcript reconnecting forever.
const MAX_RECONNECTS = 5;
const RECONNECT_DELAY_MS = 1200;

export default function App() {
  const [theme, setTheme] = useState<ThemeChoice>(initTheme);
  const [view, setView] = useState<View>("chat");
  const [workspace, setWorkspace] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);

  const shortcuts = useMemo<Shortcut[]>(() => [
    { key: "k", mod: true, describe: "Focus the message box", run: () => {
        setView("chat");
        window.setTimeout(() => document.querySelector<HTMLTextAreaElement>(".composer textarea")?.focus(), 0);
      } },
    { key: "n", mod: true, shift: true, describe: "New conversation", run: () => {
        setView("chat");
        window.dispatchEvent(new CustomEvent("loro:new-conversation"));
      } },
    { key: "1", mod: true, describe: "Go to Chat", run: () => setView("chat") },
    { key: "2", mod: true, describe: "Go to Run center", run: () => setView("runs") },
    { key: "3", mod: true, describe: "Go to Workspace", run: () => setView("workspace") },
    { key: "4", mod: true, describe: "Go to Graphs", run: () => setView("graphs") },
    { key: "5", mod: true, describe: "Go to Bots", run: () => setView("bots") },
    { key: "6", mod: true, describe: "Go to Profiles", run: () => setView("profiles") },
    { key: "7", mod: true, describe: "Go to Extensions", run: () => setView("extensions") },
    { key: "8", mod: true, describe: "Go to Memory", run: () => setView("memory") },
    { key: "9", mod: true, describe: "Go to Governance", run: () => setView("governance") },
    { key: "/", describe: "Show keyboard shortcuts", run: () => setShowShortcuts(true) },
    { key: "Escape", describe: "Close a dialog or clear an error", run: () => {
        setShowShortcuts(false);
        setError("");
      } },
  ], []);

  useEffect(() => registerShortcuts(shortcuts), [shortcuts]);

  const refreshConversations = useCallback(async () => {
    const items = await request<Conversation[]>("/api/conversations");
    setConversations(items);
    setActiveId((current) => current && items.some((item) => item.id === current) ? current : items[0]?.id || null);
  }, []);

  const refreshProfiles = useCallback(async () => {
    setProfiles(await request<Profile[]>("/api/profiles"));
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const session = await initialize();
        setWorkspace(session.workspace);
        // Ask whether this folder can actually run a turn before showing a
        // workspace whose composer would fail on the first message.
        const readiness = await request<{ ready?: boolean }>("/api/onboarding/readiness").catch(
          () => ({ ready: true }),
        );
        // Only an explicit "no". Replacing the whole workspace with a setup
        // panel is a strong intervention, so an unreachable or unexpected
        // answer must not trigger it: the composer reports its own errors.
        if (readiness.ready === false) {
          setNeedsSetup(true);
          setReady(true);
          return;
        }
        await Promise.all([refreshConversations(), refreshProfiles()]);
        setReady(true);
      } catch (reason) {
        setError(String(reason));
      }
    })();
  }, [refreshConversations, refreshProfiles]);

  async function newConversation(profileName?: string, participants?: string[], groupMode = "sequential", coordinatorProfile?: string) {
    try {
      const roster = participants?.filter(Boolean) ?? [];
      const conversation = await request<Conversation>("/api/conversations", {
        method: "POST",
        body: JSON.stringify(
          roster.length
            ? { participants: roster, title: `Group: ${roster.join(", ")}`, group_mode: groupMode, coordinator_profile: coordinatorProfile || null }
            : { profile_name: profileName || null },
        ),
      });
      await refreshConversations();
      setActiveId(conversation.id);
      setView("chat");
    } catch (reason) { setError(String(reason)); }
  }

  if (!ready) return <Splash error={error} />;
  // A fresh folder has no provider; showing an empty workspace whose first
  // message will fail is worse than saying so.
  if (needsSetup) {
    return (
      <FirstRun
        setError={setError}
        onReady={() => {
          setNeedsSetup(false);
          void refreshConversations();
        }}
      />
    );
  }

  return (
    <div className="app-shell">
      <aside className="rail">
        <div className="brand" aria-label="Loro"><span>🦜</span><b>Loro</b></div>
        <nav aria-label="Primary navigation">
          {(Object.keys(icons) as View[]).map((item) => (
            <button key={item} className={view === item ? "active" : ""} onClick={() => setView(item)}>
              <span>{icons[item]}</span>{item}
            </button>
          ))}
        </nav>
        <div className="workspace" title={workspace}><span className="status-dot" />Local workspace<br/><small>{workspace.split("/").pop()}</small></div>
        <button
          className="theme-toggle"
          type="button"
          onClick={() => {
            const choice = nextTheme(theme);
            setTheme(choice);
            applyTheme(choice);
            storeTheme(choice);
          }}
          aria-label={`Theme: ${themeLabel(theme)}. Activate to change.`}
          title={`Theme: ${themeLabel(theme)}`}
        >
          <span aria-hidden="true">{themeGlyph(theme)}</span>
          <i>{themeLabel(theme)}</i>
        </button>
      </aside>
      <main className="main-panel">
        {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError("")}>×</button></div>}
        {showShortcuts && (
          <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"
               onClick={(event) => { if (event.target === event.currentTarget) setShowShortcuts(false); }}>
            <div className="modal shortcuts-sheet">
              <button className="modal-close" onClick={() => setShowShortcuts(false)} aria-label="Close">×</button>
              <small>Keyboard</small>
              <h2>Shortcuts</h2>
              <ul>
                {shortcuts.map((item) => (
                  <li key={item.describe}><span>{item.describe}</span><kbd>{chord(item)}</kbd></li>
                ))}
                <li><span>Send the message</span><kbd>Enter</kbd></li>
                <li><span>Newline in the message box</span><kbd>Shift+Enter</kbd></li>
              </ul>
            </div>
          </div>
        )}
        {view === "chat" && <ChatView conversations={conversations} activeId={activeId} setActiveId={setActiveId} profiles={profiles} onNew={newConversation} refresh={refreshConversations} setError={setError} />}
        {view === "runs" && <RunCenterView setError={setError} />}
        {view === "workspace" && <WorkspaceView setError={setError} />}
        <div className={`persistent-view ${view === "graphs" ? "active" : ""}`} aria-hidden={view !== "graphs"}><GraphsView setError={setError} /></div>
        {view === "bots" && <BotsView profiles={profiles} onChat={newConversation} />}
        <div className={`persistent-view ${view === "profiles" ? "active" : ""}`} aria-hidden={view !== "profiles"}><ProfilesView profiles={profiles} refresh={refreshProfiles} setError={setError} /></div>
        {view === "extensions" && <ExtensionsView setError={setError} />}
        {view === "memory" && <MemoryView setError={setError} />}
        {view === "governance" && <GovernanceView setError={setError} />}
        {view === "settings" && <SettingsView profiles={profiles} refreshProfiles={refreshProfiles} setError={setError} />}
      </main>
      <ApprovalCenter setError={setError} />
    </div>
  );
}

function Splash({ error }: { error: string }) {
  return <div className="splash"><div className="parrot">🦜</div><h1>Loro</h1><p>{error || "Opening your governed workspace…"}</p>{error && <AuthHelp />}</div>;
}

function AuthHelp() {
  const [token, setToken] = useState("");
  return <form onSubmit={(event) => { event.preventDefault(); localStorage.setItem("loro-auth-token", token); location.reload(); }} className="auth-form"><input type="password" placeholder="Bearer token" value={token} onChange={(event) => setToken(event.target.value)} /><button>Connect</button></form>;
}

function ChatView({ conversations, activeId, setActiveId, profiles, onNew, refresh, setError }: {
  conversations: Conversation[]; activeId: string | null; setActiveId: (id: string) => void;
  profiles: Profile[]; onNew: (profile?: string) => void; refresh: () => Promise<void>; setError: (error: string) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [speaker, setSpeaker] = useState("");
  const [listOpen, setListOpen] = useState(false);
  const [context, setContext] = useState<{ path: string; name?: string }[]>([]);
  const uploadInput = useRef<HTMLInputElement>(null);

  // Cmd/Ctrl+Shift+N is registered globally; the handler lives here because
  // this is where onNew is in scope.
  useEffect(() => {
    const open = () => onNew();
    window.addEventListener("loro:new-conversation", open);
    return () => window.removeEventListener("loro:new-conversation", open);
  }, [onNew]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [notice, setNotice] = useState("");
  const [resumed, setResumed] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const active = conversations.find((item) => item.id === activeId);

  const loadMessages = useCallback(async () => {
    if (!activeId) { setMessages([]); return; }
    setMessages(await request<Message[]>(`/api/conversations/${activeId}/messages`));
  }, [activeId]);

  useEffect(() => { loadMessages().catch((reason) => setError(String(reason))); }, [loadMessages, setError]);
  useEffect(() => {
    if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [messages, streaming, approvals]);

  /**
   * Watch a run to completion, resuming if the connection drops.
   *
   * Split out of `send` because starting a turn is not the only way to end up
   * watching one: a browser that reloads mid-reply, or whose connection drops,
   * has to pick the same stream back up. Every event carries its sequence, so
   * a reconnect asks for exactly what it has not seen.
   */
  const watch = useCallback(
    async (id: string, from: number) => {
      let seen = from;
      let attempts = 0;
      setRunId(id);
      try {
        for (;;) {
          try {
            await streamRun(
              id,
              (eventName, data) => {
                if (eventName === "assistant.delta") setStreaming((current) => current + data.content);
                if (eventName === "approval.requested") {
                  const request = data.envelope?.request;
                  if (request) setApprovals((current) => [...current.filter((item) => item.request_id !== request.id), {
                    runId: id,
                    request_id: request.id,
                    action: request.action?.summary || request.action?.name || "Protected action",
                    target: request.action?.resource || request.action?.working_directory || "",
                    arguments_preview: JSON.stringify(request.action?.arguments || {}, null, 2),
                    scopes: (request.choices || []).filter((choice: any) => choice.decision === "approve").map((choice: any) => choice.scope),
                  }]);
                }
                if (eventName === "approval.resolved") {
                  const requestId = data.envelope?.resolution?.request_id || data.request_id;
                  setApprovals((current) => current.filter((item) => item.request_id !== requestId));
                }
                // A group hands off between speakers mid-run; label the live
                // bubble and flush the finished reply so each voice stays a
                // separate message.
                if (eventName === "speaker.started") setSpeaker(String(data.profile || ""));
                if (eventName === "speaker.finished") { setSpeaker(""); void refresh(); }
                if (["run.failed", "run.cancelled"].includes(eventName)) setError(data.error);
              },
              { after: seen, onCursor: (sequence) => { seen = sequence; } },
            );
            return;
          } catch (reason) {
            // A dropped connection is not a dead run: it keeps going and still
            // records its reply, so resume from the cursor instead of losing
            // the rest of the answer. Bounded, so a server that has actually
            // gone away says so rather than retrying forever.
            attempts += 1;
            if (attempts > MAX_RECONNECTS) throw reason;
            setNotice(`Reconnecting to this reply (${attempts}/${MAX_RECONNECTS})…`);
            await new Promise((resolve) => window.setTimeout(resolve, RECONNECT_DELAY_MS));
          }
        }
      } finally {
        setNotice("");
        setRunId(null);
        setStreaming("");
        setSpeaker("");
        setApprovals([]);
        await Promise.all([loadMessages(), refresh()]);
      }
    },
    [loadMessages, refresh, setError],
  );

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!activeId || !draft.trim() || runId) return;
    const content = draft.trim();
    setDraft("");
    setMessages((current) => [...current, { id: `pending-${Date.now()}`, role: "user", content, status: "complete", metadata: {}, created_at: new Date().toISOString() }]);
    setStreaming("");
    try {
      const started = await request<{ run_id: string }>(`/api/conversations/${activeId}/messages`, { method: "POST", body: JSON.stringify({ content, context: context.map((item) => ({ path: item.path })) }) });
      setContext([]);
      await watch(started.run_id, -1);
    } catch (reason) { setError(String(reason)); setRunId(null); setStreaming(""); }
  }

  // Reattach to a reply that was still arriving when this view last went away.
  useEffect(() => {
    if (!activeId || runId) return;
    let abandoned = false;
    (async () => {
      try {
        const live = await activeRun(activeId);
        if (abandoned || !live || live.finished) return;
        setResumed(true);
        setStreaming("");
        // From the start: this browser has no state from a run it never watched.
        await watch(live.run_id, -1);
      } catch {
        /* nothing in flight is the normal case, not an error worth showing */
      } finally {
        if (!abandoned) setResumed(false);
      }
    })();
    return () => { abandoned = true; };
    // Deliberately keyed on the conversation only: this is recovery on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  async function decide(approval: Approval, decision: "approve" | "deny", scope: "once" | "session" = "once") {
    await request(`/api/runs/${approval.runId}/approvals/${approval.request_id}`, { method: "POST", body: JSON.stringify({ decision, scope }) });
    setApprovals((current) => current.filter((item) => item.request_id !== approval.request_id));
  }

  async function archive(id: string) {
    await request(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ status: "archived" }) });
    await refresh();
  }

  async function rename(conversation: Conversation) {
    const title = window.prompt("Rename conversation", conversation.title)?.trim();
    if (!title || title === conversation.title) return;
    await request(`/api/conversations/${conversation.id}`, { method: "PATCH", body: JSON.stringify({ title }) });
    await refresh();
  }

  async function remove(conversation: Conversation) {
    if (!window.confirm(`Permanently delete “${conversation.title}”?`)) return;
    await request(`/api/conversations/${conversation.id}`, { method: "DELETE" });
    await refresh();
  }

  async function upload(files: File[]) {
    if (!activeId) return;
    try {
      for (const file of files) {
        const content_base64 = await fileBase64(file);
        const item = await request<{ path: string; name: string }>(`/api/conversations/${activeId}/attachments`, { method: "POST", body: JSON.stringify({ name: file.name, media_type: file.type || "application/octet-stream", content_base64 }) });
        setContext((current) => [...current, item]);
      }
    } catch (reason) { setError(String(reason)); }
  }

  function addWorkspacePath() {
    const path = window.prompt("Workspace-relative file path")?.trim();
    if (path && !context.some((item) => item.path === path)) setContext((current) => [...current, { path }]);
  }

  return <div className={`chat-layout ${listOpen ? "list-open" : ""}`}>
      <button className="list-scrim" type="button" aria-label="Hide conversations"
              onClick={() => setListOpen(false)} tabIndex={listOpen ? 0 : -1} />
    <section className="conversation-list">
      <div className="section-heading"><div><small>Workspace</small><h2>Conversations</h2></div><button className="icon-button" onClick={() => onNew()} aria-label="New conversation">＋</button></div>
      <div className="conversation-items">
        {conversations.map((item) => <button key={item.id} className={`conversation-row ${item.id === activeId ? "selected" : ""}`} onClick={() => { setActiveId(item.id); setListOpen(false); }}>
          <span className="conversation-icon">{item.profile_name ? "◉" : "⌁"}</span><span><b>{item.title}</b><small>{item.profile_name || "Loro default"} · {relativeTime(item.updated_at)}</small></span>
        </button>)}
        {!conversations.length && <div className="empty-small">No conversations yet.</div>}
      </div>
      {active && <div className="conversation-actions"><button onClick={() => rename(active)}>Rename</button><button onClick={() => archive(active.id)}>Archive</button><button className="danger" onClick={() => remove(active)}>Delete</button></div>}
    </section>
    <section className="chat-stage">
      <header className="chat-header">
        <button className="list-toggle" type="button" onClick={() => setListOpen(true)}
                aria-label="Show conversations" aria-expanded={listOpen}>☰</button><div><small>{active?.participants?.length ? `${active.group_mode || "sequential"} group` : active?.profile_name ? "BOT CONVERSATION" : "CONVERSATION"}</small><h1>{active?.title || "Start a conversation"}</h1></div>{active?.profile_name && <span className="profile-chip">{active.profile_name} · r{active.profile_revision}</span>}</header>
      <div className="transcript" ref={transcript} role="log" aria-label="Conversation transcript"
           aria-live="polite" aria-relevant="additions text" aria-busy={Boolean(runId)}>
        {!active && <EmptyChat onNew={() => onNew()} />}
        {active && !messages.length && !streaming && <div className="welcome-message"><div className="avatar">🦜</div><h2>What are we working on?</h2><p>Ask a question, inspect this workspace, or start a governed task.</p><div className="prompt-grid">{["Summarize this project", "What should I work on next?", "Review the current architecture"].map((prompt) => <button key={prompt} onClick={() => setDraft(prompt)}>{prompt}<span>↗</span></button>)}</div></div>}
        {messages.filter((item) => item.role !== "tool").map((message) => <MessageBubble key={message.id} message={message} />)}
        {resumed && runId && <p className="run-resumed" role="status">Picked this reply back up. It kept going while the view was away.</p>}
        {notice && <p className="run-resumed" role="status">{notice}</p>}
        {streaming && <div className="message assistant"><div className="message-label">{speaker || "Loro"} <span className="live-dot" /></div><div className="message-content"><Markdown>{streaming}</Markdown></div></div>}
        <p className="sr-only" role="status">{runId ? "Loro is working." : approvals.length ? `${approvals.length} approvals required.` : ""}</p>
        {approvals.map((approval) => <div className="approval-card" key={approval.request_id}><small>APPROVAL REQUIRED</small><h3>{approval.action}</h3><p>{approval.target}</p><code>{approval.arguments_preview}</code><div><button className="secondary" onClick={() => decide(approval, "deny")}>Deny</button><button onClick={() => decide(approval, "approve", "once")}>Approve once</button>{approval.scopes.includes("session") && <button onClick={() => decide(approval, "approve", "session")}>For session</button>}</div></div>)}
      </div>
      {active && <form className="composer" onSubmit={send}><textarea aria-label="Message" rows={2} placeholder={`Message ${active.profile_name || "Loro"}…`} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} disabled={Boolean(runId)} />{context.length > 0 && <div className="context-chips">{context.map((item) => <span key={item.path}>{item.name || item.path}<button type="button" onClick={() => setContext((current) => current.filter((found) => found.path !== item.path))}>×</button></span>)}</div>}<div className="composer-footer"><span><button type="button" className="context-action" onClick={addWorkspacePath}>＋ Workspace file</button><button type="button" className="context-action" onClick={() => uploadInput.current?.click()}>↑ Upload</button><input ref={uploadInput} type="file" multiple hidden onChange={(event) => { void upload([...event.target.files || []]); event.target.value = ""; }} /> Enter to send · Shift+Enter for a new line</span>{runId ? <button type="button" className="stop" onClick={() => request(`/api/runs/${runId}/cancel`, { method: "POST" })}>■ Stop</button> : <button disabled={!draft.trim()} aria-label="Send message">↑</button>}</div></form>}
    </section>
  </div>;
}

function MessageBubble({ message }: { message: Message }) {
  return <div className={`message ${message.role} ${message.status === "error" ? "error" : ""}`}><div className="message-label">{message.role === "user" ? "You" : message.role === "assistant" ? (String(message.metadata?.profile || "") || "Loro") : "System"}<time>{new Date(message.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time></div><div className="message-content">{message.role === "user" ? message.content : <Markdown>{message.content}</Markdown>}</div>{Boolean(message.metadata.stop_reason) && <div className="message-meta">{String(message.metadata.stop_reason)} · {String((message.metadata.usage as any)?.total_tokens || 0)} tokens</div>}</div>;
}

function EmptyChat({ onNew }: { onNew: () => void }) { return <div className="welcome-message"><div className="avatar">🦜</div><h2>Your local agent workspace</h2><p>Create a conversation to begin.</p><button className="primary-action" onClick={onNew}>New conversation</button></div>; }

function BotsView({ profiles, onChat }: {
  profiles: Profile[];
  onChat: (profile?: string, participants?: string[], groupMode?: string, coordinatorProfile?: string) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [groupMode, setGroupMode] = useState("sequential");
  const [coordinator, setCoordinator] = useState("");

  function toggle(name: string) {
    if (coordinator === name && selected.includes(name)) setCoordinator("");
    setSelected((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : current.length >= 5
          ? current
          : [...current, name],
    );
  }

  return <div className="page"><PageHeader
      eyebrow="Profile-backed assistants"
      title="Bots"
      description="Talk to any profile on its own, or pick several and put them in a room together."
    />
    {selected.length > 0 && (
      <div className="group-bar" role="status">
        <div>
          <b>{selected.length} selected</b>
          <span>{selected.join(" · ")}</span>
        </div>
        <div className="group-bar-actions">
          <select aria-label="Group execution mode" value={groupMode} onChange={(event) => setGroupMode(event.target.value)}><option value="sequential">Sequential</option><option value="parallel">Parallel</option><option value="coordinator">Coordinator</option></select>
          {groupMode === "coordinator" && <select aria-label="Coordinator profile" value={coordinator} onChange={(event) => setCoordinator(event.target.value)}><option value="">Choose coordinator</option>{selected.map((name) => <option key={name} value={name}>{name}</option>)}</select>}
          <button className="secondary-action" type="button" onClick={() => setSelected([])}>Clear</button>
          <button className="primary-action" type="button" disabled={selected.length < 2 || (groupMode === "coordinator" && !coordinator)}
                  onClick={() => { onChat(undefined, selected, groupMode, coordinator); setSelected([]); }}>
            Start group chat
          </button>
        </div>
        {selected.length < 2 && <small className="group-hint">Pick at least two profiles for a group.</small>}
        {selected.length === 5 && <small className="group-hint">Five is the maximum.</small>}
      </div>
    )}
    <div className="bot-grid">{profiles.map((profile, index) => {
      const picked = selected.includes(profile.name);
      return <article className={`bot-card ${picked ? "picked" : ""}`} key={profile.name}>
        <div className="bot-card-head">
          <div className={`bot-mark tone-${index % 4}`}>{profile.name.slice(0, 2).toUpperCase()}</div>
          <label className="bot-pick">
            <input type="checkbox" checked={picked} onChange={() => toggle(profile.name)}
                   aria-label={`Add ${profile.name} to a group chat`} />
            <span>Group</span>
          </label>
        </div>
        <div className="bot-title"><div><h2>{profile.name}</h2><p>{profile.description || "A governed Loro assistant."}</p></div><span className={`trust ${profile.trust}`}>{profile.trust}</span></div>
        <div className="capability-row"><span>{profile.provider}/{profile.model}</span><span>{profile.tool_count} tools</span><span>{profile.skill_count} skills</span></div>
        {profile.adjustment_count > 0 && <p className="adjustment">◇ {profile.adjustment_count} managed policy adjustment{profile.adjustment_count === 1 ? "" : "s"}</p>}
        <button className="chat-bot" onClick={() => onChat(profile.name)}>Chat with {profile.name}<span>↗</span></button>
      </article>;
    })}</div>
    {!profiles.length && <EmptyPanel title="No bots configured" detail="Create a profile to give a bot its role, tools, and limits." />}
  </div>;
}

function ProfilesView({ profiles, refresh, setError }: { profiles: Profile[]; refresh: () => Promise<void>; setError: (error: string) => void }) {
  const importInput = useRef<HTMLInputElement>(null);

  /** Download the selected identity as a portable OAP document. */
  async function exportProfile(name: string) {
    try {
      const payload = await request<{ filename: string; document: unknown }>(`/api/profiles/${name}/export`);
      const body = JSON.stringify(payload.document, null, 2);
      const url = URL.createObjectURL(new Blob([body], { type: "application/json" }));
      const anchor = window.document.createElement("a");
      anchor.href = url;
      anchor.download = payload.filename.replace(/\.ya?ml$/, ".json");
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(String(reason));
    }
  }

  /** Adopt an OAP document from a file as a project profile. */
  async function importProfile(file: File) {
    try {
      const text = await file.text();
      const document = JSON.parse(text);
      await request("/api/profiles/import", { method: "POST", body: JSON.stringify({ document }) });
      await refresh();
    } catch (reason) {
      setError(
        String(reason).includes("JSON")
          ? "That file is not a JSON agent profile. Export one first to see the shape."
          : String(reason),
      );
    }
  }

  const [selected, setSelected] = useState<string | null>(profiles[0]?.name || null);
  const [document, setDocument] = useState<any>(null);
  const [effective, setEffective] = useState<any>(null);
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generationBusy, setGenerationBusy] = useState(false);
  const [generationElapsed, setGenerationElapsed] = useState(0);
  const [generatedDocument, setGeneratedDocument] = useState<any>(null);
  const [generationScope, setGenerationScope] = useState("project");
  const current = profiles.find((item) => item.name === selected);
  useEffect(() => { if (!generationBusy) { setGenerationElapsed(0); return; } const started = Date.now(); const timer = window.setInterval(() => setGenerationElapsed(Math.floor((Date.now() - started) / 1000)), 1000); return () => window.clearInterval(timer); }, [generationBusy]);
  useEffect(() => {
    if (!generationBusy) return;
    const button = window.document.querySelector<HTMLButtonElement>(".modal button[type='submit']");
    const form = button?.closest("form");
    if (button) button.textContent = `Generating and validating… ${generationElapsed}s`;
    form?.setAttribute("aria-busy", "true");
    return () => form?.setAttribute("aria-busy", "false");
  }, [generationBusy, generationElapsed]);

  useEffect(() => { if (!selected) return; Promise.all([request(`/api/profiles/${selected}`), request(`/api/profiles/${selected}/effective`)]).then(([profile, authority]) => { setDocument(profile); setEffective(authority); }).catch((reason) => setError(String(reason))); }, [selected, profiles, setError]);

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await request(`/api/profiles/${selected}`, { method: "PUT", body: JSON.stringify(document) });
      await refresh();
    } catch (reason) { setError(String(reason)); }
  }

  async function remove() {
    if (!selected || !current?.editable) return;
    if (!window.confirm(`Delete profile @${selected}? This cannot be undone.`)) return;
    try {
      await request(`/api/profiles/${encodeURIComponent(selected)}`, { method: "DELETE" });
      setSelected(null);
      setDocument(null);
      setEffective(null);
      await refresh();
    } catch (reason) { setError(String(reason)); }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name"));
    const payload = { oap: "1.0", kind: "AgentProfile", metadata: { name, revision: 1, description: String(data.get("description")) }, spec: { role: { instructions: String(data.get("instructions")) }, tools: { policy: "inherit" }, lifecycle: { writeback: "propose" } }, state: { revision: 1, facts: [], preferences: [] }, history: [] };
    try { await request("/api/profiles", { method: "POST", body: JSON.stringify({ document: payload, scope: String(data.get("scope") || "project") }) }); await refresh(); setSelected(name); setCreating(false); } catch (reason) { setError(String(reason)); }
  }
  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setGenerationBusy(true);
    try {
      const proposal = await request<{ document: any }>("/api/profiles/generate", {
        method: "POST",
        body: JSON.stringify({
          prompt: String(form.get("prompt") || ""),
          name: String(form.get("name") || "") || null,
        }),
      });
      setGeneratedDocument(proposal.document);
    } catch (reason) { setError(String(reason)); }
    finally { setGenerationBusy(false); }
  }
  async function acceptGenerated() {
    try {
      await request("/api/profiles", { method: "POST", body: JSON.stringify({ document: generatedDocument, scope: generationScope }) });
      const name = generatedDocument.metadata.name;
      await refresh(); setSelected(name); setGeneratedDocument(null); setGenerating(false);
    } catch (reason) { setError(String(reason)); }
  }

  return <div className="page"><PageHeader eyebrow="Open Agent Profiles" title="Profiles" description="Shape bot behavior while Loro keeps managed policy authoritative." action={<div className="profile-actions">
      <input ref={importInput} type="file" accept=".json,.yaml,.yml,application/json" hidden
             onChange={(event) => { const file = event.target.files?.[0]; if (file) void importProfile(file); event.target.value = ""; }} />
      <button className="secondary-action" type="button" onClick={() => importInput.current?.click()}>↑ Import</button>
      <button className="secondary-action" type="button" disabled={!selected}
              onClick={() => selected && void exportProfile(selected)}>↓ Export</button>
      <button className="secondary-action" onClick={() => { setGeneratedDocument(null); setGenerating(true); }}>✦ Generate profile</button>
      <button onClick={() => setCreating(true)}>＋ New profile</button>
    </div>} />
    {generating && <div className="modal-backdrop"><form className="modal" onSubmit={generate}><button type="button" className="modal-close" onClick={() => { setGenerating(false); setGeneratedDocument(null); }}>×</button><small>GENERATE OAP PROFILE</small><h2>Describe the specialist</h2>{!generatedDocument ? <><label>What should this agent do?<textarea name="prompt" rows={7} required autoFocus placeholder="Review release candidates, verify test evidence, and flag unsafe changes…" /></label><label>Preferred name (optional)<input name="name" pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="release-reviewer" /></label><label>Save location<select value={generationScope} onChange={(event) => setGenerationScope(event.target.value)}><option value="project">Project · .loro/agents</option><option value="portable">Portable with project · .agents</option><option value="universal">Universal across harnesses · ~/.agentprofiles</option><option value="user">This Loro user · ~/.config/loro/agents</option></select></label><p className="gov-note">The model can only select tools, skills, and MCP servers already available here. Loro validates the OAP document before showing it.</p><button disabled={generationBusy}>{generationBusy ? "Generating…" : "Generate draft"}</button></> : <><div className="notice"><b>@{generatedDocument.metadata.name}</b><p>{generatedDocument.metadata.description}</p></div><label>Instructions<textarea rows={8} readOnly value={generatedDocument.spec.role.instructions} /></label><p className="gov-note">Review complete. Creating this profile writes it to the selected {generationScope} location; managed policy can still narrow its authority.</p><div className="profile-actions"><button className="secondary-action" type="button" onClick={() => setGeneratedDocument(null)}>Revise prompt</button><button type="button" onClick={() => void acceptGenerated()}>Create profile</button></div></>}</form></div>}
    {creating && <div className="modal-backdrop"><form className="modal" onSubmit={create}><button type="button" className="modal-close" onClick={() => setCreating(false)}>×</button><small>NEW PROFILE</small><h2>Create a bot identity</h2><label>Name<input name="name" required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="release-reviewer" /></label><label>Description<input name="description" placeholder="Reviews releases for evidence and risk" /></label><label>Instructions<textarea name="instructions" rows={5} defaultValue="Be helpful, precise, and cite concrete evidence." /></label><label>Save location<select name="scope" defaultValue="project"><option value="project">Project · .loro/agents</option><option value="portable">Portable with project · .agents</option><option value="universal">Universal across harnesses · ~/.agentprofiles</option><option value="user">This Loro user · ~/.config/loro/agents</option></select></label><button>Create profile</button></form></div>}
    <div className="profile-layout"><aside className="profile-list">{profiles.map((profile) => <button key={profile.name} className={selected === profile.name ? "selected" : ""} onClick={() => setSelected(profile.name)}><span className="mini-mark">{profile.name.slice(0, 1).toUpperCase()}</span><span><b>{profile.name}</b><small>r{profile.revision} · {profile.source_scope || profile.trust}</small></span>{profile.default && <i>default</i>}</button>)}</aside>
      {current && document && <form className="profile-editor" onSubmit={save}><div className="editor-heading"><div><small>{current.trust.toUpperCase()} PROFILE</small><h2>{current.name}</h2></div><span className="digest" title={current.spec_digest}>digest {current.spec_digest.slice(0, 8)}</span></div>
        {!current.editable && <div className="notice">This profile is managed and read-only. Its effective settings are shown below.</div>}
        <div className="form-grid"><label>Description<input disabled={!current.editable} value={document.metadata.description || ""} onChange={(event) => setDocument({ ...document, metadata: { ...document.metadata, description: event.target.value } })} /></label><label>Revision<input disabled value={document.metadata.revision} /></label></div>
        <label>Role instructions<textarea disabled={!current.editable} rows={8} value={document.spec?.role?.instructions || ""} onChange={(event) => setDocument({ ...document, spec: { ...document.spec, role: { ...document.spec.role, instructions: event.target.value } } })} /></label>
        <div className="editor-section"><h3>Model and capabilities</h3><div className="form-grid three"><label>Provider<input disabled={!current.editable} value={document.spec.model?.provider || ""} placeholder="inherit" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, model: { ...document.spec.model, provider: event.target.value || null } } })} /></label><label>Model ID<input disabled={!current.editable} value={document.spec.model?.id || ""} placeholder="inherit" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, model: { ...document.spec.model, id: event.target.value || null } } })} /></label><label>Tool policy<select disabled={!current.editable} value={document.spec.tools?.policy || "inherit"} onChange={(event) => setDocument({ ...document, spec: { ...document.spec, tools: { ...document.spec.tools, policy: event.target.value } } })}><option value="inherit">Inherit</option><option value="allowlist">Allowlist</option><option value="denylist">Denylist</option></select></label></div>
          <div className="form-grid wide"><label>Allowed tools<input disabled={!current.editable} value={(document.spec.tools?.allow || []).join(", ")} placeholder="file.read, file.search" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, tools: { ...document.spec.tools, allow: parseList(event.target.value) } } })} /></label><label>Denied tools<input disabled={!current.editable} value={(document.spec.tools?.deny || []).join(", ")} placeholder="shell.run" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, tools: { ...document.spec.tools, deny: parseList(event.target.value) } } })} /></label><label>Skills<input disabled={!current.editable} value={referenceNames(document.spec.tools?.skills).join(", ")} placeholder="python-review" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, tools: { ...document.spec.tools, skills: updateNamedReferences(document.spec.tools?.skills, parseList(event.target.value), (name) => ({ name })) } } })} /></label><label>MCP servers<input disabled={!current.editable} value={referenceNames(document.spec.tools?.mcp_servers).join(", ")} placeholder="docs" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, tools: { ...document.spec.tools, mcp_servers: updateNamedReferences(document.spec.tools?.mcp_servers, parseList(event.target.value), (name) => ({ name, transport: "stdio", command: name })) } } })} /></label></div></div>
        <div className="editor-section"><h3>Permissions and workspace</h3><div className="form-grid three">{(["default", "shell", "edit", "network"] as const).map((permission) => <label key={permission}>{permission}<select disabled={!current.editable} value={document.spec.permissions?.[permission] || ""} onChange={(event) => setDocument({ ...document, spec: { ...document.spec, permissions: { ...document.spec.permissions, [permission]: event.target.value || undefined } } })}><option value="">Inherit</option><option value="allow">Allow</option><option value="ask">Ask</option><option value="deny">Deny</option></select></label>)}</div><label>Workspace roots<input disabled={!current.editable} value={(document.spec.permissions?.filesystem?.write_roots || document.spec.permissions?.filesystem?.read_roots || []).join(", ")} placeholder="." onChange={(event) => { const roots = parseList(event.target.value); setDocument({ ...document, spec: { ...document.spec, permissions: { ...document.spec.permissions, filesystem: { ...document.spec.permissions?.filesystem, read_roots: roots, write_roots: roots } } } }); }} /></label></div>
        <div className="editor-section"><h3>Memory, budgets, and learning</h3><div className="form-grid three"><label>Memory stores<input disabled={!current.editable} value={referenceNames(document.spec.memory?.stores).join(", ")} placeholder="oap-state, local" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, memory: { ...document.spec.memory, stores: updateNamedReferences(document.spec.memory?.stores, parseList(event.target.value), (name) => ({ name, kind: name })) } } })} /></label><label>Max turns<input type="number" min="1" disabled={!current.editable} value={document.spec.runtime?.max_turns || ""} placeholder="inherit" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, runtime: { ...document.spec.runtime, max_turns: event.target.value ? Number(event.target.value) : undefined } } })} /></label><label>Max tool calls<input type="number" min="0" disabled={!current.editable} value={document.spec.runtime?.max_tool_calls ?? ""} placeholder="inherit" onChange={(event) => setDocument({ ...document, spec: { ...document.spec, runtime: { ...document.spec.runtime, max_tool_calls: event.target.value ? Number(event.target.value) : undefined } } })} /></label><label>Writeback<select disabled={!current.editable} value={document.spec.lifecycle?.writeback || "propose"} onChange={(event) => setDocument({ ...document, spec: { ...document.spec, lifecycle: { ...document.spec.lifecycle, writeback: event.target.value } } })}><option value="off">Off</option><option value="propose">Propose</option><option value="auto">Auto</option></select></label></div></div>
        <div className="authority"><div className="authority-head"><h3>Effective authority</h3><span>After managed-policy narrowing</span></div><div className="authority-grid"><Metric label="Model route" value={`${effective?.model?.provider || "—"}/${effective?.model?.model || "—"}`} /><Metric label="Tools" value={effective?.tools?.length || 0} /><Metric label="Skills" value={effective?.skills?.length || 0} /><Metric label="Memory" value={effective?.memory_stores?.join(", ") || "none"} /></div>{effective?.adjustments?.length > 0 && <details><summary>{effective.adjustments.length} policy adjustments</summary><pre>{JSON.stringify(effective.adjustments, null, 2)}</pre></details>}</div>
        {current.editable && <div className="form-actions"><span>Saving creates revision {current.revision + 1}</span><div className="profile-actions"><button className="secondary-action danger" type="button" onClick={() => void remove()}>Delete profile</button><button>Save revision</button></div></div>}
      </form>}
    </div>
  </div>;
}

function SettingsView({ profiles, refreshProfiles, setError }: { profiles: Profile[]; refreshProfiles: () => Promise<void>; setError: (error: string) => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [providers, setProviders] = useState<Array<{ name: string; display_name: string; default_model: string; small_model: string; needs_key?: boolean; api_key_env?: string; credential_ready?: boolean }>>([]);
  const [credential, setCredential] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => { Promise.all([request<Settings>("/api/settings"), request<{ providers: Array<{ name: string; display_name: string; default_model: string; small_model: string }> }>("/api/onboarding/providers")]).then(([current, catalog]) => { setSettings(current); setProviders(catalog.providers || []); }).catch((reason) => setError(String(reason))); }, [setError]);
  async function save(event: FormEvent) {
    event.preventDefault(); if (!settings) return;
    try { if (credential) await request("/api/onboarding/configure", { method: "POST", body: JSON.stringify({ provider: settings.model.provider, model: settings.model.model, small_model: settings.model.small_model, credential }) }); const updated = await request<Settings>("/api/settings", { method: "PATCH", body: JSON.stringify({ provider: settings.model.provider, model: settings.model.model, small_model: settings.model.small_model, default_profile: settings.agent_profiles.default_profile }) }); setCredential(""); setSettings(updated); setSaved(true); setTimeout(() => setSaved(false), 2500); await refreshProfiles(); } catch (reason) { setError(String(reason)); }
  }
  if (!settings) return <div className="page"><PageHeader eyebrow="Workspace configuration" title="Settings" description="Loading effective defaults…" /></div>;
  const selectedProvider = providers.find((item) => item.name === settings.model.provider);
  return <div className="page settings-page"><PageHeader eyebrow="Workspace configuration" title="Default settings" description="Changes are written to the local project overlay; managed policy still wins." />
    <form onSubmit={save}><section className="settings-card"><div><h2>Model route</h2><p>The provider and models used by conversations without a profile override.</p></div><div className="settings-fields"><label>Provider<select value={settings.model.provider} onChange={(event) => { const provider = providers.find((item) => item.name === event.target.value); setSettings({ ...settings, model: { ...settings.model, provider: event.target.value, model: provider?.default_model || settings.model.model, small_model: provider?.small_model || provider?.default_model || settings.model.small_model } }); }}>{providers.map((provider) => <option value={provider.name} key={provider.name}>{provider.display_name}</option>)}</select></label><label>Primary model<select value={settings.model.model} onChange={(event) => setSettings({ ...settings, model: { ...settings.model, model: event.target.value } })}>{Array.from(new Set([settings.model.model, providers.find((item) => item.name === settings.model.provider)?.default_model].filter(Boolean))).map((model) => <option key={model} value={model}>{model}</option>)}</select></label><label>Small model<select value={settings.model.small_model} onChange={(event) => setSettings({ ...settings, model: { ...settings.model, small_model: event.target.value } })}>{Array.from(new Set([settings.model.small_model, providers.find((item) => item.name === settings.model.provider)?.small_model, settings.model.model].filter(Boolean))).map((model) => <option key={model} value={model}>{model}</option>)}</select></label><div className="credential-state"><span className={settings.model.credential_configured ? "ok" : "warn"} />{settings.model.credential_configured ? "Credential reference configured" : "No credential reference detected"}</div></div></section>
      {selectedProvider?.needs_key && <section className="settings-card"><div><h2>Provider credential</h2><p>Add or rotate the key for {selectedProvider.display_name}. It is stored in Loro's OS-keyring-backed vault and is never returned to the browser.</p></div><div className="settings-fields"><label>API key<input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} placeholder={selectedProvider.credential_ready || settings.model.credential_configured ? "Credential configured · leave blank to keep it" : `Enter key or export ${selectedProvider.api_key_env || "the provider variable"}`} /></label></div></section>}
      <section className="settings-card"><div><h2>Default bot</h2><p>New conversations use this profile unless you select a bot explicitly.</p></div><div className="settings-fields"><label>Default profile<select value={settings.agent_profiles.default_profile || ""} onChange={(event) => setSettings({ ...settings, agent_profiles: { ...settings.agent_profiles, default_profile: event.target.value || null } })}><option value="">No profile</option>{profiles.map((profile) => <option value={profile.name} key={profile.name}>{profile.name} · {profile.source_scope || profile.trust}</option>)}</select></label><div className="setting-facts"><span>Writeback <b>{settings.agent_profiles.writeback}</b></span><span>Local memory <b>{settings.memory.local_enabled ? "on" : "off"}</b></span><span>Shared memory <b>{settings.memory.shared_enabled ? "on" : "off"}</b></span></div></div></section>
      {settings.managed_overlay_active && <div className="managed-banner">◇ A managed configuration overlay is active. Locked values may narrow these defaults.</div>}
      <div className="settings-save"><span>{saved ? "✓ Settings saved" : `Writes to ${settings.write_target}`}</span><button>Save defaults</button></div>
    </form>
  </div>;
}

function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) { return <header className="page-header"><div><small>{eyebrow}</small><h1>{title}</h1><p>{description}</p></div>{action}</header>; }
function Metric({ label, value }: { label: string; value: React.ReactNode }) { return <div><small>{label}</small><b>{value}</b></div>; }
function EmptyPanel({ title, detail }: { title: string; detail: string }) { return <div className="empty-panel"><span>◇</span><h2>{title}</h2><p>{detail}</p></div>; }
function relativeTime(value: string) { const seconds = Math.floor((Date.now() - new Date(value).getTime()) / 1000); if (seconds < 60) return "now"; if (seconds < 3600) return `${Math.floor(seconds / 60)}m`; if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`; return `${Math.floor(seconds / 86400)}d`; }
function parseList(value: string) { return value.split(",").map((item) => item.trim()).filter(Boolean); }
function referenceNames(values: any[] | undefined) { return (values || []).map((item) => typeof item === "string" ? item : item?.name).filter(Boolean); }
function updateNamedReferences(values: any[] | undefined, names: string[], create: (name: string) => any) { const current = new Map((values || []).map((item) => [typeof item === "string" ? item : item?.name, item])); return names.map((name) => current.get(name) || create(name)); }
function fileBase64(file: File): Promise<string> { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onerror = () => reject(new Error(`Could not read ${file.name}`)); reader.onload = () => resolve(String(reader.result).split(",")[1] || ""); reader.readAsDataURL(file); }); }
