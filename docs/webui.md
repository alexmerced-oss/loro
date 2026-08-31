# Local Web UI

Loro includes an optional, local-first Web UI for durable conversations, profile-backed bots,
profile management, and workspace defaults. It is an adapter over the existing Loro runtime: model
calls, tools, permissions, approvals, sandboxing, data protection, memory, profiles, sessions, and
audit behavior remain authoritative.

## Runtime Approvals (AAIS)

The Web UI presents protected tool requests from chats, delegated work, and graph agents in one
global modal. It shows the exact action, arguments, resource, risk reasons, digest, and authority-
offered scopes while the originating job remains alive. Loro still owns policy and atomically records
the resolution before releasing a tool. Graph workflow input gates remain graph inputs; authority
decisions use AAIS.

Other presenters can launch `loro run --approval-stdio`. Requests are emitted as
[AAIS 1.0](https://github.com/alexmerced-oss/agent-approval-interchange-spec) NDJSON on standard
output, decisions return on standard input, and logs remain on standard error. The Web API provides
snapshot, cursor-based event, and decision endpoints below `/api/approvals` behind the existing
local-session and CSRF protections.

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

## Appearance And Keyboard

The UI follows the operating system's light or dark setting. The control at the foot of the rail
cycles between matching the system, forcing light, and forcing dark; the choice is stored per
browser and applied before the first paint.

Press `/` for the shortcut sheet. Cmd or Ctrl with `K` focuses the message box, with `Shift+N`
starts a conversation, and with `1` through `4` switches views. An unmodified key never fires while
a text field has focus, so typing `/` in the composer inserts a slash.

Below 680px the conversation list becomes a drawer reached from the header, and closes once a
conversation is chosen.

## Agentic Graphs

The Graphs view lists every `.agraph.yaml`, `.agraph.yml`, and `.agraph.json` in the workspace,
validates the one you select, and shows its plan as a dependency board: node count, maximum
parallelism, worst-case executions, cost ceiling, and the document digest. Invalid graphs report
their findings instead of offering a Run button.

Running goes through the same `GraphExecutor` the CLI drives, so identity, permission policy,
sandbox profiles, budgets, and the audit log all apply unchanged. Cards move between **To do**,
**Current work**, and **Done** as the run progresses, and the event stream is cursor-based, so a
reconnecting browser replays what it missed rather than losing it.

While a run is active, a health panel reports the most recent safe lifecycle event: active card,
model attempt, gate state, or completion status. It does not expose private reasoning, prompts,
credentials, tool arguments, or raw intermediate output.

Cards are a Kanban: every node starts in **Pending**, moves to **In progress** while the executor
works it, and lands in **Complete** when it finishes, whichever way it finished.

There are three ways to get a graph onto the board:

- **Load a file.** Any `.agraph.yaml`, `.agraph.yml`, or `.agraph.json` in the workspace.
- **Start blank.** One card to begin with, then *Add card* appends more, each chained onto the last
  so the board stays a connected DAG. Nothing is written until you name the file and save; an
  invalid draft is refused rather than persisted.
- **Generate from a goal.** Describe the outcome and the configured model drafts the workflow. This
  runs the same pipeline as `loro graph generate`, so the model is prompted with the bundled
  `agentic-graph` skill's contract, the managed step ceiling applies, and an invalid draft gets one
  correction round. The model returns a workflow draft that Loro compiles into a governed graph; it
  never hands back an AGS document directly. `--no-ai` deterministic generation needs no provider
  and conservatively declares capabilities inferred from the goal, including research network and
  implementation write access where applicable.

*Export* downloads the current graph, saved or draft, as a JSON document you can keep, share, or
commit.

Every card has an editor before execution. It can change the title and instructions, choose a
profile, select dependencies, and declare the logical tools and portable permissions the node may
request. Editing a saved graph first creates an unsaved browser draft; **Save graph** then runs the
normal validation and workspace-confined write path. This keeps an undeclared-tool failure
recoverable from the board without letting the editor grant authority beyond Loro's effective
profile and managed policy.

Human gates and graph-card tool approvals both participate in browser execution. A gate pauses the
run and waits for an explicit Approve or Reject rather than being auto-approved; a protected tool
action shows its redacted action, target, and arguments through the same reconnectable prompt. The
graph worker never falls back to a terminal question. Parallel requests are serialized so one card
cannot replace another card's pending decision. An unanswered request times out after thirty minutes
so it cannot pin a worker indefinitely. The Run center includes graph requests in its awaiting-
approval count. Discovery is bounded to four directory levels and skips dependency and VCS
directories, and every path is confined to the workspace.

## Conversations

The Chat view provides multiple durable conversations. Each conversation has an append-only
message transcript and retains the associated Loro runtime session id. Conversations can be
renamed, archived, or deleted. The first prompt supplies an automatic title that can be renamed
later.

Conversation creation pins profiles and group participants before the first message, and deletion
requires confirmation. A running `loro web` process remains intentionally confined to the project
folder it was launched for. The Workspaces view identifies adjacent Loro projects and provides the
exact `loro web -C <path>` launch command; it does not silently widen one server's filesystem
authority to another project.

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
imported profiles are read-only. Editable profiles can also be deleted after explicit confirmation.
**Generate profile** accepts a natural-language purpose, lets the configured planning model choose
only catalogued capabilities, compiles canonical OAP, and shows the validated draft before the
user creates it. Generation never writes a profile by itself.
A save:

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

## First Run

Loro is configured per folder, and the browser previously assumed that had already happened: open
`loro web` in a fresh project and the first message failed with a provider error, having never said
that a provider was not chosen.

The boot sequence now asks `/api/onboarding/readiness` before rendering the workspace. When the
folder cannot run a turn, the setup panel replaces the shell rather than presenting a composer that
is guaranteed to fail. It reports the same four checks `loro get-started` does:

| Step | Blocking | Meaning |
| --- | --- | --- |
| Project configuration | yes | Whether `.loro/config.local.toml` exists |
| Model | no | The provider and model that would be used, flagged when offline |
| Credential | yes | Whether a key was found, and which variable was searched |
| Default agent profile | no | Optional; a role for bots, not a gate on the first message |

A provider and model can be chosen in the panel, which writes only the route into the project
configuration. **Credentials are never accepted through the form.** A key typed into a page ends up
in a file on disk, so keys stay in the environment or the OS keyring and the panel reports only
whether one was found and which variable it expects. `Try it offline first` selects the `mock`
provider, which answers without any credential, so the whole loop can be seen before a key is
found.

## Reconnecting To A Run

This applies to both chat replies and graph runs. Each executes on its own thread and records its
result whether or not a browser is watching, and each streams an append-only event log from a
cursor, so losing the connection loses the *view* of a turn rather than the turn.

### Graph Runs


A graph run outlives the page watching it, and the event stream is cursor-based so that a
reconnecting browser can ask for exactly what it has not seen. That was only half-built: the client
requested `after=-1` every time and never tracked the cursor, and its `onerror` handler closed the
stream and cleared the run. A dropped connection therefore blanked the board while the run carried
on server-side, and the comment claiming otherwise was wrong.

The client now records each event's sequence, delivered as the SSE `id`, and resumes from it. A
dropped connection reconnects up to five times before giving up and saying the connection was lost
rather than leaving the board reconnecting forever.

Reloading the page is the other way to lose a run, and a reloaded page knows nothing about the run
it lost. `GET /api/graphs/runs/active` lists the runs this server still holds in memory, distinct
from `GET /api/graphs/runs`, which reads persisted history: a finished record is not something you
can reattach to. On mount the Graphs view adopts any run still in progress and replays it from the
beginning, because the board has no state from a run it never watched. A run waiting on an approval
is reported as such, and replaying its `gate.requested` event re-renders the prompt, so a reattached
run is not left stuck behind a gate nobody can answer.

### Chat Replies

The chat stream was cursor-capable on the server and the client ignored it: it never sent `after`,
never reconnected, and could not find its run after a reload. Reloading mid-reply therefore lost the
live view of a reply that was still arriving, and the transcript only caught up once the run
finished and the conversation was refetched.

The client now tracks each event's sequence and resumes from it, retrying a bounded number of times
before reporting the connection lost. `GET /api/conversations/{id}/active-run` reports the run a
conversation is still executing, so a reloaded page finds the reply it lost; only an unfinished run
is offered, because a finished one already wrote its message and replaying it would show the answer
twice. An approval still awaiting a decision is reported in the snapshot, so a reattached run is not
left waiting on a question nobody can see.

Run handles are kept for reattachment, not as history, and nothing ever removed them: a long
session accumulated every run it had executed along with that run's whole event log. Finished
handles are now evicted past a cap, and a run still going is never evicted however old it is.

## Memory

Loro keeps three related things: local memories written for this workspace, a queue of proposals the
agent has raised for a human to decide, and governed shared memory behind a Postgres or Iceberg
backend. None of it was reachable from the browser, so the memory shaping every reply was invisible
and the proposal queue could only be reviewed from a terminal.

**Proposals** is the review queue and the only screen here that changes anything. Accepting a local
proposal writes a local memory; accepting a shared one stages a shared-memory draft rather than
committing it, because committing to a governed backend is a separate, deliberate step, and the
result says so and names the command. Both use the same defaults `loro memory accept-proposal` uses,
so accepting here and accepting there put a draft in the same place.

**Declining is new.** There was no way to decline a proposal anywhere: the CLI only accepts, so the
queue could only grow and a proposal you did not want stayed pending forever. Declining writes
nothing to memory. Every decision, either way, is written to the audit record.

Deciding a proposal twice is refused by name rather than silently applied, because two open tabs is
a race and not a fault, and each proposal reports whether it can still be decided so the UI does not
offer buttons that would be rejected.

**Local memories** lists and searches what this workspace remembers, newest first, bounded and
reporting when the list was cut. The same view can create, edit, and delete local records, with
confirmation before deletion and the normal data-protection checks on every write. **Shared
memory** searches the governed backend and shows the
citation each record carries, which is how a shared memory is referred to elsewhere. An unreachable
backend explains itself instead of rendering as an empty result.

## Settings Choices

Provider, primary-model, small-model, and default-profile fields use the discovered provider
catalog and profile roster instead of accepting arbitrary spelling. Changing provider selects its
catalog defaults; credentials remain outside the browser in the environment or credential store.

## Accessibility

An audit pass over every view in both themes turned up four defects.

**Contrast.** Small muted text sat between 2.6:1 and 4.4:1 against its actual background, below the
4.5:1 AA threshold for text that size, across roughly twenty distinct labels. The tokens were
recomputed as the nearest value along the lightness axis that clears the threshold on the
least-contrasting surface each is used on, in both themes.

**Chips rendered as browser defaults in dark mode.** A `<button class="chip">` set no background, so
it fell back to the user agent's grey, which does not follow the theme: light grey text on light
grey at 2.6:1. The background is now explicit.

**Fields lost their focus ring.** Inputs set `outline:0` for their resting state at a specificity
that also defeated `:focus-visible`, so a keyboard user could not tell which field was focused.
Buttons were unaffected; only fields were.

**Chips were 18px tall,** below the 24px minimum for a pointer target.

The audit is re-run against a live server rather than asserted in unit tests, because the properties
that matter here are computed styles against real backgrounds.

## Governance

The Governance view is the evidence surface, and it is entirely read-only.

**Posture** reports who you resolve as, the tenant and roles, the runtime budgets, whether the
sandbox profile is enforced, the approval mode, and where the audit record is written.

**Policy** evaluates a permission request and shows the decision, the reason, the policy version and
source, and which rule matched. It is `loro policy explain` in the browser: the rules are evaluated
against a hypothetical request and nothing is executed.

**Audit** lists recent events newest first, with counts by event type you can filter on, and a
*Verify chain* control that recomputes the SHA-256 hash chain and reports whether every event still
hashes onto its predecessor. Only the JSONL sink can be verified locally; another sink says so
rather than pretending. The event window is bounded, because a ledger grows without limit and the
browser only ever shows part of it.

## Bots And Group Conversations

The Bots view is a roster of every profile you can talk to, each showing the model route it will
actually use, its tool and skill counts, and any managed policy adjustments. Chat with one on its
own, or tick two to five and start a group.

In a group every participant speaks once per turn, in the order you picked them, and each one reads
what the earlier speakers just said, so the result is a conversation rather than parallel
monologues. Replies are attributed to the profile that produced them, and tool activity carries the
same attribution.

Each participant's spec digest is pinned when the group is created. A profile that changes after
the conversation starts is refused rather than quietly speaking with different authority, exactly
as for a single-profile bot. Group members also run with a fresh session each turn and read the
transcript instead, so one member's hidden context never leaks into another's.

Groups offer three explicit execution modes. Sequential mode preserves deterministic handoff and
lets later speakers see earlier findings. Parallel mode gives every participant the same starting
context and runs them concurrently under the existing global concurrency and approval limits.
Coordinator mode runs the non-coordinator participants first, then asks the selected coordinator
to synthesize their attributed findings. Parallel approval requests are queued independently in
the chat rather than replacing one another.

## Workspace Context And Artifacts

The composer accepts workspace-relative file references and bounded browser uploads. Up to twenty
references may accompany a message. UTF-8 text-like files up to 256 KB are embedded in an
untrusted context envelope, with a 750 KB total inline ceiling. Images, binary files, and larger
documents remain explicit workspace paths for tools that are permitted to read them. Uploads are
limited to 10 MB and stored beneath `.loro/attachments/<conversation-id>`.

The Workspace view lists reviewable files, previews bounded text/images/PDFs, supports authenticated
downloads, and presents read-only Git status, staged diff, and unstaged diff. Paths are resolved
beneath the active project; dependency/build trees and private `.loro` state are excluded. The
project selector identifies adjacent initialized Loro workspaces and copies an exact launch command
for switching, keeping each server instance and policy boundary tied to one project root.

## Run Center, Schedules, And Notifications

The Run center combines durable conversation records, live conversation handles, graph history,
active graph handles, outstanding approvals, usage, and schedules. It polls for state changes and
can opt into browser-native completion notifications. Notification permission is requested only
from the operator's explicit action and the preference remains in local browser storage.

Graph schedules use bounded minute intervals and persist atomically in
`.loro/webui-schedules.json`. A scheduler executes due graphs only while the local Web UI server is
running, through the same `GraphService` validation, policy, budget, gate, and audit path as a
manual graph run. Failures are recorded on the schedule rather than weakening policy or retrying
without a fresh interval.

## Extension Inventory

The Extensions view shows the effective MCP enablement state, configured servers and transports,
protocol extensions, and discovered managed/user/project skills. It deliberately exposes no
credential values and does not bypass configuration-file review for installing or changing an
extension.

## Portable Identities

A profile can be exported as a portable Open Agent Profile document and imported into another
workspace. Export deliberately strips runtime state and revision history: an exported profile is an
identity to share, not a snapshot of one machine's session. Import applies the same validation as
creation, drops any inbound state or history, and starts the receiving workspace's revision history
at 1, so a shared profile cannot carry another workspace's learned claims into this one.

Each profile declares its own provider and model. The Profiles list shows the *effective* route
after managed resolution, which is what the profile will actually use; the declared route stays in
the document and travels with an export.

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
- Workspace context, uploads, previews, Git output, file discovery, and scheduler intervals are
  independently bounded. Internal Loro state cannot be attached or previewed except for the
  dedicated attachment directory.
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
- bounded context uploads, file/artifact previews, read-only change review, and workspace launch
  metadata;
- unified chat/graph run-center data, interval graph schedules, and extension inventory;
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

## Durable authoring and extension management

Graph and profile generation show elapsed health feedback. Graph and profile screens stay mounted while navigating, preserving the live graph event stream, generation request, and editor state. Unsaved graph documents are additionally cached in browser session storage and restored when the UI is reloaded; discarding or saving removes that cache.

Graph drafting is a server-side background job. The board polls bounded lifecycle states (queued,
authoring, validated, or failed), so navigation does not cancel model work. These messages expose
operational health and validation state, never private model reasoning. Provider credentials entered
during first-run or Settings are written to Loro's OS-keyring-backed vault; project configuration
stores only the credential reference.
After 90 seconds the graph panel labels model authoring as slower than usual and reminds the operator
that Loro's configured model-request timeout remains authoritative.

The Extensions screen can create, edit, and delete project-owned skills and MCP server definitions. MCP setup distinguishes local stdio from Streamable HTTP, offers explicit protocol negotiation modes and timeouts, and uses an environment-variable allowlist for credentials. Managed protocol extensions and managed skills are labeled read-only because project UI authority cannot modify operator policy.
