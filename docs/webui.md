# Local Web UI

Loro includes an optional, local-first Web UI for durable conversations, profile-backed bots,
profile management, and workspace defaults. It is an adapter over the existing Loro runtime: model
calls, tools, permissions, approvals, sandboxing, data protection, memory, profiles, sessions, and
audit behavior remain authoritative.

## Install And Start

Install the optional server dependencies:

```bash
python -m pip install "loro-agent[webui]"
```

Run the UI from the project folder Loro should operate on:

```bash
loro web
loro web --port=8765 --no-open
loro web doctor
```

`loro web` binds to `127.0.0.1` by default and opens the application in the default browser. The
process runs only while the command is active; installing the extra does not create a daemon or
enable remote access.

Every launch mints a fresh access token and prints it as part of the URL:

```text
Loro Web UI: http://127.0.0.1:8765/?token=<per-launch-token>
```

The browser stores that token for the origin and removes it from the address bar, so it does not
survive in history, bookmarks, or a screenshot. Opening the bare address without a token shows an
explanation rather than an empty workspace. The token is not persisted by the server: restarting
`loro web` invalidates the previous one, and any tab still holding it will ask you to reopen the
printed URL.

## Conversations

The Chat view provides multiple durable conversations. Each conversation has an append-only
message transcript and retains the associated Loro runtime session id. Conversations can be
renamed, archived, or deleted. The first prompt supplies an automatic title that can be renamed
later.

The Web UI stores its transcript separately from the existing session summary format. This keeps
the CLI session contract backward compatible while allowing a traditional multi-turn chat. Up to
40 recent user and assistant messages, bounded to 50,000 UTF-8 bytes, are supplied as explicitly
untrusted context on the next turn. This context cannot grant authority or bypass policy.

The default database is `.loro/webui.sqlite3`. Schema version 1 contains:

- conversation identity, title, status, workspace, profile pin, and runtime session id;
- append-only user, assistant, tool, and system-event messages;
- run status, provider/model route, usage, stop reason, and error metadata.

SQLite foreign keys, bounded input, parameterized queries, and transactional commits are enabled.
The database can be overridden for an isolated deployment:

```bash
loro web --database=/approved/path/loro-webui.sqlite3
```

## Bots

Every discovered Open Agent Profile is presented as a bot. The Bots view shows its trust source,
description, effective provider/model route, tool and Skill counts, and managed-policy
adjustments. Starting a bot chat pins the conversation to the profile name, revision, and
authority-stable digest.

Loro refuses to continue a bot conversation after that profile's authority digest changes. Start
a new conversation to adopt the new revision. This prevents an existing conversation from
silently acquiring different authority.

## Profiles

The Profiles view can create project profiles and edit user or project profiles. Managed and
imported profiles are read-only. A save:

1. preserves the profile name;
2. increments the revision;
3. validates the document through the Open Agent Profile model;
4. rejects literal secret material;
5. writes atomically and restores the previous content if validation fails;
6. recomputes the profile and specification digests;
7. displays effective authority after managed-policy narrowing.

The initial form exposes descriptions and role instructions plus the effective model, tools,
Skills, memory stores, and policy adjustments. The API accepts the complete validated profile
document, allowing future form controls to expand without defining another profile format.

## Default Settings

The Settings view can update the default provider, primary model, small model, and default profile.
It writes through Loro's existing configuration writer to `.loro/config.local.toml`. It never
returns credential values. The interface reports only whether an environment or vault credential
reference is configured.

Managed configuration is applied after the local overlay and remains authoritative. When a
managed overlay is present, the UI marks that state and effective profile views show any narrowing
adjustments.

## Streaming, Tools, And Approvals

Each message starts a bounded background run through `AgentRuntime`. The server streams typed
events over Server-Sent Events:

- model and tool start/completion activity;
- assistant token deltas;
- approval requests and their redacted argument preview;
- completion, cancellation, usage, stop reason, and failures.

Ask-gated tools pause in the runtime's trusted approval provider. The user may deny, approve once,
or approve for the current session when managed policy allows session scope. The existing
`ApprovalManager` still creates, binds, consumes, expires, and audits the approval record. Browser
fields and model output do not become approval authority.

Only one run may be active in a conversation. Separate conversations may run concurrently, with a
server ceiling of four. Cancellation interrupts at the next streaming or runtime event boundary.

## Security Model

The default Web UI is a local operator surface, not a production multi-user service.

- Loopback binding is the default.
- Every launch is token-gated, including on loopback. Origin and CSRF checks already stop a hostile
  web page; the token additionally keeps other local processes and other users on a shared machine
  out of the API.
- A non-loopback IP is rejected unless `--auth-token-env` names a non-empty bearer token, which
  then replaces the minted one.
- State-changing requests require a strict same-site session cookie and CSRF header.
- Requests with a mismatched `Origin` are rejected.
- CORS is not enabled.
- CSP, frame denial, no-sniff, and no-referrer headers are applied.
- API message size, database text, history context, and concurrency are bounded.
- Credentials and unrestricted environment data are not exposed.
- Assistant markdown is rendered without a raw-HTML pass. Model output is untrusted because it can
  quote a hostile file, a scraped page, or a tool result, so embedded markup stays visible text,
  links open with `noopener`, and a `javascript:` or `data:` URL never becomes an anchor.
- All consequential actions still pass through Loro permission, approval, sandbox, safety, and
  audit controls.

For explicit non-loopback evaluation:

```bash
export LORO_WEB_TOKEN="use-a-random-secret"
loro web --host=192.0.2.10 --auth-token-env=LORO_WEB_TOKEN --no-open
```

The browser will request the token and keep it in browser local storage for that origin. Use TLS,
an approved reverse proxy, authenticated identity, rate limiting, and deployment review before
exposing the UI beyond a controlled network. The built-in bearer mode is not a replacement for
enterprise identity.

## API Summary

The version-1 local API provides:

- status and CSRF session establishment;
- conversation CRUD, messages, runs, cancellation, SSE, and approval decisions;
- profile list, document, effective authority, create, update, and validation;
- redacted settings reads and constrained default updates.

FastAPI's interactive schema routes are disabled so the local operator UI is the only published
browser surface.

## Development And Verification

The React/TypeScript source lives in `webui/`. The compiled assets are packaged under
`src/loro/webui/static` so installed users do not need Node.js.

```bash
cd webui
npm ci            # exact, lockfile-pinned; `npm install` may drift the pins
npx vitest run
npm run build     # writes ../src/loro/webui/static

cd ..
python -m pip install -e ".[dev,webui]"
pytest -q tests/test_webui.py
ruff check src/loro/webui tests/test_webui.py
loro web doctor
```

The compiled bundle is committed. **Any change under `webui/src` must be rebuilt and the result
committed in the same change**, or the shipped UI silently predates its own source. The `Web UI`
workflow enforces this: it reinstalls from the lockfile, runs the unit tests, rebuilds, and fails
when `src/loro/webui/static` differs from what the current source produces. A second job starts
`loro web` against a temporary workspace and asserts that the page is served and that the API is
token-gated.

Release verification must also build a wheel, inspect that the static HTML/CSS/JavaScript assets
are present, install it into a clean environment with the `webui` extra, and check `/api/status`
plus the root application document.
