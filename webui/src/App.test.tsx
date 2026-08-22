import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./api", () => ({
  initialize: async () => ({ workspace: "/workspace/loro" }),
  request: async (path: string) => path === "/api/profiles" ? [] : path === "/api/conversations" ? [] : {},
  streamRun: async () => undefined,
}));

describe("App", () => {
  it("renders the conversation workspace", async () => {
    render(<App />);
    expect(await screen.findByText("Conversations")).toBeInTheDocument();
    expect(screen.getByText("Your local agent workspace")).toBeInTheDocument();
  });
});
