import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const READY = { ok: true, ready: true, steps: [], blocking: [] };

function stubApi(readiness: Record<string, unknown>) {
  return {
    initialize: async () => ({ workspace: "/workspace/loro" }),
    request: async (path: string) => {
      if (path === "/api/profiles") return [];
      if (path === "/api/conversations") return [];
      if (path === "/api/onboarding/readiness") return readiness;
      if (path === "/api/onboarding/providers") return { providers: [] };
      return {};
    },
    streamRun: async () => undefined,
  };
}

const readiness = { current: READY as Record<string, unknown> };
const session = { current: async (): Promise<Record<string, unknown>> => ({ workspace: "/workspace/loro" }) };
vi.mock("./api", () => {
  class LoginRequiredError extends Error {
    info: Record<string, unknown>;
    constructor(info: Record<string, unknown>) {
      super("Sign in");
      this.info = info;
    }
  }
  return {
    LoginRequiredError,
    initialize: () => session.current(),
    loginHref: () => "/auth/login?next=%2F",
    takeAuthError: () => "",
    signOut: async () => undefined,
    request: async (path: string) => stubApi(readiness.current).request(path),
    streamRun: async () => undefined,
  };
});

describe("App", () => {
  beforeEach(() => {
    readiness.current = READY;
    session.current = async () => ({ workspace: "/workspace/loro" });
  });

  it("asks an OIDC workspace to sign in before loading anything", async () => {
    const api = await import("./api");
    session.current = async () => {
      throw new (api.LoginRequiredError as unknown as new (info: object) => Error)({
        mode: "oidc",
        issuer_name: "login.example.com",
      });
    };
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Sign in to Loro" })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Continue with login.example.com" });
    expect(link).toHaveAttribute("href", "/auth/login?next=%2F");
    expect(screen.queryByText("Conversations")).not.toBeInTheDocument();
  });

  it("shows who is signed in and offers sign out", async () => {
    session.current = async () => ({
      workspace: "/workspace/loro",
      identity: { subject: "alex@example.com", display_name: "Alex Example", tenant: "acme", roles: ["approver"], groups: [], auth_method: "oidc", verified: true },
    });
    render(<App />);
    expect(await screen.findByText("Alex Example")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  // Without this each render stacks in the same document, so a "not present"
  // assertion sees the previous test's markup.
  afterEach(cleanup);

  it("renders the conversation workspace", async () => {
    render(<App />);
    expect(await screen.findByText("Conversations")).toBeInTheDocument();
    expect(screen.getByText("Your local agent workspace")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /runs/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /workspace/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /extensions/i })).toBeInTheDocument();
  });

  it("shows setup instead of a composer when the folder cannot run a turn", async () => {
    readiness.current = { ok: true, ready: false, blocking: ["config"], steps: [] };
    render(<App />);

    expect(await screen.findByText("Set up this folder")).toBeInTheDocument();
    expect(screen.queryByText("Conversations")).not.toBeInTheDocument();
  });

  it("does not hijack the workspace when readiness is unreadable", async () => {
    // Replacing the whole workspace is a strong intervention; an unexpected
    // answer must not trigger it. An earlier version treated any falsy `ready`
    // as a "no", so a malformed 200 sent every user to the setup panel.
    readiness.current = {};
    render(<App />);

    expect(await screen.findByText("Conversations")).toBeInTheDocument();
  });
});
