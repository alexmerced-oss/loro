let csrfToken = "";

const AUTH_STORAGE_KEY = "loro-auth-token";

/**
 * `loro web` mints a per-launch token and opens the browser at `/?token=...`.
 * Capture it once, keep it for subsequent requests, and strip it from the
 * address bar so it does not survive in history, bookmarks, or a shared
 * screenshot.
 */
function captureLaunchToken(): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");
  if (!token) return;
  localStorage.setItem(AUTH_STORAGE_KEY, token);
  url.searchParams.delete("token");
  window.history.replaceState({}, "", url.pathname + url.search + url.hash);
}

captureLaunchToken();

function headers(json = false): HeadersInit {
  const result: Record<string, string> = {};
  if (json) result["Content-Type"] = "application/json";
  if (csrfToken) result["X-Loro-CSRF"] = csrfToken;
  const auth = localStorage.getItem(AUTH_STORAGE_KEY);
  if (auth) result.Authorization = `Bearer ${auth}`;
  return result;
}

async function responseError(response: Response): Promise<Error> {
  // The body can only be consumed once. Reading it as JSON and then falling
  // back to text() threw "body stream already read", which replaced every
  // real server message with a browser TypeError.
  let body = "";
  try {
    body = await response.text();
  } catch {
    body = "";
  }

  let detail = body.trim();
  if (detail.startsWith("{")) {
    try {
      const parsed = JSON.parse(detail);
      detail = String(parsed.detail || parsed.error || detail);
    } catch {
      /* not JSON after all; keep the raw text */
    }
  }

  // Named states for the cases a user can actually act on.
  if (response.status === 401) {
    return new Error(
      "This workspace needs the launch token. Reopen the URL that `loro web` printed, " +
        "or restart it to mint a new one.",
    );
  }
  if (response.status === 403) {
    return new Error(
      detail.toLowerCase().includes("origin")
        ? "Request blocked: it did not come from this workspace's own page."
        : "Session expired. Reload the page to start a new one.",
    );
  }
  if (response.status === 404) return new Error(detail || "That record no longer exists.");
  if (response.status === 409) return new Error(detail || "Someone else changed this first. Reload and try again.");
  if (response.status >= 500) {
    return new Error(detail || "Loro hit an internal error. Check the terminal running `loro web`.");
  }
  return new Error(detail || `${response.status} ${response.statusText}`);
}

export async function initialize(): Promise<{ workspace: string }> {
  const response = await fetch("/api/session", { headers: headers() });
  if (!response.ok) throw await responseError(response);
  const value = await response.json();
  csrfToken = value.csrf_token;
  return value;
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: { ...headers(Boolean(options.body)), ...(options.headers || {}) },
  });
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return undefined as T;
  return response.json();
}

export async function streamRun(
  runId: string,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const response = await fetch(`/api/runs/${runId}/events`, { headers: headers() });
  if (!response.ok) throw await responseError(response);
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Streaming is unavailable in this browser.");
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = block.match(/^event: (.+)$/m)?.[1];
      const data = block.match(/^data: (.+)$/m)?.[1];
      if (event && data) onEvent(event, JSON.parse(data));
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}
