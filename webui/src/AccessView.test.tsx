import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccessView } from "./AccessView";
import { PermissionsContext } from "./permissions";

const ACCESS = {
  mode: "oidc",
  rbac_enabled: true,
  you: { subject: "alex@example.com", roles: ["operator"], permissions: ["read", "operate"] },
  roles: [
    { name: "viewer", permissions: ["read"] },
    { name: "operator", permissions: ["operate", "read"] },
    { name: "approver", permissions: ["approve", "read"] },
    { name: "admin", permissions: ["admin", "approve", "operate", "read"] },
  ],
  permission_labels: { read: "View", operate: "Run", approve: "Approve", admin: "Administer" },
  mappings: { "data-platform": "operator" },
  admins: ["root@example.com"],
  default_role: null,
  accept_role_names: true,
};

vi.mock("./api", () => ({ request: async () => ACCESS }));

describe("AccessView", () => {
  afterEach(cleanup);

  it("shows your roles and lets admins edit rules", async () => {
    render(<PermissionsContext.Provider value={["read", "operate", "approve", "admin"]}><AccessView setError={() => undefined} /></PermissionsContext.Provider>);
    expect(await screen.findByText("alex@example.com")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Role permissions" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("data-platform")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save access rules" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove rule data-platform" })).toBeInTheDocument();
  });

  it("is read-only for non-admins", async () => {
    render(<PermissionsContext.Provider value={["read", "operate"]}><AccessView setError={() => undefined} /></PermissionsContext.Provider>);
    expect(await screen.findByDisplayValue("data-platform")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save access rules" })).not.toBeInTheDocument();
    expect(screen.getByText("Only admins can change access rules.")).toBeInTheDocument();
  });
});
