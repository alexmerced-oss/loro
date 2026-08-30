import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GraphsView } from "./GraphsView";

const document = {
  ags: "1.0",
  graph: { id: "editable" },
  nodes: {
    research: {
      type: "task",
      title: "Research",
      description: "Collect evidence",
      depends_on: [],
      requirements: { tools: ["file_read"], permissions: ["fs:read:**"] },
    },
  },
};

vi.mock("./api", () => ({
  request: async (path: string) => {
    if (path === "/api/graphs") return [{ path: "work.agraph.yaml", name: "work", size_bytes: 1 }];
    if (path === "/api/profiles") return [{ name: "reviewer" }];
    if (path === "/api/graphs/runs/active") return [];
    if (path.startsWith("/api/graphs/plan")) return {
      ok: true, path: "work.agraph.yaml", graph_id: "editable", title: "Editable", objective: "",
      digest: "sha256:test", nodes: [{ id: "research", title: "Research", type: "task", description: "Collect evidence", depends_on: [], profile: "", tier: "", state: "pending" }],
      gates: [], node_count: 1, max_parallel: 1, worst_case_executions: 1,
      estimated_cost_usd: null, findings: [],
    };
    if (path.startsWith("/api/graphs/document")) return { document };
    return {};
  },
}));

describe("GraphsView", () => {
  afterEach(cleanup);

  it("opens a saved card for editing its tools and permissions", async () => {
    render(<GraphsView setError={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit card" }));

    expect(await screen.findByRole("dialog", { name: "Edit Research" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Required tools/)).toHaveValue("file_read");
    expect(screen.getByLabelText(/Permissions/)).toHaveValue("fs:read:**");
    expect(screen.getByRole("option", { name: "@reviewer" })).toBeInTheDocument();
  });
});
