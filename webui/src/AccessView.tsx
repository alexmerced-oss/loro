import { FormEvent, useEffect, useState } from "react";
import { request } from "./api";
import { useCan } from "./permissions";

type Role = "viewer" | "operator" | "approver" | "admin";
type Access = {
  mode: string;
  rbac_enabled: boolean;
  you: { subject: string; roles: Role[]; permissions: string[] };
  roles: { name: Role; permissions: string[] }[];
  permission_labels: Record<string, string>;
  mappings: Record<string, Role>;
  admins: string[];
  default_role: Role | null;
  accept_role_names: boolean;
};

const ROLE_NAMES: Role[] = ["viewer", "operator", "approver", "admin"];

/** Who can do what in this workspace, and (for admins) the claim-to-role rules. */
export function AccessView({ setError }: { setError: (message: string) => void }) {
  const [data, setData] = useState<Access | null>(null);
  const [rows, setRows] = useState<{ claim: string; role: Role }[]>([]);
  const [admins, setAdmins] = useState("");
  const [defaultRole, setDefaultRole] = useState<string>("");
  const [saved, setSaved] = useState("");
  const isAdmin = useCan("admin");

  function adopt(value: Access) {
    setData(value);
    setRows(Object.entries(value.mappings).map(([claim, role]) => ({ claim, role })));
    setAdmins(value.admins.join("\n"));
    setDefaultRole(value.default_role || "");
  }

  useEffect(() => { request<Access>("/api/access").then(adopt).catch((reason) => setError(String(reason))); }, [setError]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!data) return;
    setSaved("");
    try {
      const mappings = Object.fromEntries(rows.filter((row) => row.claim.trim()).map((row) => [row.claim.trim(), row.role]));
      const value = await request<Access>("/api/access/admin/rbac", {
        method: "PUT",
        body: JSON.stringify({ mappings, admins: admins.split("\n"), default_role: defaultRole || null, accept_role_names: data.accept_role_names }),
      });
      adopt(value);
      setSaved("Access rules saved.");
    } catch (reason) { setError(String(reason)); }
  }

  if (!data) return <div className="page"><p>Loading access…</p></div>;
  return <div className="page access-page">
    <header className="page-header"><div><small>Multi-user workspace</small><h1>Access</h1><p>Roles come from verified sign-in claims. Every change is audited.</p></div></header>
    <section className="access-card" aria-labelledby="you-heading">
      <h2 id="you-heading">You</h2>
      <p><b>{data.you.subject}</b> · {data.you.roles.length ? data.you.roles.join(", ") : "no role"}</p>
      <ul className="access-permissions">{Object.entries(data.permission_labels).map(([name, label]) => <li key={name} className={data.you.permissions.includes(name) ? "granted" : "missing"}><span aria-hidden="true">{data.you.permissions.includes(name) ? "✓" : "–"}</span>{label}<span className="sr-only">{data.you.permissions.includes(name) ? " (allowed)" : " (not allowed)"}</span></li>)}</ul>
    </section>
    <section className="access-card" aria-labelledby="roles-heading">
      <h2 id="roles-heading">Roles</h2>
      <div className="access-table" role="table" aria-label="Role permissions">
        <div role="row" className="access-row head"><span role="columnheader">Role</span>{Object.keys(data.permission_labels).map((name) => <span role="columnheader" key={name}>{name}</span>)}</div>
        {data.roles.map((role) => <div role="row" className="access-row" key={role.name}><span role="cell"><b>{role.name}</b></span>{Object.keys(data.permission_labels).map((name) => <span role="cell" key={name}>{role.permissions.includes(name) ? "✓" : "–"}</span>)}</div>)}
      </div>
      <p className="gov-note">Operators cannot decide approvals unless they also hold the approver role.</p>
    </section>
    <form className="access-card" onSubmit={save} aria-labelledby="rules-heading">
      <h2 id="rules-heading">Claim to role rules</h2>
      <p className="gov-note">Map a role or group claim from your identity provider to a Loro role.{data.accept_role_names ? " Claims literally named viewer, operator, approver or admin also count." : ""}</p>
      {rows.map((row, index) => <div className="access-rule" key={index}>
        <label>Claim value<input value={row.claim} disabled={!isAdmin} onChange={(event) => setRows(rows.map((item, i) => i === index ? { ...item, claim: event.target.value } : item))} /></label>
        <label>Role<select value={row.role} disabled={!isAdmin} onChange={(event) => setRows(rows.map((item, i) => i === index ? { ...item, role: event.target.value as Role } : item))}>{ROLE_NAMES.map((name) => <option key={name}>{name}</option>)}</select></label>
        {isAdmin && <button type="button" className="secondary-action" aria-label={`Remove rule ${row.claim || index + 1}`} onClick={() => setRows(rows.filter((_, i) => i !== index))}>Remove</button>}
      </div>)}
      {!rows.length && <p className="run-empty">No rules yet.</p>}
      {isAdmin && <button type="button" className="secondary-action" onClick={() => setRows([...rows, { claim: "", role: "viewer" }])}>＋ Add rule</button>}
      <label>Always admins (one subject per line)<textarea rows={3} value={admins} disabled={!isAdmin} onChange={(event) => setAdmins(event.target.value)} /></label>
      <label>Role for signed-in users without a rule<select value={defaultRole} disabled={!isAdmin} onChange={(event) => setDefaultRole(event.target.value)}><option value="">No access</option>{ROLE_NAMES.map((name) => <option key={name}>{name}</option>)}</select></label>
      <div className="settings-save"><span role="status">{saved || (isAdmin ? "Saved to .loro/config.local.toml" : "Only admins can change access rules.")}</span>{isAdmin && <button>Save access rules</button>}</div>
    </form>
  </div>;
}
