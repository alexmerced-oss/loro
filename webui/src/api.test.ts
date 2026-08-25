import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

/**
 * `api.ts` captures the launch token at import time, so each case reloads the
 * module against a freshly staged location and storage.
 */
async function loadApi(search: string) {
  vi.resetModules();
  const replaceState = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: new URL(`http://127.0.0.1:8765/${search}`),
  });
  window.history.replaceState = replaceState;
  const mod = await import("./api");
  return { mod, replaceState };
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("launch token", () => {
  it("stores the token from the launch URL and strips it from the address bar", async () => {
    const { replaceState } = await loadApi("?token=secret-123");
    expect(localStorage.getItem("loro-auth-token")).toBe("secret-123");
    expect(replaceState).toHaveBeenCalled();
    expect(String(replaceState.mock.calls[0][2])).not.toContain("token=");
  });

  it("leaves storage alone when no token is present", async () => {
    await loadApi("");
    expect(localStorage.getItem("loro-auth-token")).toBeNull();
  });

  it("sends the stored token as a bearer credential", async () => {
    await loadApi("?token=secret-123");
    const seen: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: unknown, init?: RequestInit) => {
        seen.push(init ?? {});
        return new Response(JSON.stringify({ csrf_token: "c", workspace: "/w" }), { status: 200 });
      }),
    );
    const { initialize } = await import("./api");
    await initialize();
    const headers = seen[0]?.headers as Record<string, string> | undefined;
    expect(headers?.Authorization).toBe("Bearer secret-123");
  });
});

describe("error messages", () => {
  async function failWith(status: number, body: string): Promise<Error> {
    await loadApi("");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status })));
    const { request } = await import("./api");
    try {
      await request("/api/conversations");
    } catch (error) {
      return error as Error;
    }
    throw new Error(`expected ${status} to reject`);
  }

  // Reading the body as JSON and then again as text threw
  // "body stream already read", masking every real server message.
  it("never reports a body-stream error", async () => {
    const error = await failWith(401, "Authentication required.");
    expect(error.message).not.toContain("body stream");
    expect(error.message).not.toContain("TypeError");
  });

  it("explains an unauthenticated workspace in terms of the launch URL", async () => {
    const error = await failWith(401, "Authentication required.");
    expect(error.message).toContain("launch token");
    expect(error.message).toContain("loro web");
  });

  it("names a rejected origin", async () => {
    const error = await failWith(403, "Origin rejected.");
    expect(error.message).toContain("did not come from this workspace");
  });

  it("names an expired session", async () => {
    const error = await failWith(403, "CSRF validation failed.");
    expect(error.message).toContain("Session expired");
  });

  it("surfaces a JSON detail field", async () => {
    const error = await failWith(404, JSON.stringify({ detail: "Conversation not found." }));
    expect(error.message).toBe("Conversation not found.");
  });

  it("points a server error at the terminal", async () => {
    const error = await failWith(500, "");
    expect(error.message).toContain("loro web");
  });
});
