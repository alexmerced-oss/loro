import { FormEvent, useEffect, useState } from "react";
import { request } from "./api";

export function RunCenterView({ setError }: { setError: (message: string) => void }) {
  const [data, setData] = useState<any>(null);
  const [graphs, setGraphs] = useState<any[]>([]);
  const [notifications, setNotifications] = useState(localStorage.getItem("loro-notifications") === "on");

  async function refresh() {
    try {
      const [center, graphList] = await Promise.all([request<any>("/api/run-center"), request<any[]>("/api/graphs")]);
      if (notifications && data && "Notification" in window && Notification.permission === "granted") {
        const prior = new Set(data.chat_runs.filter((item: any) => item.status === "running").map((item: any) => item.id));
        const finished = center.chat_runs.find((item: any) => prior.has(item.id) && item.status !== "running");
        if (finished) new Notification("Loro task finished", { body: `${finished.conversation_title}: ${finished.status}` });
      }
      setData(center); setGraphs(graphList);
    } catch (reason) { setError(String(reason)); }
  }

  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer); }, [notifications]);

  async function toggleNotifications() {
    if (!("Notification" in window)) return setError("Desktop notifications are unavailable in this browser.");
    if (notifications) { localStorage.removeItem("loro-notifications"); setNotifications(false); return; }
    if (await Notification.requestPermission() === "granted") { localStorage.setItem("loro-notifications", "on"); setNotifications(true); }
  }

  async function schedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    try {
      await request("/api/schedules", { method: "POST", body: JSON.stringify({ graph_path: values.get("graph"), interval_minutes: Number(values.get("interval")) }) });
      event.currentTarget.reset(); await refresh();
    } catch (reason) { setError(String(reason)); }
  }

  if (!data) return <div className="page"><p>Loading runs…</p></div>;
  return <div className="page run-center-page"><header className="page-header"><div><small>BACKGROUND WORK</small><h1>Run center</h1><p>Chat, graph, approval, and scheduled work in one durable operational view.</p></div><div><button className="secondary-action" onClick={() => void toggleNotifications()}>{notifications ? "Notifications on" : "Notify on completion"}</button><button onClick={() => void refresh()}>Refresh</button></div></header>
    <div className="run-metrics"><div><small>ACTIVE CHATS</small><b>{data.active_chat_runs.length}</b></div><div><small>ACTIVE GRAPHS</small><b>{data.active_graph_runs.length}</b></div><div><small>AWAITING APPROVAL</small><b>{data.active_chat_runs.reduce((sum: number, item: any) => sum + item.awaiting_approval.length, 0)}</b></div><div><small>SCHEDULES</small><b>{data.schedules.filter((item: any) => item.enabled).length}</b></div></div>
    <section><h2>Conversation runs</h2><div className="run-table">{data.chat_runs.map((item: any) => <article key={item.id}><span className={`run-state ${item.status}`} /> <b>{item.conversation_title}</b><span>{item.status}</span><span>{item.provider || "—"}/{item.model || "—"}</span><span>{item.usage?.total_tokens || 0} tokens</span><time>{new Date(item.created_at).toLocaleString()}</time></article>)}</div></section>
    <section><h2>Graph runs</h2><div className="run-table">{[...data.active_graph_runs, ...data.graph_runs].map((item: any, index: number) => <article key={`${item.run_id || item.id}-${index}`}><span className={`run-state ${item.status || "completed"}`} /><b>{item.graph_id || item.path || "Graph"}</b><span>{item.status || "completed"}</span><span>{item.run_id || item.id}</span></article>)}</div></section>
    <section className="schedule-section"><h2>Scheduled graphs</h2><form onSubmit={schedule}><label>Graph<select name="graph" required><option value="">Choose graph</option>{graphs.map((item) => <option key={item.path} value={item.path}>{item.name}</option>)}</select></label><label>Every<input name="interval" type="number" min="1" defaultValue="60" required /> minutes</label><button>Create schedule</button></form><div className="schedule-list">{data.schedules.map((item: any) => <article key={item.id}><div><b>{item.graph_path}</b><small>Every {item.interval_minutes} minutes · next {new Date(item.next_run_at).toLocaleString()}</small>{item.last_error && <em>{item.last_error}</em>}</div><button className="secondary-action" onClick={async () => { await request(`/api/schedules/${item.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !item.enabled }) }); await refresh(); }}>{item.enabled ? "Pause" : "Resume"}</button></article>)}</div></section>
  </div>;
}
