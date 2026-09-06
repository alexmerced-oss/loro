import { useEffect, useState } from "react";
import { request } from "./api";

type Editor = { kind: "skill" | "mcp"; name: string; description: string; body: string; transport: "stdio" | "streamable_http"; command: string; args: string; url: string; cwd: string; protocol_mode: string; timeout_seconds: number; env_allowlist: string; enabled: boolean };
const blank = (kind: Editor["kind"]): Editor => ({ kind, name: "", description: "", body: "", transport: "stdio", command: "", args: "", url: "", cwd: "", protocol_mode: "auto", timeout_seconds: 30, env_allowlist: "", enabled: true });

export function ExtensionsView({ setError }: { setError: (message: string) => void }) {
  const [data, setData] = useState<any>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [webmcp, setWebmcp] = useState<any>(null);
  const [url, setUrl] = useState("https://alexmerced.app/");
  const [busy, setBusy] = useState(false);
  const [tool, setTool] = useState<any>(null);
  const [args, setArgs] = useState("{}");
  const [result, setResult] = useState<any>(null);

  async function load() {
    const [inventory, status] = await Promise.all([request<any>("/api/workspace/extensions"), request<any>("/api/webmcp/status")]);
    setData(inventory); setWebmcp(status); if (status.url) setUrl(status.url);
  }
  useEffect(() => { load().catch((reason) => setError(String(reason))); }, [setError]);

  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!editor) return;
    try {
      await request("/api/workspace/extensions", { method: "POST", body: JSON.stringify({ ...editor, action: "save", args: editor.args.split("\n").map((item) => item.trim()).filter(Boolean), env_allowlist: editor.env_allowlist.split(/[\n,]/).map((item) => item.trim()).filter(Boolean) }) });
      setEditor(null); await load();
    } catch (reason) { setError(String(reason)); }
  }

  async function remove(kind: Editor["kind"], name: string) {
    if (!window.confirm(`Delete ${kind} ${name}?`)) return;
    try { await request("/api/workspace/extensions", { method: "POST", body: JSON.stringify({ kind, action: "delete", name }) }); await load(); }
    catch (reason) { setError(String(reason)); }
  }

  async function openWebMCP() {
    setBusy(true); setResult(null);
    try { setWebmcp(await request("/api/webmcp/open", { method: "POST", body: JSON.stringify({ url }) })); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }

  async function callWebMCP(approved = false) {
    if (!tool) return;
    let parsed: Record<string, unknown>;
    try { parsed = JSON.parse(args); } catch { setError("WebMCP arguments must be a JSON object."); return; }
    setBusy(true);
    try {
      const response = await request<any>("/api/webmcp/call", { method: "POST", body: JSON.stringify({ url: "", name: tool.name, arguments: parsed, registry_revision: webmcp.registry_revision, approved }) });
      if (response.approval_required && !approved) {
        if (window.confirm(`${response.message}\n\nTool: ${tool.name}\nArguments: ${args}`)) await callWebMCP(true);
      } else setResult(response);
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }

  if (!data) return <div className="page"><p>Loading extensions…</p></div>;
  return <div className="page extensions-page">
    <header className="page-header"><div><small>CAPABILITY INVENTORY</small><h1>Extensions</h1><p>Manage project-owned skills, governed MCP connections, and exact-origin browser tools.</p></div></header>
    <section className="webmcp-panel">
      <div className="section-heading"><div><h2>WebMCP browser tools <span>{webmcp?.connected ? "connected" : "closed"}</span></h2><p className="gov-note">Web pages may expose live tools only on configured HTTPS origins. Discovery never grants permission to run them.</p></div>{webmcp?.connected && <button className="secondary-action" onClick={() => void request("/api/webmcp/close", { method: "POST", body: "{}" }).then(load)}>Close session</button>}</div>
      <div className="webmcp-connect"><label>Page URL<input type="url" value={url} onChange={(event) => setUrl(event.target.value)} /></label><button disabled={busy} onClick={() => void openWebMCP()}>{busy ? "Working…" : "Open and inspect"}</button></div>
      <p><b>Allowed origins:</b> {(webmcp?.origins || []).join(", ") || "None configured"}</p>
      {webmcp?.tools && <div className="extension-grid">{webmcp.tools.map((item: any) => <article key={item.name} className={tool?.name === item.name ? "selected" : ""}><b>{item.name}</b><span>{item.annotations?.readOnlyHint ? "Read only" : "Approval governed"}</span><p>{item.description || "No description supplied by the site."}</p><button className="secondary-action" onClick={() => { setTool(item); setArgs("{}"); setResult(null); }}>Inspect</button></article>)}</div>}
      {tool && <div className="webmcp-invoke"><h3>{tool.name}</h3><details><summary>Input schema</summary><pre>{JSON.stringify(tool.inputSchema, null, 2)}</pre></details><label>Arguments (JSON)<textarea rows={5} value={args} onChange={(event) => setArgs(event.target.value)} /></label><button disabled={busy} onClick={() => void callWebMCP()}>{busy ? "Running…" : "Run tool"}</button>{result && <pre aria-label="WebMCP result">{JSON.stringify(result, null, 2)}</pre>}</div>}
    </section>
    <section><div className="section-heading"><h2>MCP servers <span>{data.mcp_enabled ? "enabled" : "disabled"}</span></h2><button onClick={() => setEditor(blank("mcp"))}>＋ New server</button></div><div className="extension-grid">{data.mcp_servers.map((item: any) => <article key={item.name}><b>{item.name}</b><span>{item.enabled ? "Enabled" : "Disabled"}</span><p>{item.transport} · {item.command || item.url || "unconfigured"}</p><small>Protocol {item.protocol_mode || "auto"}</small><div className="profile-actions"><button className="secondary-action" onClick={() => setEditor({ ...blank("mcp"), ...item, args: (item.args || []).join("\n"), env_allowlist: (item.env_allowlist || []).join("\n") })}>Edit</button><button className="secondary-action danger" onClick={() => void remove("mcp", item.name)}>Delete</button></div></article>)}</div>{!data.mcp_servers.length && <p>No MCP servers configured.</p>}</section>
    <section><h2>MCP protocol extensions <span>managed</span></h2><p className="gov-note">Protocol extensions are supplied by harness policy and cannot be changed from a project.</p><div className="extension-grid">{data.mcp_extensions.map((item: any) => <article key={item.name}><b>{item.name}</b><span>{item.enabled ? `v${item.version}` : "Disabled"}</span><p>{item.adapter || "protocol extension"}</p></article>)}</div></section>
    <section><div className="section-heading"><h2>Skills</h2><button onClick={() => setEditor(blank("skill"))}>＋ New project skill</button></div><div className="extension-grid">{data.skills.map((item: any) => <article key={item.path}><b>{item.name}</b><span>{item.editable ? "Project · editable" : "Managed · read-only"}</span><p>{item.path}</p>{item.editable && <div className="profile-actions"><button className="secondary-action" onClick={() => setEditor({ ...blank("skill"), name: item.name, description: item.description || "", body: item.body || "" })}>Edit</button><button className="secondary-action danger" onClick={() => void remove("skill", item.name)}>Delete</button></div>}</article>)}</div>{!data.skills.length && <p>No skills discovered.</p>}</section>
    {editor && <EditorModal editor={editor} setEditor={setEditor} save={save} />}
  </div>;
}

function EditorModal({ editor, setEditor, save }: { editor: Editor; setEditor: (value: Editor | null) => void; save: (event: React.FormEvent) => void }) {
  return <div className="modal-backdrop" role="dialog" aria-modal="true"><form className="modal" onSubmit={save}><button type="button" className="modal-close" onClick={() => setEditor(null)}>×</button><small>{editor.kind.toUpperCase()}</small><h2>{editor.name ? "Edit" : "Create"} {editor.kind}</h2><label>Name<input required disabled={Boolean(editor.name)} pattern="[a-z0-9][a-z0-9_-]*" value={editor.name} onChange={(event) => setEditor({ ...editor, name: event.target.value })}/></label>{editor.kind === "skill" ? <><label>Description<input required value={editor.description} onChange={(event) => setEditor({ ...editor, description: event.target.value })}/></label><label>Instructions<textarea required rows={10} value={editor.body} onChange={(event) => setEditor({ ...editor, body: event.target.value })}/></label></> : <><div className="form-grid"><label>Transport<select value={editor.transport} onChange={(event) => setEditor({ ...editor, transport: event.target.value as Editor["transport"] })}><option value="stdio">Local process (stdio)</option><option value="streamable_http">Remote Streamable HTTP</option></select></label><label>Protocol<select value={editor.protocol_mode} onChange={(event) => setEditor({ ...editor, protocol_mode: event.target.value })}><option value="auto">Auto-negotiate</option><option value="legacy">Legacy</option><option value="2026-07-28">2026-07-28</option><option value="2025-11-25">2025-11-25</option><option value="2024-11-05">2024-11-05</option></select></label></div>{editor.transport === "stdio" ? <><label>Command<input required value={editor.command} onChange={(event) => setEditor({ ...editor, command: event.target.value })}/></label><label>Arguments (one per line)<textarea rows={4} value={editor.args} onChange={(event) => setEditor({ ...editor, args: event.target.value })}/></label><label>Credential/environment allowlist<textarea rows={3} placeholder="API_TOKEN" value={editor.env_allowlist} onChange={(event) => setEditor({ ...editor, env_allowlist: event.target.value })}/></label></> : <label>Server URL<input required type="url" value={editor.url} onChange={(event) => setEditor({ ...editor, url: event.target.value })}/></label>}<label>Timeout seconds<input type="number" min={1} max={300} value={editor.timeout_seconds} onChange={(event) => setEditor({ ...editor, timeout_seconds: Number(event.target.value) })}/></label></>}<button>Save</button></form></div>;
}
