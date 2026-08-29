import { useEffect, useState } from "react";
import { request } from "./api";

export function ExtensionsView({ setError }: { setError: (message: string) => void }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => { request("/api/workspace/extensions").then(setData).catch((reason) => setError(String(reason))); }, [setError]);
  if (!data) return <div className="page"><p>Loading extensions…</p></div>;
  return <div className="page extensions-page"><header className="page-header"><div><small>CAPABILITY INVENTORY</small><h1>Extensions</h1><p>Effective MCP servers, protocol extensions, and skills discovered for this workspace.</p></div></header>
    <section><h2>MCP servers <span>{data.mcp_enabled ? "enabled" : "disabled"}</span></h2><div className="extension-grid">{data.mcp_servers.map((item: any) => <article key={item.name}><b>{item.name}</b><span>{item.enabled ? "Enabled" : "Disabled"}</span><p>{item.transport} · {item.configured ? "configured" : "unconfigured"}</p>{item.extensions?.length > 0 && <small>{item.extensions.join(", ")}</small>}</article>)}</div>{!data.mcp_servers.length && <p>No MCP servers configured.</p>}</section>
    <section><h2>MCP protocol extensions</h2><div className="extension-grid">{data.mcp_extensions.map((item: any) => <article key={item.name}><b>{item.name}</b><span>{item.enabled ? `v${item.version}` : "Disabled"}</span><p>{item.adapter || "protocol extension"}</p></article>)}</div>{!data.mcp_extensions.length && <p>No protocol extensions configured.</p>}</section>
    <section><h2>Skills</h2><div className="extension-grid">{data.skills.map((item: any) => <article key={item.path}><b>{item.name}</b><span>Enabled</span><p>{item.path}</p></article>)}</div>{!data.skills.length && <p>No skills discovered.</p>}</section>
  </div>;
}
