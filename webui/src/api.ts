let csrfToken = "";

function headers(json = false): HeadersInit {
  const result: Record<string, string> = {};
  if (json) result["Content-Type"] = "application/json";
  if (csrfToken) result["X-Loro-CSRF"] = csrfToken;
  const auth = localStorage.getItem("loro-auth-token");
  if (auth) result.Authorization = `Bearer ${auth}`;
  return result;
}

async function responseError(response: Response): Promise<Error> {
  let message = `${response.status} ${response.statusText}`;
  try {
    const value = await response.json();
    message = value.detail || message;
  } catch {
    const text = await response.text();
    if (text) message = text;
  }
  return new Error(message);
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
