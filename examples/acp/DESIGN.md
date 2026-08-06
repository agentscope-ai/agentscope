# ACP Agent (stdio) example for AgentScope — design & build plan (examples/acp/)

**Status:** v2 — revised against `main` as of 2026-08-06 and updated to
match the shipped PR1 implementation in this directory. Original RFC
discussed in [#1948](https://github.com/agentscope-ai/agentscope/discussions/1948).

**Scope one-liner:** A fully runnable Agent Client Protocol (ACP)
**Agent role, stdio transport** example under `examples/acp/`, built
only on AgentScope's existing public API — a desktop shell (ACP Client,
e.g. Zed) drives an AgentScope kernel (ACP Agent) as a subprocess.
**No changes to `agentscope/` core.**

> **Changelog v1 → v2 (2026-08-06).** The v1 draft (2026-07-01) was
> written against a late-June snapshot of `main`; core moved
> significantly while it sat: **#1995** shipped first-class graceful
> interruption (the v1 "core gap 1" is closed — §15), **#2001** added
> the `on_check_permission` middleware hook (weakens v1 gaps 2/6),
> **#2117** added a DEFAULT-mode read-only fast path plus batch
> confirmation de-duplication (§12), the workspace grew five new
> sandbox backends (§13), and **#1997** added an app-layer `channel`
> subsystem (§2). Two v1 claims were wrong at writing time and are
> corrected here: Glob never used `find` (§13), and
> `ReActConfig.stop_on_reject` is declared but consumed nowhere (§18).
> The op-id binding mechanism (invariant c) was redesigned after
> implementation showed the v1 ContextVar timing does not survive
> concurrent tool batches (§14). On the ACP side: the SDK pin moved to
> 0.12.0, and the v1 claims about SDK/schema versions are corrected
> (§21 q6, §22).

---

## 0. Header

- **Title:** ACP Agent (stdio) example for AgentScope — design & build plan (examples/acp/)
- **Status:** v2, implemented (PR1) — see §20 for what shipped vs follow-ons
- **Deliverable:** `examples/acp/` — a fixed, out-of-the-box general assistant *with coding capabilities*, exposed to any ACP desktop shell over stdio, isolated behind a single `build_agent()` factory so it is trivially forkable into a template.
- **Non-deliverable (this phase):** any in-tree `agentscope/acp/` module, any core surface-area change, the ACP Client direction, HTTP/WebSocket transport.

---

## 1. Summary & scope

We ship `examples/acp/`: an ACP **Agent** that speaks newline-delimited
JSON-RPC 2.0 over **stdin/stdout**, wrapping an AgentScope
`agentscope.agent.Agent`. A desktop code editor (the ACP **Client**)
spawns the example as a subprocess, calls `initialize` / `session/new` /
`session/prompt`, and receives streamed `session/update` notifications
produced by translating AgentScope's `AgentEvent` stream (from
`Agent.reply_stream`).

**In scope (Phase 1, implemented):**
- ACP **Agent** role only.
- **stdio** transport only.
- The event→protocol mapping built on public API: `Agent`,
  `reply_stream` → `AgentEvent` union, and the AgentEvent→protocol
  *conversion pattern* (the same idea the AG-UI middleware uses,
  re-implemented for stdio).
- Default **shell delegation** of filesystem *and* terminal (client owns
  the workspace) via a single client-delegating backend, with an
  **opt-in** AgentScope Workspace sandbox mode.
- Receipt-ready event invariants (stable ids, capability snapshot,
  operation ids, permission binding, terminal taxonomy) as PR1
  *acceptance criteria*, enforced by the test suite (§19).

**Deferred / out of scope:**
- The ACP **Client** direction (AgentScope driving Claude Code / Codex
  as sub-agents).
- **HTTP / WebSocket** transport. The transport is an in-progress RFD;
  the Python SDK ships an RFD-based implementation as of 0.12.0, but
  stdio remains the only stable transport and this example stays
  stdio-only.
- Any promotion into an in-tree SDK — reassessed in Phase 2.
- **No changes to `agentscope/` core.** Everything below is composed
  from already-public symbols; remaining friction points are flagged as
  *core gaps to raise separately* (§15), not patched here.

---

## 2. Background & motivation

**The desktop kernel↔shell seam.** ACP factors an agentic coding
session into two roles: the **Client** (the editor/IDE — owns the UI,
the open buffers, the local filesystem and terminal) and the **Agent**
(the model-driven kernel — owns planning, tool intent, and the streamed
narration of a turn). They talk JSON-RPC over stdio. AgentScope already
*is* a kernel: `Agent.reply_stream` emits a structured `AgentEvent`
stream that describes exactly what a turn is doing. ACP is the
desktop-native presentation of that stream.

**Adapters over one core.** AgentScope now has two in-tree adapter
families over the AgentEvent core: the **AG-UI** middleware
(`app/middleware/_protocol/_agui.py`, `AgentEvent` → AG-UI SSE for
web/HTTP front-ends) and the **channel** subsystem
(`app/channel/`, #1997 — long-lived IM connectors such as Feishu and
Discord, built on the service stack's gateway/message-bus). ACP is a
third presentation of the same stream — the desktop/stdio case:

```
              AgentScope Agent.reply_stream  ──▶  AgentEvent stream
                    │                 │                    │
        (web / HTTP)│    (IM platforms)│     (desktop / stdio)
                    ▼                 ▼                    ▼
          AG-UI SSE middleware   app.channel        examples/acp/
          (service extra)        (service stack)    (this example)
```

Neither in-tree adapter fits the ACP seam: the AG-UI middleware is
HTTP-bound (Starlette `BaseHTTPMiddleware`, behind the `service`
extra), and channels are service-layer constructs (gateway, message
bus, storage, the `channel` extra) for persistent platform
connections — not a subprocess speaking JSON-RPC on stdio. The example
builds its **own** stdio peer (from the ACP SDK) and reuses only the
*mapping idea* and the discriminated-union deserialization pattern.

**The kernel we ship.** A **general assistant with coding
capabilities**: an `Agent` with the builtin tools (`Read`, `Write`,
`Edit`, `Bash`, `Grep`, `Glob`) plus a permission engine, wired so
file/terminal operations are *mediated by the shell* by default. It
works against a real ACP client (Zed) out of the box. (Windows-minded
forkers can swap `Bash` for the builtin `PowerShell` tool through the
same `backend=` seam.)

**Why example-first.** ACP's v1 schema line is stable but the protocol
is still moving (v2 exists as a draft); the Python SDK is pre-1.0.
Committing an in-tree module or a core surface to a still-moving
protocol is premature. An example on public API proves which
abstractions matter, ships value now, and is trivially removable if ACP
churns. *Merging proven functionality later is easier than removing
unproven functionality.* (See §3, DavdGao Concern 1.)

---

## 3. Maintainer concerns addressed (explicit)

### Concern 1 (DavdGao) — infrastructure duplication vs the multi-tenant core stack

The worry: an ACP integration might duplicate the multi-tenant core
stack (sessions, storage, service layer) or bake ACP-specific surface
into `agentscope/` while the protocol is still moving.

**Resolution.** `examples/acp/` uses **only public API** and adds
**zero** core surface:
- It does **not** import the service layer (`agentscope.app.*`) or its
  extras (FastAPI/uvicorn/AG-UI/channel). The stdio peer comes from the
  ACP SDK (§16).
- It does **not** reintroduce a multi-tenant session manager. The
  library's entire public "session" concept is `AgentState.session_id`;
  the example keeps an in-process `dict[SessionId, Session]` and can
  persist with `AgentState.model_dump`/`model_validate` if/when
  `session/load` is added. No Redis, no `agentscope.app.storage`.
- The new `app.channel` subsystem (#1997) confirms the pattern this
  example deliberately avoids duplicating: channels live in the service
  stack precisely because they need its gateway and message bus. The
  stdio example needs neither.
- All required behavior is reachable from public symbols (§15). Where
  friction remains, we **flag it as a gap to raise separately** and
  work around it in the example — we do **not** patch core here.
- **Phase 2** reassesses promotion (in-tree `agentscope/acp/`, a
  separate `agentscope-acp` package, or in-between) *after* the example
  proves the abstractions and ACP stabilizes; the channel subsystem now
  provides an in-tree precedent to judge that promotion against.

### Concern 2 (DavdGao) — kernel positioning / configuration

The worry: should the example ship a fixed agent, or a fully
user-configurable one? A fixed agent risks being a toy; a
fully-configurable one risks being an empty framework with no runnable
default.

**Resolution — IN-BETWEEN.** Ship a **fixed, runnable default agent**
(general assistant with coding capabilities) that works out of the box,
with all agent construction isolated in a single `build_agent()`
factory and model/credentials supplied via **environment variables**.
What the *example author* fixes = the ACP wiring + the default agent.
What is *exposed to the user* = model/creds via env, plus the
`build_agent()` seam a forker edits to swap in their own agent. A
finished demo that is trivially forkable into a template. The explicit
**fixed-vs-exposed** matrix is in §7.

---

## 4. Goals / Non-goals

**Goals**
1. A subprocess that any ACP-compliant desktop client can spawn and
   drive over stdio, demoable against **Zed**.
2. Faithful mapping of a full AgentScope turn (`reply_stream`) onto ACP
   `session/prompt` semantics: streamed text/thinking, tool calls,
   permission prompts, fs/terminal callbacks, stop reason.
3. **Shell-delegation** as the default fs/terminal authority (client
   owns the workspace, sees unsaved buffers).
4. **Receipt-ready invariants** (§14) satisfied so a later, optional
   receipt emitter needs no log-scraping.
5. Zero core changes; a single `build_agent()` seam; env-based config.

**Non-goals**
1. ACP Client role; HTTP/WebSocket transport.
2. Persistent multi-tenant storage / service layer.
3. Full ACP optional-method coverage in PR1 (`session/load`,
   `session/resume`, modes, config options) — *follow-ons within the
   example* (§20).
4. Emitting the actual receipt in PR1 — the emitter is an optional,
   derived, vendor-neutral follow-on. PR1 only *guarantees the
   invariants that make it possible*.

*(The v1 draft listed "a first-class `Agent.interrupt()`" as a
non-goal-slash-core-gap; #1995 closed that gap upstream, so
interruption is now simply part of Goal 2 — see §12, §15, §18.)*

---

## 5. Architecture overview

```
  ┌────────────────────────────┐     stdio (newline-delimited JSON-RPC 2.0)    ┌──────────────────────────────────────────────┐
  │      Desktop shell         │ <──────────────────────────────────────────>  │     examples/acp/ kernel (ACP Agent)         │
  │       (ACP Client)         │  client→agent: initialize, session/new,       │                                              │
  │   e.g. Zed / any IDE       │    session/prompt, session/cancel             │  ┌────────────────────────────────────────┐  │
  │                            │  agent→client: session/update (notif),        │  │ stdio JSON-RPC peer (acp.run_agent —   │  │
  │  owns: UI, open buffers,   │    session/request_permission,                │  │ SDK; task-per-inbound dispatch)        │  │
  │  local FS + terminal       │    fs/read_text_file, fs/write_text_file,     │  └──────────────────┬─────────────────────┘  │
  └────────────────────────────┘    terminal/*                                 │                     │ Agent-role handlers    │
                                                                               │  ┌──────────────────▼─────────────────────┐  │
                                                                               │  │ session manager (dict[SessionId,       │  │
                                                                               │  │ Session]; AgentState + turn task/guard │  │
                                                                               │  │ + per-session OpRegistry)              │  │
                                                                               │  └──────────────────┬─────────────────────┘  │
                                                                               │                     │ build_agent()          │
                                                                               │  ┌──────────────────▼─────────────────────┐  │
                                                                               │  │ AgentScope Agent (public API)          │  │
                                                                               │  │ reply_stream(inputs) -> AgentEvent     │  │
                                                                               │  └──────────────────┬─────────────────────┘  │
                                                                               │          AgentEvent │ stream                 │
                                                                               │  ┌──────────────────▼─────────────────────┐  │
                                                                               │  │ translate.py AgentEvent → SessionUpdate│  │
                                                                               │  └──────────────────┬─────────────────────┘  │
                                                                               │  ┌──────────────────▼─────────────────────┐  │
                                                                               │  │ bridge.py ClientBackend + permission   │  │
                                                                               │  │ (shell delegation: fs/* + terminal/*;  │  │
                                                                               │  │  OpBindingMiddleware; Workspace opt-in)│  │
                                                                               │  └────────────────────────────────────────┘  │
                                                                               └──────────────────────────────────────────────┘
```

**Internal layers**
1. **stdio JSON-RPC peer** — from the official SDK (`acp.run_agent`
   binds stdin/stdout; §16). The SDK's dispatcher runs one task per
   inbound request/notification, so `session/cancel` and client
   callbacks dispatch concurrently with an open `session/prompt` —
   verified against SDK 0.12.0 source.
2. **Session manager** — maps `SessionId` → an in-process `Session`
   holding the AgentScope `Agent`, its `AgentState`, the capability
   snapshot, the current **turn task + single-active-turn guard**
   (§16, §18), and the per-session **OpRegistry** (§14).
3. **AgentScope Agent** — constructed via `build_agent()` (§7, §16).
   The kernel proper.
4. **AgentEvent → session/update translator** (`translate.py`) —
   a stateful per-session `Translator` mapping each `AgentEvent` to
   zero or more `session/update` variants (§10).
5. **fs / terminal / permission bridge** (`bridge.py`) — a single
   `ClientBackend(BackendBase)` implementing **all three** backend
   primitives (`read_file`/`write_file` → client `fs/*`; `exec_shell`
   → client `terminal/*`) under shell delegation, or a Workspace
   backend under the opt-in sandbox mode; the `OpBindingMiddleware`
   that binds backend calls to their enclosing tool call (§14 c); and
   the `RequireUserConfirmEvent` ↔ `session/request_permission`
   conversion (§12).

**Built only on public API.** Nothing here subclasses or imports
core-internal machinery. The one deliberate reuse from AG-UI is
*conceptual*: the event→protocol mapping shape. (The v1 draft also
planned to reuse the `TypeAdapter` discriminated-union deserialization;
in-process the translator receives typed `AgentEvent` objects directly,
so no deserialization is needed at all.)

---

## 6. examples/acp/ file layout

The runnable package is named **`acp_example`** — it cannot be named
`acp` because that is the SDK's import name.

```
examples/acp/
├── DESIGN.md            # this document
├── README.md            # what it is; how to run; Zed setup; fork guide
├── requirements.txt     # agent-client-protocol==0.12.0 ; agentscope ; pytest
├── pytest.ini
├── acp_example/
│   ├── __init__.py
│   ├── __main__.py      # `python -m acp_example` — serve over stdio
│   ├── server.py        # Agent-role handlers (initialize / new_session /
│   │                    #   prompt / cancel); child turn task + stop reason
│   ├── agent.py         # build_agent() — the single fixed-vs-exposed seam (§7)
│   ├── session.py       # Session + SessionManager; capability snapshot;
│   │                    #   single-active-turn guard
│   ├── translate.py     # AgentEvent -> SessionUpdate mapping (§10, §11)
│   ├── bridge.py        # ClientBackend (fs/* + terminal/*), OpRegistry +
│   │                    #   OpBindingMiddleware (§14c), permission bridge (§12)
│   └── config.py        # env parsing: provider/creds, authority mode, name
└── tests/               # §19 — mock-client harness + unit tests
    ├── conftest.py
    ├── mock_client.py   # scripted ACP client: fs from a temp dir,
    │                    #   terminals backed by real subprocesses
    ├── mock_model.py    # scripted ChatModelBase (mirrors tests/utils.py)
    ├── test_translate.py
    ├── test_server.py
    └── test_stdio_rpc.py  # wire-level smoke test over a socket pair
```

Optional follow-on files (later PRs within the example, §20):
`receipt.py` (derived receipt emitter), `persist.py` (`session/load`
via `AgentState.model_dump`).

---

## 7. Positioning & configuration (Concern 2 resolved)

The example is **IN-BETWEEN**: a fixed runnable default, with agent
construction isolated in `build_agent()` and model/creds via env.

- **The fixed default agent** — a general assistant with coding
  capabilities: an `agentscope.agent.Agent` named `"agentscope-acp"`, a
  coding-oriented `system_prompt`, a `Toolkit` carrying
  `Read`/`Write`/`Edit`/`Bash`/`Grep`/`Glob`, and a
  `PermissionMode.DEFAULT` permission posture.
- **`build_agent()` factory** — the *single* place agent construction
  lives (`agent.py`). It reads `config.py` (env), picks the model,
  builds the `Toolkit` (with the client-delegating backend by default,
  gated on client capabilities; or a Workspace backend when the sandbox
  env flag is set), and returns a constructed `Agent`. A forker edits
  this one function.
- **Env vars** — model provider + credentials + a few knobs; nothing
  agent-structural is hard-coded to a vendor.

### Fixed-vs-exposed matrix

| Concern | FIXED by the example author | EXPOSED to the user |
|---|---|---|
| ACP wiring (peer, routing, lifecycle) | Yes — `server.py`, `session.py` | No |
| AgentEvent→session/update mapping | Yes — `translate.py` | No |
| fs/terminal/permission bridge semantics | Yes — `bridge.py` (single `ClientBackend`) | Authority **mode** toggle only (env: `ACP_AUTHORITY=shell` \| `workspace`) |
| The default agent (name, system prompt, tool set, permission posture) | Yes — default inside `build_agent()` | Overridable by editing `build_agent()` (fork seam) |
| Model provider & class | Default provided (`dashscope`/`qwen-max`) | `ACP_MODEL_PROVIDER`, `ACP_MODEL_NAME` (env) |
| Credentials / API base | — | `*_API_KEY`, `*_BASE_URL` (env) — never committed |
| Permission mode / rules | Default `PermissionMode.DEFAULT` | `ACP_PERMISSION_MODE` (env) → `AgentState.permission_context.mode`; rules editable in `build_agent()` |
| Workspace sandbox backend | Off by default; `local` wired | Opt-in via `ACP_AUTHORITY=workspace` + `ACP_WORKSPACE_BACKEND`; other backends wired in `build_agent()` |
| Advertised capabilities | Yes — minimal (§8) | Indirect (follows authority mode) |
| Tool gating on absent client caps | Yes — `build_agent()` omits tools whose channel is unavailable (§8, §13) | Indirect (follows client `fs`/`terminal` caps) |

**How a forker turns the example into a template.** (1)
`cp -r examples/acp/ my-acp-agent/`. (2) Edit **only** `build_agent()`
— swap the model, system prompt, tool set, permission rules, or drop in
a custom `Agent` subclass. (3) Leave
`server.py`/`session.py`/`translate.py`/`bridge.py` untouched (the ACP
plumbing is generic). (4) Set env, point Zed at
`python -m acp_example`. Because the ACP surface never leaks into
`build_agent()`, forking is a one-function edit.

---

## 8. Protocol surface

Target **protocol version `1`** (the stable v1 schema line; the pinned
SDK 0.12.0 bundles schema v1.19.0). `acp.PROTOCOL_VERSION = 1`.

### ACP Agent methods implemented (Client → Agent)

| Method | Kind | PR1? | Notes |
|---|---|---|---|
| `initialize` | request | **PR1** | Baseline. Returns `agentCapabilities`, `agentInfo`, `protocolVersion`. |
| `session/new` | request | **PR1** | Baseline. Requires absolute `cwd`; returns `sessionId`. `mcpServers` accepted but not yet wired (§20). |
| `session/prompt` | request | **PR1** | Baseline. Drives one `reply_stream` turn; returns `stopReason`. One active turn per session (§16, §18). |
| `session/cancel` | notification | **PR1** | Baseline. Cancels the in-flight child turn task (§13, §16). |
| `authenticate` | request | PR1 (no-op) | Local posture: advertise no `authMethods` ⇒ never called; implemented as a defensive no-op. |
| `session/load` | request | Follow-on | Gated by `agentCapabilities.loadSession`; needs history replay (§9, §20). |
| `session/set_mode` / `session/set_config_option` | request | Follow-on | Only if we expose `modes`/`configOptions`. |
| `session/resume` / `session/close` / `session/list` / `session/delete` / `session/fork` | request | Deferred | Gated by respective `sessionCapabilities.*`; not advertised in PR1. |

### ACP Client methods called (Agent → Client)

| Method | Kind | PR1? | Gate we require the client to advertise |
|---|---|---|---|
| `session/update` | notification | **PR1** | Baseline (client must accept). |
| `session/request_permission` | request | **PR1** | Baseline (client must implement). |
| `fs/read_text_file` | request | **PR1** (shell mode) | `clientCapabilities.fs.readTextFile` |
| `fs/write_text_file` | request | **PR1** (shell mode) | `clientCapabilities.fs.writeTextFile` |
| `terminal/create` / `terminal/output` / `terminal/wait_for_exit` / `terminal/kill` / `terminal/release` | request | **PR1** (shell mode) | `clientCapabilities.terminal` — **required in shell mode**: the builtin file tools call `exec_shell` for existence/dir/mkdir/search checks, so fs delegation depends on `terminal/*` too (§13). |

### Capabilities advertised at `initialize`

PR1 advertises a **minimal** `AgentCapabilities` (the SDK defaults):
- `loadSession: false` (flips to `true` when `session/load` lands).
- `promptCapabilities`: `{image: false, audio: false, embeddedContext:
  false}` — we accept baseline `text` and `resource_link` only. (Bump
  once those are forwarded to the model.)
- `mcpCapabilities`: `{http: false, sse: false}` (stdio MCP is baseline
  and needs no capability — but is not wired yet, §20).
- No `authMethods`; `agentInfo: Implementation(name="agentscope-acp",
  title="AgentScope ACP Agent", version=...)`.

The example **reads** `clientCapabilities` from the `initialize`
request and stores the snapshot (§14 invariant b). Shell mode requires
**both** `fs.readTextFile`/`fs.writeTextFile` **and** `terminal`:
- If `fs.*` is absent, `Read`/`Write`/`Edit` are **omitted** in shell
  mode (§13).
- If `terminal` is absent, no client-delegated tool can run at all —
  all are omitted until the user opts into Workspace mode (§13). Never
  call a client method whose capability is false.

---

## 9. Session lifecycle mapping

| ACP step | AgentScope mapping |
|---|---|
| `initialize` | Advertise `agentCapabilities` (§8); **snapshot** `clientCapabilities` (invariant b in §14). |
| `session/new` (`cwd`) | Mint a `Session`: construct a fresh `AgentState()` and call `build_agent(cwd=..., state=..., conn=..., caps=..., ops=..., config=...)`. **The ACP `sessionId` = `AgentState.session_id`** so one stable id is shared end-to-end (invariant a). `cwd` must be absolute (rejected otherwise) and becomes the tools' working directory. |
| `session/prompt` (`prompt: ContentBlock[]`) | Translate prompt blocks → a `UserMsg` (§11), then run **one turn** = `async for event in agent.reply_stream(inputs=user_msg)` inside a **child `asyncio.Task`** the handler awaits (§16). Each `AgentEvent` → zero or more `session/update`s (§10). A second `session/prompt` for a busy `sessionId` is **rejected** (§18). The request stays open for the whole turn. |
| `session/cancel` (`{sessionId}`) | Cancel the **child turn task** (`sess.turn_task.cancel()`, never the request handler; §16). Since #1995 the core converts this into a *graceful* ending: unfinished tool calls close with `INTERRUPTED` results and the stream ends with `ReplyEndEvent(finished_reason=INTERRUPTED)` → `stopReason: "cancelled"`. |
| stop-reason | Derived from **`ReplyEndEvent.finished_reason`** (table below). |
| multi-session per connection | The single stdio connection holds `dict[SessionId, Session]`; each has its own `Agent`/`AgentState` and its own single-turn guard. The SDK's task-per-inbound receive loop lets sessions, turns and client callbacks interleave. |
| `authenticate` | **Local no-op posture.** No `authMethods` advertised ⇒ the client creates sessions without auth; the handler is a defensive no-op. |

### Stop-reason mapping

`ReplyEndEvent` is **always** the terminal event of a completed stream
and carries `finished_reason: ReplyFinishedReason` (plus a structured
`error: ErrorInfo | None`). `ExceedMaxItersEvent` still exists but is
an auxiliary signal emitted immediately *before* the terminal
`ReplyEndEvent` — a consumer that tracks "last terminal event wins"
would misreport max-iters turns, so the mapping keys on
`finished_reason` alone:

| `ReplyEndEvent.finished_reason` | `PromptResponse.stopReason` |
|---|---|
| `completed` | `end_turn` |
| `exceed_max_iters` | `max_turn_requests` |
| `interrupted` (task cancel, `UserInterruptEvent`, model-stream interrupt) | `cancelled` |
| `error` | surfaced as a JSON-RPC internal error on the open `session/prompt` (with `ErrorInfo` detail) |
| — (`max_tokens` / `refusal`) | **still unmapped:** AgentScope has no dedicated max-tokens or refusal turn-end signal; PR1 never emits these ACP stop reasons (unchanged gap, §21 q3). |

**Pending-confirmation nuance.** When a turn yields
`RequireUserConfirmEvent`, `reply_stream` *returns* (the generator
completes, with **no** `ReplyEndEvent`) — the reply is *parked*. This
is **not** the end of the ACP turn: the example resolves permission via
`session/request_permission`, feeds the result back with a resume
`reply_stream(inputs=UserConfirmResultEvent(...))`, and only reports
`stopReason` when the *resumed* stream reaches its terminal
`ReplyEndEvent`. If the client answers `{"outcome": "cancelled"}`, the
parked reply is aborted with the public
`reply_stream(inputs=UserInterruptEvent(reply_id=...))` input (#1995),
which closes the ASKING tool calls with `INTERRUPTED` results. See §12.

---

## 10. Event mapping table

Events arrive in-process as typed `AgentEvent` objects (discriminator
`type` if serialization is ever needed). `session/update` variants are
discriminated by `sessionUpdate`; the SDK's model names are
`AgentMessageChunk`, `AgentThoughtChunk`, `ToolCallStart`
(`sessionUpdate: "tool_call"`) and `ToolCallProgress`
(`sessionUpdate: "tool_call_update"`).

| AgentEvent (`type`) | Key fields | ACP mapping | Notes |
|---|---|---|---|
| `ReplyStartEvent` | `session_id`, `reply_id`, `name`, `role` | — | Turn boundary; `reply_id` recorded as the turn id (invariant a). |
| `ReplyEndEvent` | `reply_id`, **`finished_reason`**, **`error`** | — (terminal) | The single terminal signal; drives the stop reason (§9). |
| `ModelCallStartEvent` / `ModelCallEndEvent` | `model_name`; `input_tokens`, `output_tokens`, `finished_reason` | *(none)* | ACP `usage_update` requires *both* `used` (tokens in context) and `size` (total window); AgentScope reports per-call tokens and no window size, so a schema-valid `usage_update` is impossible — omitted entirely (unchanged gap, §21 q4). |
| `TextBlockStartEvent` / `EndEvent` | `block_id` | — | Correlation markers only; chunks are additive in ACP, so a new `messageId` implies a new message and no start/end updates are needed. |
| `TextBlockDeltaEvent` | `block_id`, `delta` | `agent_message_chunk` | One chunk per delta, `messageId = block_id`. |
| `ThinkingBlock{Start,Delta,End}Event` | `block_id`, `delta` | `agent_thought_chunk` (deltas only) | Reasoning stream; `messageId = block_id`. |
| `DataBlock{Start,Delta,End}Event` | `block_id`, `data` (b64), `media_type` | `agent_message_chunk` at **End** | Base64 deltas are buffered until `DATA_BLOCK_END` (partial base64 is not renderable), then emitted as one `image`/`audio` ContentBlock. |
| `HintBlockEvent` | `block_id`, `source`, `hint` | **dropped** | Since #2134 every default-configured agent injects runtime-state hints (time, tasks, context usage) on ordinary turns; rendering them would leak internal system-reminder text into the client UI. *(v1 planned to render hints as agent text — reversed.)* |
| `ToolCallStartEvent` | `tool_call_id`, `tool_call_name` | `tool_call` | `toolCallId = tool_call_id` (the operation id, invariant c), `title`, `kind` (§12), `status: "pending"`. |
| `ToolCallDeltaEvent` | `tool_call_id`, `delta` (JSON fragment) | — (buffered) | ACP has no argument-delta update; fragments accumulate. |
| `ToolCallEndEvent` | `tool_call_id` | `tool_call_update` | Emits the final `rawInput`; registers the pending op for middleware claiming (§14 c). |
| `ToolResultStartEvent` | `tool_call_id`, `tool_call_name` | `tool_call_update` | `status: "in_progress"` — execution began (permission, if any, resolved). |
| `ToolResultTextDeltaEvent` | `tool_call_id`, `delta` | `tool_call_update` | `content` *replaces* the collection, so each update carries the accumulated text. |
| `ToolResultDataDeltaEvent` | `tool_call_id`, `block_id`, `data`, `media_type` | — (buffered) | Flushed into `content` at `TOOL_RESULT_END`. |
| `ToolResultEndEvent` | `tool_call_id`, `state: ToolResultState`, `metadata` | `tool_call_update` | `SUCCESS→completed`; `ERROR`/`INTERRUPTED`/`DENIED→failed`, with the finer taxonomy label (invariant e) in `_meta.agentscope.result_state`. |
| `ExceedMaxItersEvent` | `reply_id` | — | Auxiliary; the following `ReplyEndEvent(finished_reason=exceed_max_iters)` drives the stop reason. |
| `RequireUserConfirmEvent` | `reply_id`, `tool_calls: [ToolCallBlock]` (state `asking`, with `suggested_rules`) | (drives) `session/request_permission` | Not a `session/update`; §12. |
| `RequireExternalExecutionEvent` | `reply_id`, `tool_calls` | unsupported → abort | The fixed tool set has no external tools; if a forker's does, the example logs and aborts the turn via `UserInterruptEvent` (extend the handler for real external execution). |
| `UserConfirmResultEvent` / `ExternalExecutionResultEvent` / **`UserInterruptEvent`** | — | — (resume/abort inputs) | Inputs fed back into `reply_stream`, not outputs. `UserInterruptEvent` (new since #1995) aborts a *parked* reply. |
| `CustomEvent` | `name`, `value` | dropped | No stable ACP mapping; a forker can route these (e.g. onto `plan`). |

**Correlation summary.** Streamed text/thinking/data blocks correlate
by **`block_id`** → ACP `messageId`. Tool call/result lifecycle
correlates by **`tool_call_id`** → ACP `toolCallId` (the operation id).
Turn id = **`reply_id`**; session id = **`session_id`** (= ACP
`sessionId`). No log-scraping needed (§14).

---

## 11. Content model mapping

AgentScope message blocks → ACP `ContentBlock`. ACP `ContentBlock` is
MCP-compatible (`type` discriminator), so MCP tool outputs forward with
minimal transformation.

| AgentScope block | ACP target | Mapping |
|---|---|---|
| `TextBlock(text)` | `TextContent` (`type:"text"`) | `acp.text_block`. Baseline; always allowed. |
| `ThinkingBlock(thinking)` | `TextContent` inside `agent_thought_chunk` | The *update variant* marks it as thinking, not a distinct content type. |
| `ToolCallBlock(name, input, state)` | `ToolCallStart` / `ToolCallProgress` | Not a ContentBlock — the tool-call channel (§10, §12). `input` (JSON string) → `rawInput`. |
| `ToolResultBlock(output, state, metadata)` | `ToolCallContent[]` on `tool_call_update` | text → `Content{type:"content"}`; `state` → `status` + taxonomy. |
| `DataBlock` w/ `Base64Source(data, media_type)` | `ImageContent` / `AudioContent` | By media type; other media degrade to a textual stand-in. Prompt-direction use gated by `promptCapabilities` (PR1: false). |
| `DataBlock` w/ `URLSource(url, media_type)` | `ResourceLink` | `url` → `uri`. Baseline in prompts. |
| `HintBlock` | — | Dropped (§10). |
| Inbound `resource_link` | `TextBlock` | Rendered as a readable link mention for the model. |
| Inbound embedded `resource` | `TextBlock` | Text resources are inlined defensively even though `embeddedContext` is not advertised. |

**Plan handling.** AgentScope has no first-class "plan" block in the
public event/message union, so ACP `plan` is **not emitted in PR1**
(explicitly noted, not fabricated). Todo/plan-like `CustomEvent`s, if a
forker emits them, can be mapped in a forked translator.

---

## 12. Tool-call & permission model

### ToolCall lifecycle → `tool_call` / `tool_call_update`

```
ToolCallStartEvent      → tool_call            status=pending      (toolCallId, title, kind)
ToolCallDelta…End       → tool_call_update     (final rawInput; deltas buffered)
[permission gate, §below]
ToolResultStartEvent    → tool_call_update     status=in_progress
ToolResult*Delta        → tool_call_update     (content replaced with buffered output)
ToolResultEndEvent      → tool_call_update     status=completed|failed  (content, rawOutput, taxonomy in _meta)
```

**`kind` mapping** (presentation hint only): `Read` → `read`,
`Grep`/`Glob` → `search`, `Write`/`Edit` → `edit`,
`Bash`/`PowerShell` → `execute`, others → `other`.

### RequireUserConfirmEvent ↔ `session/request_permission`

The AgentScope permission flow is a **park/resume** flow:

1. Inside a turn, the agent finds `check_permission` returns
   `ASK`/`PASSTHROUGH`, sets the `ToolCallBlock.state = ASKING`,
   attaches `suggested_rules`, **yields
   `RequireUserConfirmEvent(reply_id, tool_calls=[...])`**, and
   `reply_stream` returns (the reply parks).
2. The example sends **`session/request_permission`** to the client for
   each gated tool call: `toolCall: ToolCallUpdate{toolCallId =
   tool_call.id, title, rawInput}` (binds the request to the *exact
   operation id* — invariant d), `options:` the four
   `PermissionOptionKind`s (`allow_once`, `allow_always`,
   `reject_once`, `reject_always`).
3. The client returns `RequestPermissionResponse.outcome`:
   - `{"outcome":"selected","optionId":...}` → mapped to a
     `ConfirmResult(confirmed=..., tool_call=..., rules=...)`:
     `allow_always` carries the core's own `suggested_rules` (they
     encode the tool-specific `rule_content` — a path glob, a command
     prefix) or a minted `PermissionRule(tool_name=...,
     behavior=ALLOW, source="acp-client")`; `reject_always` a DENY
     rule. Rules ride in `ConfirmResult.rules` and are applied by the
     agent internally on resume — the example never touches the
     private `agent._engine` (§15 gap 5).
   - `{"outcome":"cancelled"}` → the turn is aborted: the parked reply
     is closed via `reply_stream(inputs=UserInterruptEvent(reply_id))`
     (closing ASKING calls as `INTERRUPTED`) and the open
     `session/prompt` resolves with `stopReason:"cancelled"`.
4. Otherwise the example resumes:
   `reply_stream(inputs=UserConfirmResultEvent(reply_id,
   confirm_results=[...]))`. The agent validates against the awaiting
   tool-call ids, applies each decision (state `ALLOWED`, or a `DENIED`
   `ToolResultEnd`), installs any `rules`, and continues the react
   loop. Partial confirmations are allowed.

### PermissionEngine composed with shell-mediated approval — one prompt, with stated exceptions

Two approval authorities exist: AgentScope's `PermissionEngine`
(kernel) and the client's own permission UI. To avoid double-prompting:

- The kernel's `PermissionEngine` decides **whether a permission is
  needed at all**. Only `ASK`/`PASSTHROUGH` surface
  `session/request_permission`; an outright `ALLOW`/`DENY` never
  prompts.
- **Read-only fast path (#2117).** Under `PermissionMode.DEFAULT` the
  engine auto-allows read-only invocations — `Read`, `Grep`, `Glob`
  and read-only `Bash` commands never prompt. Only side-effecting
  operations (`Write`/`Edit`/side-effecting `Bash`) reach the client.
- The client is the **sole interactive prompt surface** for `ASK`
  cases; the kernel is a subprocess with no UI and always delegates.
- **Batch de-duplication (#2117).** Within one concurrent tool batch,
  core suppresses a second ASK whose invocation is already covered by
  an earlier confirmation's suggested rule — the call stays PENDING and
  is re-evaluated after resume. So not every gated call yields its own
  `RequireUserConfirmEvent`; the "exactly one prompt" property is
  per-*operation*, sometimes resolved by an earlier prompt's rule.
- **Bypass-immune safety ASKs (#2117).** Safety-critical decisions are
  marked bypass-immune and are **not** silenced by installed allow
  rules — a client may legitimately be prompted again for a dangerous
  operation after `allow_always`. The v1 "no re-prompting after
  allow_always" promise is therefore scoped to ordinary operations.
- **No silent shell writes.** `PermissionMode.DEFAULT` gates
  `Write`/`Edit`/side-effecting `Bash` behind ASK, so a gated operation
  always surfaces exactly one client prompt *before* any side effect.
- **Permission is bound to the exact `toolCallId`**, not to prompt text
  (invariant d).
- **Single-gate assumption.** The design assumes the client treats
  `session/request_permission` as the sole interactive gate and does
  not independently re-prompt on the resulting `fs/write_text_file` /
  `terminal/*` callbacks; ACP clients are expected not to.

**Alternative considered — `on_check_permission` middleware (#2001).**
Core now exposes a live per-call interception hook
(`MiddlewareBase.on_check_permission`): an adapter middleware could
await the `session/request_permission` round-trip *inside* the reply
loop and return ALLOW/DENY directly, avoiding the park/resume dance.
The example deliberately stays on park/resume because it (a) preserves
core's ASKING-state bookkeeping, `suggested_rules`, and batch
de-duplication untouched, (b) keeps the turn cancellable at the gate
through the same task-cancel path as everywhere else, and (c) leaves
the permission UX identical for `session/load` replay later. Forkers
wanting an in-turn gate now have a public seam for it. *(This replaces
v1's "confirm-feedback ergonomics" core gap — the hook exists now.)*

---

## 13. Filesystem & terminal authority

**Default = shell delegation.** The ACP Client owns the local
filesystem and terminal (it sees unsaved editor buffers). The kernel
routes file/terminal *operations* to the client through **one**
backend.

**One `ClientBackend(BackendBase)` implementing all three primitives.**
`BackendBase` declares **three** `@abstractmethod` primitives —
`exec_shell(command: list[str], *, cwd=None, timeout=None) ->
ExecResult`, `read_file(path) -> bytes`, `write_file(path, data:
bytes)` — and a subclass must implement all three. Moreover, the
builtin file tools call `exec_shell` at runtime, so a client-delegating
backend cannot be fs-only:

- `Read` runs `file_exists` + `is_dir` (base helpers that call
  `exec_shell(["test","-e"/"-d", path])`) *before* `read_file`.
- `Write` calls `exec_shell(["mkdir","-p", parent])` plus `file_exists`.
- `Edit` composes `read_file` + `write_file` with `file_exists`.
- `Grep` shells out to **ripgrep** (`rg`) via `exec_shell`.
- `Glob` runs `is_dir` plus the bundled **`_glob_helper.py`** script,
  invoked as `[python3, <helper_path>, --pattern, P, --base-dir, D]`
  through `exec_shell`. *(v1 wrongly said Glob runs `find`; the helper
  script was already the implementation then. `find` is used by the
  base-class `scandir`/`stat` helpers, which the fixed tool set does
  not exercise.)*

ACP has no `fs/exists`, `fs/is_dir`, `fs/list`, or `fs/search` method,
so these existence/dir/mkdir/search paths can only route to
`terminal/*`. The example therefore ships a **single** `ClientBackend`
that routes:

- `read_file(path)` → `fs/read_text_file` (returns content **including
  unsaved editor buffers** — the point of delegation); bytes↔text
  conversion is UTF-8 at the bridge (ACP's fs channel is text-only),
- `write_file(path, data)` → `fs/write_text_file`,
- `exec_shell(command, *, cwd, timeout)` → the client `terminal/*`
  create→wait→output→release pattern, used both directly by `Bash` and
  internally by `file_exists`/`is_dir`/`mkdir -p`/ripgrep/the Glob
  helper. The returned `terminalId` is recorded against the op id
  (§14 c); a timeout kills the terminal and reports the `ExecResult`
  internal-failure code (−1). The ACP terminal merges stdout/stderr
  into one stream, surfaced as `stdout`.

**Same-machine reality check.** In practice the client spawns this
kernel *locally*, so client-executed commands share the kernel's
filesystem — which is what makes `rg` and the Glob helper (a path
inside the installed `agentscope` package) resolvable at all. The
client machine must have `rg` and `python3` on PATH for `Grep`/`Glob`;
`Glob(glob_helper_path=...)` exists for exotic layouts. The split —
existence/dir checks reflect *on-disk* state via `terminal/*` while
content reads reflect *editor buffers* via `fs/*` — is an accepted
approximation.

**Consequence — terminal is in PR1.** fs-delegated `Read`/`Write`/
`Edit` and exec-backed `Grep`/`Glob`/`Bash` **all** require the client
`terminal` capability *in addition to* `fs.*`. The terminal bridge is
part of PR1, not a follow-on.

- **Absolute-path requirement:** ACP mandates absolute paths. The
  backend resolves any relative path against the session `cwd` before
  calling `fs/*`/`terminal/*`, and overrides `getcwd()` to return the
  session `cwd` without a client round-trip.

**Workspace opt-in sandbox mode.** Set `ACP_AUTHORITY=workspace`. In
`build_agent()`, instead of the client-delegating backend, the tools
are constructed with **one** Workspace backend implementing all three
primitives natively — `LocalBackend` is wired out of the box, and any
`agentscope.workspace` backend (**Docker, E2B, Daytona, K8s,
OpenSandbox, Apple container, Bubblewrap** — all `BackendBase`
subclasses, each behind its `workspace-*` extra) drops into the same
one-line seam. Same tools, rebound via injection — no core change, and
no client `fs`/`terminal` capability needed. (Tools are wired directly
with `backend=...` rather than via `LocalWorkspace.list_tools()`,
because `LocalWorkspace._backend` is hard-coded and not injectable —
§15 gap 2.)

**Authority boundary.** Shell owns local file/terminal *mediation* by
default; kernel owns agent planning, tool intent, and `AgentEvent`
production. In Workspace mode the kernel owns the sandboxed FS/terminal
too. The mode is captured in the capability snapshot (invariant b).

**Capability-absent handling (shell mode).**
- `terminal` absent: nothing client-delegated can run (even
  `Read`/`Write`/`Edit` need `exec_shell` for their existence checks) —
  `build_agent()` omits **all** client-delegated tools, logs to stderr,
  and the user opts into Workspace mode for local tools.
- `fs.readTextFile` / `fs.writeTextFile` absent: `Read`/`Write`/`Edit`
  are omitted (no silent fallback to touching local disk in shell
  mode); `Grep`/`Glob`/`Bash` still run if `terminal` is present.

---

## 14. Receipt-ready event invariants (PR1 acceptance criteria)

The receipt *emitter* is an optional, derived, vendor-neutral follow-on
(§20). **PR1 nonetheless guarantees** that a receipt can be
reconstructed *without log-scraping*. The five invariants and where
each id comes from:

| # | Invariant | Source (reuse core id vs mint+map) |
|---|---|---|
| **a** | **One stable turn/session id** shared by `session/new`+`session/prompt`, emitted tool calls, permission requests, and the final stop reason. | **Reuse core.** Session id = `AgentState.session_id` = ACP `sessionId`. Turn id = `reply_id` (on every event). |
| **b** | **Initialized capability snapshot**, including whether fs/terminal are **shell-delegated** or **Workspace-backed** for that session. | **Mint+map at adapter.** Captured at `initialize` (client caps) + resolved at `session/new` (authority mode from env). Stored on the `Session`. |
| **c** | **Stable operation id for every `fs/*`, `terminal/*`, and permission-gated tool call.** | **Reuse core id, threaded via middleware + ContextVar.** Tool-call op id = `tool_call_id` (= ACP `toolCallId`). Mechanism below. |
| **d** | **Permission decision bound to the operation id**, not only to prompt text. | **Reuse core.** `session/request_permission.toolCall.toolCallId` = `tool_call_id`; the returned outcome maps into a `ConfirmResult(tool_call=<same id>)`. |
| **e** | **Terminal result state taxonomy:** `completed` / `denied` / `cancelled` / `failed` / `interrupted`. | **Mint+map at adapter,** derived from `ToolResultState` (+ turn-level cancel): carried in `_meta.agentscope.result_state` on the final `tool_call_update`; terminal exit codes attach to the op record. |

### The op-id binding mechanism (invariant c) — revised in v2

`BackendBase.{read_file,write_file,exec_shell}` receive **no**
enclosing `tool_call_id` (still a core gap — §15 gap 4). The v1 draft
proposed setting a ContextVar when the event consumer observes
`ToolResultStartEvent`. **Implementation showed that timing cannot
work for concurrent tools:** AgentScope executes concurrency-safe
tools (`Read`/`Grep`/`Glob`) in *parallel worker tasks* whose contexts
are snapshotted when the batch starts — before the consumer sees any
`TOOL_RESULT_START` — so a consumer-side ContextVar write never
reaches them.

The shipped mechanism binds from *inside* the tool's own task instead,
using two public seams:

1. The translator registers every completed tool call (`TOOL_CALL_END`
   → `tool_call_id`, tool name, raw JSON input) in the session's
   `OpRegistry` as *pending*.
2. Every tool carries an `OpBindingMiddleware`
   (`ToolMiddlewareBase`) that runs in the tool's execution context:
   at invocation it *claims* the matching pending entry (by tool name,
   disambiguated by parsed input when one batch calls the same tool
   twice) and sets the `_CURRENT_TOOL_CALL_ID` ContextVar **inside the
   worker task**, where every backend call the tool makes can see it.
3. `ClientBackend` stamps each outbound `fs/*`/`terminal/*` call with
   the ContextVar value into the `OpRegistry` records (path/command,
   `terminalId`, exit code).

A backend call outside any tool records `tool_call_id = None`. A
first-class backend-call context in core would make the claim-matching
unnecessary — kept as a gap to raise (§15 gap 4, §21 q2).

---

## 15. Public-API-only implementation notes

Exact public symbols the example imports (all from AgentScope
subpackages; top-level `agentscope` only re-exports `logger`,
`setup_logger`, `set_id_factory`, `set_timestamp_factory`,
`__version__`):

- **`agentscope.agent`**: `Agent` (constructor: `name`,
  `system_prompt`, `model`, `toolkit`, `state`, plus config objects).
- **`Agent.reply_stream`** — the turn/streaming API. Current
  signature: `reply_stream(inputs: Msg | list[Msg] |
  UserConfirmResultEvent | UserInterruptEvent |
  ExternalExecutionResultEvent | None = None, structured_schema=None,
  yield_final_msg=False) -> AsyncGenerator[AgentEvent | Msg, None]`
  (the `Msg` is only yielded when `yield_final_msg=True`, so the
  event-only consumption here is unaffected; `structured_schema` is
  #2150's structured-output support, unused by the example).
- **`agentscope.event`**: the `AgentEvent` union and every subtype in
  §10 — including the post-v1 additions `UserInterruptEvent` and the
  `ReplyFinishedReason`-carrying `ReplyEndEvent` — plus
  `ConfirmResult` (which lives here, not in `agentscope.message`).
- **`agentscope.types`**: `ReplyFinishedReason`, `ErrorInfo`.
- **`agentscope.permission`**: `PermissionMode`, `PermissionBehavior`,
  `PermissionRule` (engine/context/decision types available but not
  needed directly).
- **`agentscope.tool`**: `Toolkit`; builtin tools `Read`, `Write`,
  `Edit`, `Bash`, `Grep`, `Glob`; backend seam `BackendBase`,
  `LocalBackend`, `ExecResult`; middleware seam `ToolMiddlewareBase`.
- **`agentscope.workspace`** (opt-in sandbox): any of the
  Docker/E2B/Daytona/K8s/OpenSandbox/AppleContainer/Bubblewrap
  backends.
- **`agentscope.message`**: `Msg`, `UserMsg`, blocks (`TextBlock`,
  `DataBlock`, `Base64Source`, `ToolCallBlock`), `ToolResultState`.
- **`agentscope.state`**: `AgentState` (session id +
  `permission_context`), `model_dump`/`model_validate` for DIY
  persistence.
- **`agentscope.model` / `agentscope.credential`**: the provider
  chat-model + credential pairs used by `_make_model()`.

**None of the above requires a core change.**

### Core gaps — status after the 2026-08 revision

The v1 draft flagged six gaps. Two were closed or superseded upstream;
four remain (renumbered):

**Closed upstream:**
- *(v1 gap 1)* **Hard interrupt — CLOSED by #1995.** Cancelling the
  task consuming `reply_stream` is now a supported, gracefully-handled
  path: core catches the `CancelledError`, closes unfinished tool
  calls with `INTERRUPTED` results, emits
  `ReplyEndEvent(finished_reason=INTERRUPTED)` plus a fallback
  assistant message, and swallows the exception by default
  (`ReActConfig.interruption_raise_cancelled_error=False`). A *parked*
  reply (awaiting confirm/external execution) is aborted with the new
  public `UserInterruptEvent` input. The only residue is naming — there
  is no convenience method literally called `Agent.interrupt()` — not
  worth a core issue.
- *(v1 gap 2)* **Confirm-feedback ergonomics — SUPERSEDED by #2001.**
  The `on_check_permission` middleware hook is a live, public
  interception point (§12). The example stays on park/resume by
  choice, not necessity.

**Still open (flagged, not patched here):**
1. **`usage_update` fidelity** *(v1 gap 4)*. AgentScope exposes
   per-call `input_tokens`/`output_tokens`
   (`ModelCallEndEvent`), not context-window `used`/`size`; a faithful
   ACP `usage_update` needs a total-window signal, so it is omitted
   (§10). Data gap, not a core-change request.
2. **Backend injection via `LocalWorkspace`** *(v1 gap 3)*.
   `LocalWorkspace._backend = LocalBackend()` remains hard-coded (not
   a constructor param) after the workspace refactor (#1971). Avoided
   by wiring tools directly with `backend=...`; minor.
3. **No public API to install a `PermissionRule` on a running agent's
   engine** *(v1 gap 6, softened)*. `Agent._engine` is still private
   with no accessor, but two public paths exist: rules fed via
   `ConfirmResult.rules` on resume (what the example uses), and the
   engine reads `agent.state.permission_context.{allow,deny,ask}_rules`
   live, so mutating the public state object is effective. An explicit
   accessor would still be cleaner; low priority.
4. **No mechanism to thread the enclosing `tool_call_id` into a
   `BackendBase` call** *(v1 gap 5, sharpened)*. The three primitives
   carry no call context, and — worse than v1 assumed — a
   consumer-side ContextVar cannot reach concurrent tool tasks (§14).
   The example works around it with middleware claim-matching; a
   first-class backend-call context (a ContextVar set by
   `toolkit.call_tool`, or a threaded parameter) would make this
   robust for everyone. **This is the one gap worth raising as a core
   issue.**

---

## 16. Implementation notes (as built)

The full source lives beside this document; the load-bearing shapes:

**`build_agent()` (`agent.py`)** — the fork seam. Signature grew two
adapter-side parameters relative to the v1 sketch (`ops` — the
session's `OpRegistry`; `config` — parsed env), because op binding
(§14 c) needs the registry at tool-construction time:

```python
def build_agent(*, cwd, state, conn, caps, ops, config) -> Agent:
    middleware = OpBindingMiddleware(ops)
    if config.is_shell_authority:
        backend = ClientBackend(conn=conn, session_id=state.session_id,
                                cwd=cwd, ops=ops)
        tools = _shell_tools(backend, cwd, caps, middleware)  # §13 gating
    else:
        backend = _make_workspace_backend(config)
        tools = [Read(backend=backend, middlewares=[middleware]), ...]
    state.permission_context.mode = PermissionMode(config.permission_mode)
    return Agent(name=..., system_prompt=..., model=_make_model(config),
                 toolkit=Toolkit(tools=tools), state=state)
```

**The turn (`server.py`)** — one ACP turn = a `reply_stream`, resumed
across permission gates, in a child task:

```python
async def _run_turn(self, sess, inputs) -> str:
    while True:
        pending_confirm, finished = None, None
        async for event in sess.agent.reply_stream(inputs):
            if isinstance(event, RequireUserConfirmEvent):
                pending_confirm = event; break
            if isinstance(event, ReplyEndEvent):
                finished = event.finished_reason        # #1995: the one
                ...                                     # terminal signal
            for update in sess.translator.translate(event):
                await self._conn.session_update(session_id=sess.id,
                                                update=update)
        if pending_confirm is None:
            return _stop_reason(finished)               # §9 table
        results = await request_permission_for(self._conn, sess,
                                               pending_confirm)
        if results is None:                             # client cancelled
            await self._drain_interrupt(sess, pending_confirm.reply_id)
            return "cancelled"
        inputs = UserConfirmResultEvent(reply_id=pending_confirm.reply_id,
                                        confirm_results=results)
```

Cancellation notes (differ from the v1 sketch, because of #1995):
- When `session/cancel` cancels the child task **inside**
  `reply_stream`, no exception surfaces — the agent swallows the
  `CancelledError` and the stream ends normally with
  `finished_reason=interrupted`, which the loop above maps to
  `"cancelled"`. The `except asyncio.CancelledError` branch exists
  only for cancellation landing *outside* the generator (mid
  `session_update` write, mid permission round-trip), where the
  example closes the generator or aborts the parked reply via
  `UserInterruptEvent`.
- `PromptResponse{stopReason}` is **always** returned on cancel —
  never a JSON-RPC error.

**SDK surface used** (verified against `agent-client-protocol==0.12.0`):
`acp.run_agent` (binds stdio, 50 MB buffer), `acp.Agent` base class
(handlers: `initialize`, `new_session`, `prompt`, `cancel`,
`authenticate`, `on_connect`), the `acp.interfaces.Client` methods
(`session_update`, `request_permission`, `read_text_file`,
`write_text_file`, `create_terminal`, `wait_for_terminal_exit`,
`terminal_output`, `kill_terminal`, `release_terminal`),
`acp.schema` models, `acp.RequestError`, and the content helpers
(`text_block`, `image_block`, `audio_block`, `tool_content`). Handler
and helper names are snake_case in the SDK; wire names remain the
protocol's (`session/new`, `fs/read_text_file`, …).

**Vendored fallback.** v1 planned a vendored newline-delimited
JSON-RPC peer in case the SDK was judged immature. The SDK's stdio
peer, task-per-inbound dispatcher and typed schema proved solid in
implementation (§19 exercises the real wire), so the fallback is
dropped; the SDK stays pinned in `requirements.txt`, **not** a core
dependency.

---

## 17. End-to-end sequence (desktop, Agent role)

```
Shell (ACP Client, e.g. Zed)                    examples/acp/ kernel (ACP Agent)
────────────────────────────                    ────────────────────────────────
1. spawn subprocess: `python -m acp_example`  ─▶ acp.run_agent binds stdin/stdout;
                                                  on_connect(conn) stores the Client handle
2. → initialize { protocolVersion:1,
      clientCapabilities:{fs:{readTextFile,      ◀─ InitializeResponse { protocolVersion:1,
      writeTextFile}, terminal:true} }                agentCapabilities:{}, agentInfo }
                                                   (snapshot clientCapabilities — invariant b)
3. → session/new { cwd:"/abs/proj" }              build_agent(cwd=..., state=..., conn=..., caps=..., ops=...)
                                                  ◀─ NewSessionResponse { sessionId = state.session_id }
                                                   (sessionId == AgentState.session_id — invariant a)
4. → session/prompt { sessionId,
      prompt:[text_block("fix the bug in x.py")]}  try_begin_turn(); turn runs in a CHILD task
                                                   (request stays OPEN for the whole turn)
5.                                             ◀─ session/update agent_thought_chunk (messageId=block_id)
6.                                             ◀─ session/update agent_message_chunk (streamed text)
7.                                             ◀─ session/update tool_call
                                                     { toolCallId, title:"Read", kind:"read",
                                                       status:"pending" }        (op id — invariant c)
8.  [DEFAULT read-only fast path: Read is
     auto-allowed — NO permission prompt]      ◀─ terminal/create { command:"test", args:["-e", ...] } ...
                                               ◀─ fs/read_text_file { path:"/abs/proj/x.py", sessionId }
    → { content:"...file (incl. unsaved buffer)..." }
9.                                             ◀─ session/update tool_call_update {status:"completed", ...}
10.                                            ◀─ session/update tool_call
                                                     { toolCallId', title:"Edit", kind:"edit",
                                                       status:"pending" }
11.  [permission gate: Edit is side-effecting
      → engine returns ASK]                    ◀─ session/request_permission
                                                     { sessionId, toolCall:{toolCallId'}, options:[
                                                        allow_once, allow_always,
                                                        reject_once, reject_always] }
12. → { outcome:"selected", optionId:"allow_once" }
                                                   map -> ConfirmResult(confirmed=True, tool_call=<id'>)
                                                   resume: reply_stream(inputs=UserConfirmResultEvent)
                                                   (decision bound to toolCallId' — invariant d)
13.  [Edit: read + write via fs/*]             ◀─ fs/read_text_file { path } / fs/write_text_file { path, content }
    → {}
14.                                            ◀─ session/update tool_call_update
                                                     { status:"completed", content:[...], rawOutput }
                                                   (ToolResultState.SUCCESS -> completed — invariant e)
15.  [model done -> ReplyEndEvent(completed)]
                                               ◀─ PromptResponse { stopReason:"end_turn" }  (closes step 4)
```

*(v1 showed the permission prompt on the `Read` call; under #2117's
read-only fast path a `Read` never prompts in DEFAULT mode, so the
gate is illustrated on the `Edit`.)*

If the user cancels mid-turn: `→ session/cancel {sessionId}`
(notification) arrives concurrently → the kernel cancels the **child
turn task** → core closes running tools with `INTERRUPTED` results and
ends the stream with `finished_reason=interrupted` → the still-open
`session/prompt` resolves with `stopReason:"cancelled"`. For an
outstanding `session/request_permission`, the client answers
`{"outcome":"cancelled"}` and the kernel aborts the parked reply via
`UserInterruptEvent` (§12, §18).

---

## 18. Error handling & edge cases

| Case | Handling |
|---|---|
| **JSON-RPC errors** | Handler exceptions map to JSON-RPC `Error{code,message}` (SDK does the encoding; `RequestError.invalid_params` / `.resource_not_found` / `.internal_error` helpers). Unknown sessions → invalid params; relative `cwd` → invalid params; a turn ending `finished_reason=error` → internal error carrying the `ErrorInfo`. Notifications never get responses. |
| **Cancellation mid-tool** | `session/cancel` → cancel the **child turn task**, never the request-handler task. Core (#1995) converts this into `ToolResultState.INTERRUPTED` chunks + `ReplyEndEvent(finished_reason=interrupted)` and swallows the `CancelledError` (default `ReActConfig.interruption_raise_cancelled_error=False`); the turn loop maps it to `stopReason:"cancelled"`. Never leaks as a JSON-RPC error. Receipt taxonomy: `interrupted`. |
| **Cancellation mid-permission** | The client answers the outstanding `session/request_permission` with `{"outcome":"cancelled"}` (per spec); the kernel aborts the parked reply via `UserInterruptEvent` — ASKING calls close as `INTERRUPTED` — and resolves `cancelled`. If the task cancel lands first, the `except CancelledError` path does the same close-out. |
| **Second `session/prompt` while a turn is active** | Rejected with `-32603`. One `Agent`/`AgentState` must not be driven by two concurrent `reply_stream`s (context/state corruption). **Single-active-turn-per-session** invariant; different sessions remain fully independent. |
| **Permission denial** | `reject_once`/`reject_always` → `ConfirmResult(confirmed=False)` → agent emits a `DENIED` `ToolResultEnd` → `tool_call_update{status:"failed"}` (taxonomy `denied`). The turn always continues — the model sees the denial and answers accordingly. *(v1 said "unless `ReActConfig.stop_on_reject=True`"; that field exists but is consumed nowhere in core — dead config, dropped here.)* |
| **fs/terminal errors** | Client returns a JSON-RPC error for `fs/*`/`terminal/*`; the backend surfaces it as a raised read/write error or a non-zero `ExecResult`, which the tool converts into `ToolResultState.ERROR` → `tool_call_update{status:"failed"}`. Relative paths are resolved against the session cwd before any call leaves the process. |
| **Capability absent** | Never call an fs/terminal method whose capability is false (§13). Degrade (omit the affected tool in `build_agent()`, log to stderr) rather than fall back to silently touching local disk in shell mode. |
| **Large tool outputs / backpressure** | `terminal/create.outputByteLimit` (1 MiB) truncates from the beginning; tool-result text buffering keyed by `tool_call_id` sends accumulated `content` (which *replaces* the collection). The SDK's 50 MB stdio buffer covers large frames; `ContextConfig.tool_result_limit` (default 50000) caps what re-enters model context. |
| **External tools** | `RequireExternalExecutionEvent` is not supported by the fixed tool set; if a forker's toolkit emits it, the example logs to stderr and aborts the turn via `UserInterruptEvent` (extend `server.py` for real external execution). |
| **Malformed frames** | One JSON object per line; the SDK handles framing and `-32700` parse errors. The kernel MUST NOT write non-ACP text to stdout — all example logging goes to **stderr**. |
| **Concurrent client calls during a turn** | The SDK dispatcher is task-per-inbound, so the child turn task can await `fs/*`/`terminal/*`/`session/request_permission` while `session/cancel` dispatches concurrently. Multiple sessions on one connection are independent. |

---

## 19. Testing & demo

Implemented in `tests/` (all §14 invariants asserted):

1. **Mock ACP client harness** (`mock_client.py`): serves `fs/*`
   against a temp dir, backs `terminal/*` with **real subprocesses**,
   records every `session/update`, and answers
   `session/request_permission` from a script — so
   Read/Write/Edit/Bash execute the full shell-delegation path
   end-to-end.
2. **Translator unit tests** (`test_translate.py`): block_id →
   messageId and tool_call_id → toolCallId correlation; delta
   buffering; `usage_update` omission; hint-drop; the
   `ToolResultState` → status + taxonomy table.
3. **Permission round-trip** (`test_server.py`):
   `session/request_permission{toolCall.toolCallId == tool_call_id}`
   (invariant d); allow → the write lands via `fs/write_text_file`;
   reject → `DENIED` taxonomy and the turn continues;
   `{"outcome":"cancelled"}` → parked reply aborted, `stopReason:
   "cancelled"`, ASKING call closed as `interrupted`.
4. **Read-only fast path**: a `Read` turn produces **no** permission
   prompt under DEFAULT (#2117) and binds its `fs/read` + `terminal`
   ops to the tool_call_id (invariant c).
5. **Cancellation & concurrency**: `session/cancel` during a running
   `Bash sleep` yields `stopReason:"cancelled"` (not a JSON-RPC
   error), an `interrupted` tool result, and the session accepts a
   fresh turn afterwards; a second `session/prompt` on a busy session
   is rejected while parked at the gate.
6. **Capability gating**: no `terminal` → no tools; no `fs.*` →
   exactly `{Grep, Glob, Bash}`.
7. **Wire-level smoke test** (`test_stdio_rpc.py`): the kernel behind
   `acp.run_agent` driven by the SDK's own client connection over a
   socket pair — real framing, routing and serialization.
8. **e2e against a real shell (Zed)**: manual; setup documented in
   `README.md`.

---

## 20. Phasing / milestones

**Phase 1 — `examples/acp/`.**
- **PR1 (this directory, implemented):** stdio peer (SDK) + session
  manager (single-active-turn guard) + `build_agent()` +
  `translate.py` + permission bridge + the single `ClientBackend`
  delivering shell delegation over both `fs/*` and `terminal/*`,
  gated on client capabilities; cancellable child-task turns; the five
  §14 invariants enforced by tests.
- **Follow-ons within the example:**
  1. **Receipt emitter** — optional, derived, vendor-neutral; consumes
     the invariant-carrying stream (can replay via `Msg.append_event`).
  2. **`session/load`** — flip `loadSession=true`; persist/replay via
     `AgentState.model_dump`/`model_validate`.
  3. **MCP pass-through** — `session/new.mcpServers` (stdio baseline)
     into `Toolkit(mcps=...)`.
  4. **Workspace sandbox breadth** — exercise Docker/E2B/… backends
     beyond the wired `LocalBackend`.
  5. **Session modes / config options** — expose `modes` /
     `configOptions`.
  6. **Prompt capabilities** — forward `image`/`audio`/embedded
     context to capable models and advertise accordingly.

**Phase 2 — reassess SDK promotion.** Once ACP stabilizes, decide
among: an in-tree `agentscope/acp/` module, a separate `agentscope-acp`
package, or an in-between — now with `app.channel` as the in-tree
precedent for adapter promotion. Merging proven functionality is easier
than removing unproven functionality.

---

## 21. Open questions

**Resolved:**
- **fs/terminal default → shell-delegated** (client owns the workspace
  and unsaved buffers; Workspace opt-in). Implemented (§13).
- **Positioning → in-between** (fixed runnable default,
  `build_agent()` seam, env config). Implemented (§7).
- **Hard interrupt** *(v1 q1)* → closed upstream by #1995; the example
  maps `finished_reason=interrupted` → `cancelled` and uses
  `UserInterruptEvent` for parked replies. No core issue needed.
- **Confirm-feedback ergonomics** *(v1 q7)* → superseded by #2001's
  `on_check_permission` hook; the example documents why it stays on
  park/resume (§12).

**Genuinely open:**
1. **Threading `tool_call_id` into the backend** *(sharpened)*. The
   middleware claim-matching (§14 c) works but is heuristic under
   same-tool-same-input concurrent batches. Worth a core proposal: a
   ContextVar set by `toolkit.call_tool` around tool execution (or a
   context parameter on the backend primitives). **Recommended to
   raise as an issue.**
2. **`stopReason` fidelity.** `max_tokens` and `refusal` still have no
   `AgentEvent` source (`ReplyFinishedReason` covers
   completed/interrupted/exceed_max_iters/error). Raise only if a
   client is found to depend on them.
3. **`usage_update` fidelity.** Needs a context-window `used`/`size`
   signal from core (`ModelCallEndEvent` has per-call tokens only;
   `ChatModelBase.context_size` exists but not tokens-in-context).
4. **Plan mapping.** No first-class plan event; `sessionUpdate:"plan"`
   stays unmapped. A `CustomEvent`-based convention is possible if
   demand appears.
5. **Rule installation on a running engine.** Two public paths exist
   (§15 gap 3); is an explicit accessor worth core surface?
6. **SDK / protocol churn.** Facts as of 2026-08-06: Python SDK
   0.12.0 (2026-08-01; pre-1.0; bundles v1 schema 1.19.0; ships
   RFD-based HTTP/WS transports), protocol v1 schema line at 1.20.0 —
   with the SessionUpdate variants, StopReason values, usage_update
   required fields and fs/terminal registry unchanged since 1.17.0 —
   and **ACP v2 published as a Draft** (schema 2.0.0-alpha.2,
   2026-07-21) with the known migrations (`authenticate`→`auth/login`,
   `session/set_mode` removal, fs/terminal restructuring). Strategy
   unchanged: pin the SDK, target protocol v1, keep the adapter thin
   so a rename is a one-file change. *(v1 misstated the 0.10.1 pin as
   bundling schema v0.13.6 — it bundled v0.12.2 — and called the
   protocol pre-1.0; corrected.)*

---

## 22. References

**ACP specification (target protocol version `1`)** —
`github.com/agentclientprotocol/agent-client-protocol`:
- `docs/protocol/v1/*.mdx` — initialization, session setup, prompt
  turn, tool calls, content, file-system, terminals.
- `schema/v1/schema.json` — v1 line, currently 1.20.0; the pinned SDK
  bundles 1.19.0. v2 draft under `schema/v2/` (2.0.0-alpha.x).
- JSON-RPC 2.0; stdio transport (newline-delimited, UTF-8; logs on
  stderr).

**ACP Python SDK** — `github.com/agentclientprotocol/python-sdk`;
PyPI `agent-client-protocol` (import `acp`), **pinned `==0.12.0`**;
`acp.PROTOCOL_VERSION = 1`; `acp.run_agent`, `acp.Agent`,
`acp.interfaces.Client`, `acp.schema`, helpers.

**AgentScope public modules** (under `src/agentscope/`, imported via
subpackages): `agentscope.agent`, `agentscope.event`,
`agentscope.types`, `agentscope.permission`, `agentscope.tool`,
`agentscope.workspace`, `agentscope.message`, `agentscope.state`,
`agentscope.model`, `agentscope.credential`.
- **Reference only (not imported):** `agentscope.app.middleware
  ._protocol` (AG-UI mapping idea), `agentscope.app.channel` (the
  in-tree adapter precedent, #1997).
