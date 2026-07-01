# ACP Agent (stdio) example for AgentScope — design & build plan (examples/acp/)

**Status:** Draft for discussion ([#1948](https://github.com/agentscope-ai/agentscope/discussions/1948))

**Scope one-liner:** A fully runnable Agent Client Protocol (ACP) **Agent role, stdio transport** example under `examples/acp/`, built only on AgentScope's existing public API — a desktop shell (ACP Client, e.g. Zed) drives an AgentScope kernel (ACP Agent) as a subprocess. **No changes to `agentscope/` core.**

---

## 0. Header

- **Title:** ACP Agent (stdio) example for AgentScope — design & build plan (examples/acp/)
- **Status:** Draft for discussion (#1948)
- **Deliverable:** `examples/acp/` — a fixed, out-of-the-box general assistant *with coding capabilities*, exposed to any ACP desktop shell over stdio, isolated behind a single `build_agent()` factory so it is trivially forkable into a template.
- **Non-deliverable (this phase):** any in-tree `agentscope/acp/` module, any core surface-area change, the ACP Client direction, HTTP/WebSocket transport.

---

## 1. Summary & scope

We ship `examples/acp/`: an ACP **Agent** that speaks newline-delimited JSON-RPC 2.0 over **stdin/stdout**, wrapping an AgentScope `agentscope.agent.Agent`. A desktop code editor (the ACP **Client**) spawns the example as a subprocess, calls `initialize` / `session/new` / `session/prompt`, and receives streamed `session/update` notifications produced by translating AgentScope's `AgentEvent` stream (from `Agent.reply_stream`).

**In scope (Phase 1, this doc):**
- ACP **Agent** role only.
- **stdio** transport only.
- The event→protocol mapping built on public API: `Agent`, `reply_stream` → `AgentEvent` union, and the AgentEvent→protocol *conversion pattern* (the same idea the AG-UI middleware uses, re-implemented for stdio).
- Default **shell delegation** of filesystem *and* terminal (client owns the workspace) via a single client-delegating backend, with an **opt-in** AgentScope Workspace sandbox mode.
- Receipt-ready event invariants (stable ids, capability snapshot, operation ids, permission binding, terminal taxonomy) as PR1 *acceptance criteria*.

**Deferred / out of scope:**
- The ACP **Client** direction (AgentScope driving Claude Code / Codex as sub-agents).
- **HTTP / WebSocket** transport (ACP Streamable HTTP is only a draft RFD).
- Any promotion into an in-tree SDK — reassessed in Phase 2.
- **No changes to `agentscope/` core.** Everything below is composed from already-public symbols; any missing hook is flagged as a *core gap to raise separately*, not patched here.

---

## 2. Background & motivation

**The desktop kernel↔shell seam.** ACP factors an agentic coding session into two roles: the **Client** (the editor/IDE — owns the UI, the open buffers, the local filesystem and terminal) and the **Agent** (the model-driven kernel — owns planning, tool intent, and the streamed narration of a turn). They talk JSON-RPC over stdio. AgentScope already *is* a kernel: `Agent.reply_stream` emits a structured `AgentEvent` stream that describes exactly what a turn is doing. ACP is the desktop-native presentation of that stream.

**Two adapters over one core.** AgentScope already has one presentation adapter — the **AG-UI** middleware (`app/middleware/_protocol/_agui.py`), which maps `AgentEvent` → AG-UI SSE events for **web/HTTP** front-ends. ACP is the symmetric case for **desktop/stdio**. Both are *presentation adapters over one AgentEvent core*:

```
              AgentScope Agent.reply_stream  ──▶  AgentEvent stream
                         │                                │
             (web / HTTP)│                                │(desktop / stdio)
                         ▼                                ▼
              AG-UI SSE middleware              examples/acp/ ACP Agent
              (ProtocolMiddlewareBase)          (this deliverable)
```

The AG-UI adapter is HTTP-bound (Starlette `BaseHTTPMiddleware`, behind the `service` extra). ACP needs its own stdio peer, but it reuses the *mapping idea* and the discriminated-union deserialization pattern.

**The kernel we ship.** A **general assistant with coding capabilities**: an `Agent` with the builtin tools (`Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`) plus a permission engine, wired so file/terminal operations are *mediated by the shell* by default. It works against a real ACP client (Zed) out of the box.

**Why example-first.** ACP is pre-1.0 and evolving; the Python SDK is pre-1.0 too. Committing an in-tree module or a core surface to a still-moving protocol is premature. An example on public API proves which abstractions matter, ships value now, and is trivially removable if ACP churns. *Merging proven functionality later is easier than removing unproven functionality.* (See §3, DavdGao Concern 1.)

---

## 3. Maintainer concerns addressed (explicit)

### Concern 1 (DavdGao) — infrastructure duplication vs the multi-tenant core stack

The worry: an ACP integration might duplicate the multi-tenant core stack (sessions, storage, service layer) or bake ACP-specific surface into `agentscope/` while the protocol is still moving.

**Resolution.** `examples/acp/` uses **only public API** and adds **zero** core surface:
- It does **not** import the service layer (`agentscope.app.*`) or its `service` extra (FastAPI/uvicorn/AG-UI). The stdio peer is self-contained (from the ACP SDK, see §6/§16).
- It does **not** reintroduce a multi-tenant session manager. The library's entire public "session" concept is `AgentState.session_id` (Brief B §7); the example keeps an in-process `dict[SessionId, Session]` and persists with `AgentState.model_dump`/`model_validate` if/when `session/load` is added. No Redis, no `agentscope.app.storage`.
- All required behavior is reachable from public symbols (enumerated in §15). Where a hook is missing (hard interrupt; confirm-feedback ergonomics; backend injection through `LocalWorkspace`; threading `tool_call_id` into a backend; adding a `PermissionRule` to a running engine), we **flag it as a gap to raise separately** and work around it in the example — we do **not** patch core here.
- **Phase 2** reassesses promotion (in-tree `agentscope/acp/`, a separate `agentscope-acp` package, or in-between) *after* the example proves the abstractions and ACP stabilizes.

### Concern 2 (DavdGao) — kernel positioning / configuration

The worry: should the example ship a fixed agent, or a fully user-configurable one? A fixed agent risks being a toy; a fully-configurable one risks being an empty framework with no runnable default.

**Resolution — IN-BETWEEN.** Ship a **fixed, runnable default agent** (general assistant with coding capabilities) that works out of the box, with all agent construction isolated in a single `build_agent()` factory and model/credentials supplied via **environment variables**. What the *example author* fixes = the ACP wiring + the default agent. What is *exposed to the user* = model/creds via env, plus the `build_agent()` seam a forker edits to swap in their own agent. A finished demo that is trivially forkable into a template. The explicit **fixed-vs-exposed** matrix is in §7.

---

## 4. Goals / Non-goals

**Goals**
1. A subprocess that any ACP-compliant desktop client can spawn and drive over stdio, demoable against **Zed**.
2. Faithful mapping of a full AgentScope turn (`reply_stream`) onto ACP `session/prompt` semantics: streamed text/thinking, tool calls, permission prompts, fs/terminal callbacks, stop reason.
3. **Shell-delegation** as the default fs/terminal authority (client owns the workspace, sees unsaved buffers).
4. **Receipt-ready invariants** (§14) satisfied so a later, optional receipt emitter needs no log-scraping.
5. Zero core changes; a single `build_agent()` seam; env-based config.

**Non-goals**
1. ACP Client role; HTTP/WebSocket transport.
2. A first-class `Agent.interrupt()` (core gap — the example cancels the child turn `asyncio.Task`; §12/§16/§18).
3. Persistent multi-tenant storage / service layer.
4. Full ACP optional-method coverage in PR1 (`session/load`, `session/resume`, modes, config options) — these are *follow-ons within the example* (§20).
5. Emitting the actual receipt in PR1 — the emitter is an optional, derived, vendor-neutral follow-on. PR1 only *guarantees the invariants that make it possible*.

---

## 5. Architecture overview

```
  ┌────────────────────────────┐        stdio (newline-delimited JSON-RPC 2.0)       ┌──────────────────────────────────────────────┐
  │      Desktop shell         │  <───────────────────────────────────────────────>  │        examples/acp/ kernel (ACP Agent)         │
  │       (ACP Client)         │   client→agent: initialize, session/new,             │                                                │
  │   e.g. Zed / any IDE       │     session/prompt, session/cancel                   │   ┌────────────────────────────────────────┐   │
  │                            │   agent→client: session/update (notif),             │   │  stdio JSON-RPC peer  (acp.run_agent /   │   │
  │  owns: UI, open buffers,   │     session/request_permission,                      │   │  AgentSideConnection — SDK; or vendored) │   │
  │  local FS + terminal       │     fs/read_text_file, fs/write_text_file,           │   └──────────────────┬─────────────────────┘   │
  └────────────────────────────┘     terminal/*                                       │                      │  Agent-role handlers    │
                                                                                       │   ┌──────────────────▼─────────────────────┐   │
                                                                                       │   │   session manager  (dict[SessionId,      │   │
                                                                                       │   │   Session]; AgentState + turn task/lock) │   │
                                                                                       │   └──────────────────┬─────────────────────┘   │
                                                                                       │                      │ build_agent()           │
                                                                                       │   ┌──────────────────▼─────────────────────┐   │
                                                                                       │   │   AgentScope Agent  (public API)         │   │
                                                                                       │   │   reply_stream(inputs) -> AgentEvent     │   │
                                                                                       │   └──────────────────┬─────────────────────┘   │
                                                                                       │           AgentEvent │ stream                  │
                                                                                       │   ┌──────────────────▼─────────────────────┐   │
                                                                                       │   │   translate.py  AgentEvent -> SessionUpdate│  │
                                                                                       │   └──────────────────┬─────────────────────┘   │
                                                                                       │   ┌──────────────────▼─────────────────────┐   │
                                                                                       │   │   bridge.py  ClientBackend + permission  │   │
                                                                                       │   │   (shell delegation: fs/* + terminal/*;  │   │
                                                                                       │   │    Workspace sandbox opt-in)             │   │
                                                                                       │   └──────────────────────────────────────────┘   │
                                                                                       └──────────────────────────────────────────────────┘
```

**Internal layers**
1. **stdio JSON-RPC peer** — from the official SDK (`acp.run_agent` binds stdin/stdout; `AgentSideConnection` is the bidirectional peer; §16) or, as fallback, a vendored newline-delimited JSON-RPC-over-stdio peer (§6, §16). This layer owns framing, request-id/future bookkeeping, and the non-blocking receive loop.
2. **Session manager** — maps `SessionId` → an in-process `Session` holding the AgentScope `Agent`, its `AgentState`, the capability snapshot, the current **turn task + single-active-turn guard** (§16, §18), the awaiting-permission future, and the per-turn operation-id registry (§14).
3. **AgentScope Agent** — constructed via `build_agent()` (§7, §16). The kernel proper.
4. **AgentEvent → session/update translator** (`translate.py`) — parses each `AgentEvent` and emits the corresponding `session/update` variant (§10).
5. **fs / terminal / permission bridge** (`bridge.py`) — a single `ClientBackend(BackendBase)` implementing **all three** backend primitives (`read_file`/`write_file` → client `fs/*`; `exec_shell` → client `terminal/*`) under shell delegation, or a Workspace backend under the opt-in sandbox mode; owns the `_CURRENT_TOOL_CALL_ID` ContextVar that binds fs/terminal ops to their enclosing tool call (§14 c); converts `RequireUserConfirmEvent` ↔ `session/request_permission` (§12, §13).

**Built only on public API.** Nothing here subclasses or imports core-internal machinery. The one deliberate reuse from AG-UI is *conceptual*: the discriminated-union deserialization (`TypeAdapter(Annotated[AgentEvent, Field(discriminator="type")])`) and the event→protocol mapping shape.

**Contrast with the AG-UI HTTP middleware.** `ProtocolMiddlewareBase` is a Starlette `BaseHTTPMiddleware` that intercepts `text/event-stream` HTTP responses and lives behind the `service` extra (Brief B §9). We **do not** subclass it — that would drag FastAPI/uvicorn/AG-UI into an example that must stay lightweight. The example builds its **own** stdio peer and reuses only (a) the mapping idea and (b) the `TypeAdapter` discriminated-union deserialization of `AgentEvent`.

---

## 6. examples/acp/ file layout

Keep it small. PR1 is the peer + session manager + `build_agent()` + translator + the client-delegating backend (fs/* + terminal/*) + permission bridge + README.

```
examples/acp/
├── __main__.py        # `python -m acp_example` entry; calls server.main()
├── server.py          # serve-over-stdio: acp.run_agent(AcpAgent()); Agent-role handlers
│                       #   (initialize / session_new / prompt / cancel); child turn task + cancel
├── agent.py           # build_agent() factory — the single fixed-vs-exposed seam (§7)
├── session.py         # Session dataclass + in-process session manager; capability snapshot;
│                       #   turn task + single-active-turn guard; awaiting-permission future;
│                       #   per-turn operation-id registry (§14)
├── translate.py       # AgentEvent -> SessionUpdate mapping (§10, §11); the
│                       #   TypeAdapter(Annotated[AgentEvent, Field(discriminator="type")]) reuse
├── bridge.py          # fs/terminal/permission bridge (§12, §13):
│                       #   - ClientBackend(BackendBase): read_file/write_file -> fs/*,
│                       #       exec_shell -> terminal/* (implements ALL THREE primitives)
│                       #   - _CURRENT_TOOL_CALL_ID ContextVar (op-id binding, §14c)
│                       #   - RequireUserConfirmEvent <-> session/request_permission
│                       #   - Workspace opt-in wiring
├── config.py          # env parsing: model provider/creds, authority mode, agent name
├── requirements.txt   # agent-client-protocol==0.10.1 ; agentscope ; (model SDK extras)
└── README.md          # what it is; how to run; Zed setup; fixed-vs-exposed; demo transcript
```

Optional follow-on files (later PRs within the example, §20): `receipt.py` (derived receipt emitter), `persist.py` (`session/load` via `AgentState.model_dump`).

---

## 7. Positioning & configuration (Concern 2 resolved)

The example is **IN-BETWEEN**: a fixed runnable default, with agent construction isolated in `build_agent()` and model/creds via env.

- **The fixed default agent** — a general assistant with coding capabilities: an `agentscope.agent.Agent` named e.g. `"agentscope-acp"`, a coding-oriented `system_prompt`, a `Toolkit` carrying `Read`/`Write`/`Edit`/`Bash`/`Grep`/`Glob`, and a `PermissionMode.DEFAULT` permission posture.
- **`build_agent()` factory** — the *single* place agent construction lives (`agent.py`). It reads `config.py` (env), picks the model, builds the `Toolkit` (with the client-delegating backend by default, gated on client capabilities; or a Workspace backend when the sandbox env flag is set), and returns a constructed `Agent`. A forker edits this one function.
- **Env vars** — model provider + credentials + a few knobs; nothing agent-structural is hard-coded to a vendor.

### Fixed-vs-exposed matrix

| Concern | FIXED by the example author | EXPOSED to the user |
|---|---|---|
| ACP wiring (peer, routing, lifecycle) | Yes — `server.py`, `session.py` | No |
| AgentEvent→session/update mapping | Yes — `translate.py` | No |
| fs/terminal/permission bridge semantics | Yes — `bridge.py` (single `ClientBackend`) | Authority **mode** toggle only (env: `ACP_AUTHORITY=shell` \| `workspace`) |
| The default agent (name, system prompt, tool set, permission posture) | Yes — default inside `build_agent()` | Overridable by editing `build_agent()` (fork seam) |
| Model provider & class | Default provided | `ACP_MODEL_PROVIDER`, `ACP_MODEL_NAME` (env) |
| Credentials / API base | — | `*_API_KEY`, `*_BASE_URL` (env) — never committed |
| Permission mode / rules | Default `PermissionMode.DEFAULT` | `ACP_PERMISSION_MODE` (env) → `AgentState.permission_context.mode`; rules editable in `build_agent()` |
| Workspace sandbox (Local/Docker/E2B) | Off by default | Opt-in via `ACP_AUTHORITY=workspace` + backend env; wired in `build_agent()` |
| Advertised capabilities | Yes — computed from authority mode (§8) | Indirect (follows authority mode) |
| Tool gating on absent client caps | Yes — `build_agent()` omits tools whose channel is unavailable (§8, §13) | Indirect (follows client `fs`/`terminal` caps) |

**How a forker turns the example into a template.** (1) `cp -r examples/acp/ my-acp-agent/`. (2) Edit **only** `build_agent()` — swap the model, system prompt, tool set, permission rules, or drop in a custom `Agent` subclass. (3) Leave `server.py`/`session.py`/`translate.py`/`bridge.py` untouched (the ACP plumbing is generic). (4) Set env, point Zed at `python -m my_acp_agent`. Because the ACP surface never leaks into `build_agent()`, forking is a one-function edit.

---

## 8. Protocol surface

Target **protocol version `1`** (stable v1 schema 1.17.x). Method/field names are verbatim from Brief A. The SDK exposes `acp.PROTOCOL_VERSION = 1`.

### ACP Agent methods implemented (Client → Agent)

| Method | Kind | PR1? | Notes |
|---|---|---|---|
| `initialize` | request | **PR1** | Baseline. Returns `agentCapabilities`, `agentInfo`, `protocolVersion`. |
| `session/new` | request | **PR1** | Baseline. Requires `cwd`, `mcpServers`; returns `sessionId`. |
| `session/prompt` | request | **PR1** | Baseline. Drives one `reply_stream` turn; returns `stopReason`. One active turn per session (§16, §18). |
| `session/cancel` | notification | **PR1** | Baseline. Cancels the in-flight child turn task (§13, §16). |
| `authenticate` | request | PR1 (no-op) | Local posture: advertise no `authMethods` ⇒ never called; implement as a defensive no-op returning `{}`. |
| `logout` | request | PR1 (no-op) | `agentCapabilities.auth.logout` **not** advertised ⇒ MUST NOT be called; defensive `{}`. |
| `session/load` | request | Follow-on | Gated by `agentCapabilities.loadSession`; needs history replay (§9, §20). |
| `session/set_mode` / `session/set_config_option` | request | Follow-on | Only if we expose `modes`/`configOptions`. |
| `session/resume` / `session/close` / `session/list` / `session/delete` | request | Deferred | Gated by respective `sessionCapabilities.*`; not advertised in PR1. |

### ACP Client methods called (Agent → Client)

| Method | Kind | PR1? | Gate we require the client to advertise |
|---|---|---|---|
| `session/update` | notification | **PR1** | Baseline (client must accept). |
| `session/request_permission` | request | **PR1** | Baseline (client must implement). |
| `fs/read_text_file` | request | **PR1** (shell mode) | `clientCapabilities.fs.readTextFile` |
| `fs/write_text_file` | request | **PR1** (shell mode) | `clientCapabilities.fs.writeTextFile` |
| `terminal/create` / `terminal/output` / `terminal/wait_for_exit` / `terminal/kill` / `terminal/release` | request | **PR1** (shell mode) | `clientCapabilities.terminal` — **required in shell mode**: the builtin file tools call `exec_shell` for existence/dir/mkdir/search checks, so fs delegation depends on `terminal/*` too (§13). |

### Capabilities advertised at `initialize` (in `InitializeResponse.agentCapabilities`)

PR1 advertises a **minimal** `AgentCapabilities`:
- `loadSession`: `false` (PR1; flips to `true` when `session/load` lands).
- `promptCapabilities`: `{ image: false, audio: false, embeddedContext: false }` in PR1 — we accept baseline `text` and `resource_link` only. (Bump `image`/`embeddedContext` once we forward those to the model.)
- `mcpCapabilities`: `{ http: false, sse: false }` (stdio MCP is baseline and needs no capability).
- No `authMethods` (empty ⇒ `authenticate` never required); no `auth.logout`.
- `agentInfo`: `Implementation(name="agentscope-acp", title="AgentScope ACP Agent", version=...)`.

The example **reads** `clientCapabilities` from the `initialize` request and stores the snapshot (§14 invariant b). Shell mode requires **both** `fs.readTextFile`/`fs.writeTextFile` **and** `terminal`:
- If `fs.readTextFile`/`writeTextFile` are absent, `Read`/`Write`/`Edit` are **omitted** in shell mode (§13).
- If `terminal` is absent, the exec-backed tools (`Bash`, `Grep`, `Glob`) **and** the existence/dir/mkdir paths of `Read`/`Write`/`Edit` cannot run — all client-delegated file/exec tools are **omitted** in shell mode until the user opts into Workspace mode (§13).

---

## 9. Session lifecycle mapping

| ACP step | AgentScope mapping |
|---|---|
| `initialize` | Advertise `agentCapabilities` (§8); **snapshot** `clientCapabilities` + resolved authority mode into the `Session`-less connection state (used per-session at `session/new`). This snapshot is invariant (b) in §14. |
| `session/new` (`cwd`, `mcpServers`) | Mint a `Session`: construct a fresh `AgentState()` (its `session_id` is our internal id) and call `build_agent(cwd=..., state=..., conn=conn, caps=snapshot)` (keyword-only params exactly match §16). **We return the ACP `sessionId` = `AgentState.session_id`** so one stable id is shared end-to-end (invariant a). `cwd` becomes the tools' working directory (absolute); `mcpServers` (stdio baseline) can be passed into `Toolkit(mcps=...)` in a follow-on. |
| `session/prompt` (`prompt: ContentBlock[]`) | Translate prompt blocks → `Msg`/`UserMsg` content blocks (§11), then run **one turn** = `async for event in agent.reply_stream(inputs=user_msg)` inside a **child `asyncio.Task`** the handler awaits (§16). Each `AgentEvent` → `session/update` (§10). A second `session/prompt` for a `sessionId` whose turn is still in flight is **rejected** — single-active-turn-per-session (§18). The `session/prompt` request stays open for the whole turn. |
| `session/cancel` (`{sessionId}`) | Cancel the **child turn task** (`sess.turn_task.cancel()`, never the request handler; §13, §16). AgentScope converts the cancellation of a running tool into a `ToolResultState.INTERRUPTED` result; the turn resolves to `stopReason: "cancelled"`. |
| stop-reason | Map the turn's terminal `AgentEvent` → `StopReason` (table below), tracked explicitly in `_run_turn` (§16). |
| multi-session per connection | The single stdio connection holds `dict[SessionId, Session]`; each has its own `Agent`/`AgentState` and its own single-turn guard. The SDK's non-blocking receive loop lets multiple sessions/turns and client callbacks interleave (Brief C). |
| `authenticate` / `logout` | **Local no-op posture.** No `authMethods` advertised ⇒ the client creates sessions without auth. `authenticate` and `logout` are implemented as defensive no-ops (return `{}`); we never gate on auth locally. |

### Stop-reason mapping

| Turn outcome (AgentScope) | `PromptResponse.stopReason` |
|---|---|
| `reply_stream` completes normally (`ReplyEndEvent` seen as terminal, no pending confirm) | `end_turn` |
| `ExceedMaxItersEvent` (hit `ReActConfig.max_iters`) seen as terminal | `max_turn_requests` (closest ACP semantic: exceeded model requests within one turn) |
| Turn cancelled via `session/cancel` (child task cancelled) or client returns `{"outcome":"cancelled"}` | `cancelled` (MUST be returned even if cancellation raised underlying exceptions — Brief A §13) |
| Model/library reports a token cap | `max_tokens` — **imperfect mapping:** AgentScope has no dedicated "max tokens" turn-end event; PR1 does **not** synthesize this and falls back to `end_turn` unless a future signal exists (called out as a mapping gap). |
| Agent refuses | `refusal` — **imperfect mapping:** no first-class refusal event in the `AgentEvent` union; PR1 does not emit `refusal` (gap). |

**Pending-confirmation nuance.** When a turn yields `RequireUserConfirmEvent`, `reply_stream` *returns* (the generator completes) with a placeholder `AssistantMsg` (Brief B §3). This is **not** the end of the ACP turn: the example resolves permission via `session/request_permission`, feeds the result back with a resume `reply_stream(inputs=UserConfirmResultEvent(...))`, and only reports `stopReason` when the *resumed* stream reaches its terminal `ReplyEndEvent`/`ExceedMaxItersEvent`. See §12.

---

## 10. Event mapping table

Discriminator on every AgentEvent is `type` (a `Literal[EventType.*]`). Deserialize/branch with `TypeAdapter(Annotated[AgentEvent, Field(discriminator="type")])` (the AG-UI reuse). `session/update` variants are discriminated by `sessionUpdate`. All ids below are exact from Brief B; all ACP field names exact from Brief A.

| AgentEvent (`type`) | Key AgentScope fields | ACP `session/update` (`sessionUpdate`) | ACP fields | Notes |
|---|---|---|---|---|
| `ReplyStartEvent` (`REPLY_START`) | `session_id`, `reply_id`, `name` | — (turn boundary) | — | Marks turn start; **no direct ACP update** (ACP turn boundary is the open `session/prompt`). We record `reply_id` as the turn id (invariant a). |
| `ReplyEndEvent` (`REPLY_END`) | `session_id`, `reply_id` | — (turn boundary) | — | Terminal event when no confirm pending → resolve `stopReason` = `end_turn` (unless resumed for confirm). Tracked in `_run_turn` (§16). |
| `ModelCallStartEvent` (`MODEL_CALL_START`) | `reply_id`, `model_name` | *(none in PR1)* | — | Optional: could map to a UI step; PR1 drops it (no ACP equivalent that fits cleanly). |
| `ModelCallEndEvent` (`MODEL_CALL_END`) | `reply_id`, `input_tokens`, `output_tokens` | `usage_update` **only if both `used` and `size` are known — else OMIT** | `used`, `size`, `cost?` | **Imperfect:** ACP `usage_update` requires *both* `used` (tokens *in context*) and `size` (*total window*) as uint64; AgentScope gives per-call `input_tokens`/`output_tokens` and no window `size`. Since `size` is unknown, PR1 **omits the entire `usage_update`** (a size-less update is schema-invalid). Emit only if/when a total-window signal exists. Called out as a mapping gap. |
| `TextBlockStartEvent` (`TEXT_BLOCK_START`) | `reply_id`, `block_id` | `agent_message_chunk` | `content: ContentBlock`, `messageId = block_id` | Start of a streamed assistant text block; `messageId` = `block_id` correlates start/delta/end. |
| `TextBlockDeltaEvent` (`TEXT_BLOCK_DELTA`) | `reply_id`, `block_id`, `delta` | `agent_message_chunk` | `content: text_block(delta)`, `messageId = block_id` | Each delta → one chunk with the same `messageId`. |
| `TextBlockEndEvent` (`TEXT_BLOCK_END`) | `reply_id`, `block_id` | — | — | End marker; no ACP chunk needed (chunks are additive; a new `messageId` signals a new message). |
| `ThinkingBlockStartEvent` (`THINKING_BLOCK_START`) | `reply_id`, `block_id` | `agent_thought_chunk` | `content`, `messageId = block_id` | Reasoning stream. |
| `ThinkingBlockDeltaEvent` (`THINKING_BLOCK_DELTA`) | `reply_id`, `block_id`, `delta` | `agent_thought_chunk` | `content: text_block(delta)`, `messageId = block_id` | |
| `ThinkingBlockEndEvent` (`THINKING_BLOCK_END`) | `reply_id`, `block_id` | — | — | |
| `DataBlockStartEvent` / `Delta` / `End` (`DATA_BLOCK_*`) | `reply_id`, `block_id`, `media_type`, `data` (b64) | `agent_message_chunk` | `content: image_block/audio_block/resource` | Map by `media_type` → `image`/`audio`/embedded `resource`. PR1: emit as `agent_message_chunk` with the appropriate `ContentBlock`; if it's tool-produced, prefer the tool-call content channel. |
| `HintBlockEvent` (`HINT_BLOCK`) | `reply_id`, `block_id`, `source`, `hint` | `agent_message_chunk` (or drop) | `content: text_block(hint)` | One-shot, not streamed. PR1: render as a plain agent message chunk (no ACP "hint" concept). |
| `ToolCallStartEvent` (`TOOL_CALL_START`) | `reply_id`, `tool_call_id`, `tool_call_name` | `tool_call` | `toolCallId = tool_call_id`, `title = tool_call_name`, `kind` (mapped, §12), `status = "pending"` | First appearance of a tool call → `tool_call`. `toolCallId` is the operation id (invariant c). |
| `ToolCallDeltaEvent` (`TOOL_CALL_DELTA`) | `reply_id`, `tool_call_id`, `delta` (JSON arg fragment) | `tool_call_update` | `toolCallId`, `rawInput` (accumulated) | Stream args; ACP has no arg-delta, so we accumulate and send `rawInput` (or hold until end). |
| `ToolCallEndEvent` (`TOOL_CALL_END`) | `reply_id`, `tool_call_id` | `tool_call_update` | `toolCallId`, `rawInput` (final) | Args complete. Permission (§12) and execution follow. |
| `ToolResultStartEvent` (`TOOL_RESULT_START`) | `reply_id`, `tool_call_id`, `tool_call_name` | `tool_call_update` | `toolCallId`, `status = "in_progress"` | Execution began. **Also sets `_CURRENT_TOOL_CALL_ID` = `tool_call_id`** so the backend can bind its `fs/*`/`terminal/*` calls to this op id (invariant c; §14). |
| `ToolResultTextDeltaEvent` (`TOOL_RESULT_TEXT_DELTA`) | `reply_id`, `tool_call_id`, `delta` | `tool_call_update` | `toolCallId`, `content: [tool_content(text_block(buffered))]` | Buffer text keyed by `tool_call_id` (same approach as AG-UI); `content` *replaces* the collection, so send accumulated text. |
| `ToolResultDataDeltaEvent` (`TOOL_RESULT_DATA_DELTA`) | `reply_id`, `tool_call_id`, `block_id`, `media_type`, `data`/`url` | `tool_call_update` | `toolCallId`, `content: [tool_content(image/audio/resource)]` | Data output; attach to the call via `tool_call_id`. |
| `ToolResultEndEvent` (`TOOL_RESULT_END`) | `reply_id`, `tool_call_id`, `state: ToolResultState`, `metadata` | `tool_call_update` | `toolCallId`, `status = completed`/`failed`, `content` (final), `rawOutput` | Map `ToolResultState` → `status` + terminal taxonomy (invariant e; §14). `SUCCESS→completed`; `ERROR→failed`; `INTERRUPTED→failed` (+ receipt taxonomy `interrupted`); `DENIED→failed` (+ taxonomy `denied`). |
| `ExceedMaxItersEvent` (`EXCEED_MAX_ITERS`) | `reply_id`, `name` | — (turn end) | — | Terminal event → resolve turn with `stopReason = "max_turn_requests"` (§16). |
| `RequireUserConfirmEvent` (`REQUIRE_USER_CONFIRM`) | `reply_id`, `tool_calls: [ToolCallBlock]` | (drives) `session/request_permission` | see §12 | Not a `session/update`; triggers an outbound permission **request**. |
| `RequireExternalExecutionEvent` (`REQUIRE_EXTERNAL_EXECUTION`) | `reply_id`, `tool_calls` | (drives) client callback or `tool_call_update` | see §12 | PR1: not exercised by the default tool set; handled generically (resume via `ExternalExecutionResultEvent`). |
| `UserConfirmResultEvent` / `ExternalExecutionResultEvent` | — | — (resume inputs) | — | These are **inputs we feed back** into `reply_stream`, not outputs (§12). |
| `CustomEvent` (`CUSTOM`) | `name`, `value` | `_meta` on the enclosing update, or dropped | — | No stable ACP mapping; carry under `_meta` if needed. |

**Correlation summary.** Streamed text/thinking/data blocks correlate by **`block_id`** → ACP `messageId`. Tool call/result lifecycle correlates by **`tool_call_id`** → ACP `toolCallId` (the operation id). Turn id = **`reply_id`**; session id = **`session_id`** (= ACP `sessionId`). No log-scraping needed (§14).

---

## 11. Content model mapping

AgentScope message blocks (Brief B §6) → ACP `ContentBlock` (Brief A §6). ACP `ContentBlock` is MCP-compatible (`type` discriminator), so MCP tool outputs forward unchanged.

| AgentScope block | ACP target | Mapping |
|---|---|---|
| `TextBlock(text)` | `TextContent` (`type:"text"`) | `text` → `text`. Use `acp.helpers.text_block`. Baseline; always allowed. |
| `ThinkingBlock(thinking)` | `TextContent` inside `agent_thought_chunk` | Rendered via the **thought** channel: `session/update` `agent_thought_chunk` with a `text_block`. Not a distinct content *type* — it's the *update variant* that marks it as thinking (`acp.helpers.update_agent_thought`). |
| `ToolCallBlock(name, input, state)` | `ToolCall` / `ToolCallUpdate` | Not a ContentBlock — maps to the tool-call channel (§10, §12). `input` (JSON string) → `rawInput`. |
| `ToolResultBlock(output, state, metadata)` | `ToolCallContent[]` on `tool_call_update` | `output` text → `Content{type:"content"}` with `text_block`; `state` → `status` + taxonomy. |
| `DataBlock` w/ `Base64Source(data, media_type)` | `ImageContent` / `AudioContent` (`type:"image"`/`"audio"`) | If `media_type` is image/* → `image_block(data, mimeType)`; audio/* → `audio_block`. Prompt-direction use gated by `promptCapabilities.image`/`audio` (PR1 advertises false). |
| `DataBlock` w/ `URLSource(url, media_type)` | `ResourceLink` (`type:"resource_link"`) | `url` → `uri`; supply `name`, `mimeType`. Baseline in prompts. |
| `HintBlock(hint, source)` | `TextContent` | Rendered as agent text (no ACP hint concept). |
| Embedded file context (@-mention) | `EmbeddedResource` (`type:"resource"`) | Inbound prompt `resource`/`resource_link` → AgentScope text/data blocks. Requires `embeddedContext` capability for embedded `resource` (PR1: false, so we accept `resource_link` only). |

**Plan handling.** AgentScope has no first-class "plan" block in the public event/message union. ACP `plan` (`Plan{entries: PlanEntry[]}`) is therefore **not emitted in PR1**. If a future AgentScope planning event exists we would map it to `sessionUpdate: "plan"`; until then, plan is *out of scope* (explicitly noted, not fabricated). Todo/plan-like `CustomEvent`s, if a forker emits them, can be mapped to `plan` in `build_agent()`-specific translator hooks.

**MCP content-type reuse.** Because ACP `ContentBlock` mirrors MCP `ContentBlock`, tool results that already carry MCP-shaped content (from `Toolkit(mcps=...)`) can be forwarded into `tool_call_update.content` with minimal transformation.

---

## 12. Tool-call & permission model

### ToolCall lifecycle → `tool_call` / `tool_call_update`

Status transitions map AgentScope's tool events onto ACP `ToolCallStatus` (`pending`/`in_progress`/`completed`/`failed`):

```
ToolCallStartEvent      → tool_call            status=pending      (toolCallId, title, kind)
ToolCallDelta/End       → tool_call_update     status=pending      (rawInput accumulated)
[permission gate, §below]
ToolResultStartEvent    → tool_call_update     status=in_progress  (also sets _CURRENT_TOOL_CALL_ID)
ToolResult*Delta        → tool_call_update     (content replaced with buffered output)
ToolResultEndEvent      → tool_call_update     status=completed|failed  (content, rawOutput)
```

**`kind` mapping** (AgentScope tool name → ACP `ToolKind`): `Read` → `read`, `Grep`/`Glob` → `search`, `Write`/`Edit` → `edit`, `Bash` → `execute`, others → `other` (the default). This is a presentation hint only.

### RequireUserConfirmEvent ↔ `session/request_permission`

The AgentScope permission flow is a **resume** flow (Brief B §3), not an in-generator callback:

1. Inside a turn, `Agent._execute_tool_call` finds `check_permission` returns `ASK`/`PASSTHROUGH`, sets the `ToolCallBlock.state = ToolCallState.ASKING`, attaches `suggested_rules`, and **yields `RequireUserConfirmEvent(reply_id, tool_calls=[...])`**; `reply_stream` then returns with a placeholder `AssistantMsg`.
2. The example, on seeing `RequireUserConfirmEvent`, sends **`session/request_permission`** to the client for each tool call:
   - `sessionId`, `toolCall: ToolCallUpdate{ toolCallId = tool_call.id, title, rawInput }` (binds the request to the *exact operation id* — invariant d),
   - `options: PermissionOption[]` built from `PermissionOptionKind`: `allow_once`, `allow_always`, `reject_once`, `reject_always`.
   - The awaiting future is stored on the `Session` so a concurrent `session/cancel` can abort it (§16, §18).
3. The client returns `RequestPermissionResponse.outcome`:
   - `{"outcome":"selected","optionId":...}` → we map the chosen `PermissionOptionKind` to a `ConfirmResult(confirmed=..., tool_call=..., rules=...)`:
     - `allow_once` → `confirmed=True`, no rule.
     - `allow_always` → `confirmed=True`, `rules=[PermissionRule(tool_name=..., behavior=PermissionBehavior.ALLOW, source="acp-client")]` — **carried in `ConfirmResult.rules`** and applied by the agent internally when we resume via `UserConfirmResultEvent`. We use only this public path; we never touch the private `agent._engine` (§15 gap 6).
     - `reject_once` → `confirmed=False`, no rule.
     - `reject_always` → `confirmed=False`, `rules=[PermissionRule(..., behavior=PermissionBehavior.DENY, ...)]` (again carried in `ConfirmResult.rules`).
   - `{"outcome":"cancelled"}` → treat as a cancelled turn (§13): resolve with `stopReason: "cancelled"`.
4. We **feed the result back** by resuming: `agent.reply_stream(inputs=UserConfirmResultEvent(reply_id, confirm_results=[ConfirmResult(...)]))`. The agent validates against the awaiting tool-call ids, applies the decision (state `ALLOWED` + any user-edited name/input and any persisted `rules`, or a `DENIED` `ToolResultEnd`), and continues the react loop. We keep translating the resumed stream to `session/update` (§10). Partial confirmations are allowed (Brief B §3).

`suggested_rules` from AgentScope can be surfaced as extra `PermissionOption`s or in `_meta`; PR1 keeps the fixed four kinds.

### PermissionEngine composed with shell-mediated approval — no double-prompting

Two approval authorities exist: AgentScope's `PermissionEngine` (kernel) and the client's own permission UI (via `session/request_permission`, which the client MAY auto-allow/reject per user settings). To avoid double-prompting:

- The kernel's `PermissionEngine` decides **whether a permission is needed at all**. Only when it returns `ASK`/`PASSTHROUGH` do we surface `session/request_permission`. If the engine returns `ALLOW`/`DENY` outright, we do **not** prompt the client (the kernel already decided).
- The client is the **sole interactive prompt surface** for `ASK` cases. The kernel never renders its own prompt (it's a subprocess with no UI); it always delegates the *interactive* decision to the client via `session/request_permission`.
- Result: exactly one prompt per gated operation, at the client. `allow_always`/`reject_always` outcomes install a `PermissionRule` in the kernel (via `ConfirmResult.rules`) so future identical operations resolve to `ALLOW`/`DENY` without re-prompting.
- **Single-gate assumption.** The "exactly one prompt" guarantee assumes the client treats `session/request_permission` as the *sole* interactive gate and does **not** independently re-prompt on the resulting `fs/write_text_file` / `terminal/*` callbacks. If a client separately gates writes/commands, the user could see two prompts; ACP clients are expected not to.
- **No silent shell writes.** Because an outright `ALLOW` from the engine skips `session/request_permission` and the client then executes `fs/write_text_file`/`terminal/*` with no confirmation, `PermissionMode.DEFAULT` in `build_agent()` MUST map the write/execute tools (`Write`/`Edit`/`Bash`) to `ASK`, so a gated operation always surfaces exactly one client prompt *before* any side effect.
- **Permission is bound to the exact `toolCallId`/operation id**, not to prompt text — the `session/request_permission.toolCall.toolCallId` equals the AgentScope `tool_call_id`, and the resulting `ConfirmResult.tool_call` carries that id (invariant d).

---

## 13. Filesystem & terminal authority

**Default = shell delegation.** The ACP Client owns the local filesystem and terminal (it sees unsaved editor buffers). The kernel routes file/terminal *operations* to the client through **one** backend.

**One `ClientBackend(BackendBase)` implementing all three primitives.** `BackendBase` declares **three** `@abstractmethod` primitives — `exec_shell`, `read_file`, `write_file` — and a subclass MUST implement **all three** or it cannot be instantiated (`TypeError`). Moreover, the builtin file tools call `exec_shell` at runtime, so a client-delegating backend cannot be fs-only:

- `Read` runs `file_exists` + `is_dir` (base helpers that call `exec_shell(["test","-e"/"-d", path])`) *before* `read_file`.
- `Write` calls `exec_shell(["mkdir","-p", parent])` directly, plus `file_exists`.
- `Edit` calls `file_exists`.
- `Grep` shells out to ripgrep via `exec_shell`.
- `Glob` runs `find` and `is_dir` via `exec_shell`.

ACP has no `fs/exists`, `fs/is_dir`, `fs/list`, or `fs/search` method, so these existence/dir/mkdir/search paths can only route to `terminal/*`. The example therefore ships a **single** `ClientBackend` that routes:

- `read_file(path)` → `conn.read_text_file(path=<abs>, session_id=...)` → `fs/read_text_file` (returns content **including unsaved editor buffers** — the point of delegation),
- `write_file(path, data)` → `conn.write_text_file(content=..., path=<abs>, session_id=...)` → `fs/write_text_file`,
- `exec_shell(command, *, cwd, timeout)` → the client `terminal/*` create→wait→output→release pattern (below), used both directly by `Bash` and internally by `file_exists`/`is_dir`/`mkdir -p`/ripgrep/find.

`Edit` composes `read_file` + `write_file` (content) with `file_exists` (exec) through the same backend. Each `fs/*`/`terminal/*` call binds to the enclosing `tool_call_id` via the `_CURRENT_TOOL_CALL_ID` ContextVar (invariant c; §14).

**Consequence — terminal is in PR1.** fs-delegated `Read`/`Write`/`Edit` and exec-backed `Grep`/`Glob`/`Bash` **all** require the client `terminal` capability *in addition to* `fs.readTextFile`/`fs.writeTextFile`. The terminal bridge is therefore part of **PR1**, not a follow-on. (Existence/dir checks reflect *on-disk* state via `terminal/*` while content reads reflect *editor buffers* via `fs/*`; this split is an accepted approximation for the demo.)

- **Absolute-path requirement:** ACP mandates absolute paths. The backend resolves any relative path against the session `cwd` before calling `fs/*`/`terminal/*`; if resolution is impossible, it errors locally rather than sending a relative path.
- **`exec_shell` → `terminal/*`:** `terminal/create(command, args, cwd, env, outputByteLimit)` → race `terminal/wait_for_exit` against `timeout` → on timeout `terminal/kill` then `terminal/output` → always `terminal/release`; returns `ExecResult(exit_code, stdout, stderr)`. The returned `terminalId` is recorded against the enclosing op id (§14 c).

**Workspace opt-in sandbox mode.** Set `ACP_AUTHORITY=workspace`. In `build_agent()`, instead of the client-delegating backend, build the tools with **one** Workspace backend (`LocalBackend | DockerBackend | E2BBackend`, all public via `agentscope.tool` / `agentscope.workspace`), which implements all three primitives natively. `Read`/`Write`/`Edit`/`Grep`/`Glob`/`Bash` are constructed with `backend=<that backend>`, then `Toolkit(tools=[...])`. Same tools, **rebound** to a sandbox backend via injection — no core change, and no client `fs`/`terminal` capability needed. (We wire tools directly with `backend=...` rather than via `LocalWorkspace.list_tools()`, because `LocalWorkspace._backend` is hard-coded and not injectable — §15 gap 3.)

**Authority boundary.** Shell owns local file/terminal *mediation* by default; kernel owns agent planning, tool intent, and `AgentEvent` production. In Workspace mode the kernel owns the sandboxed FS/terminal too. The mode is captured in the capability snapshot (invariant b) so a receipt records whether each session was shell-delegated or Workspace-backed.

**Capability-absent handling (shell mode).**
- `terminal` absent: the exec-backed tools (`Bash`, `Grep`, `Glob`) **and** the existence/dir/mkdir paths of `Read`/`Write`/`Edit` cannot run without `exec_shell` — `build_agent()` **omits** all client-delegated file/exec tools and notes it in the capability snapshot; the user opts into Workspace mode for local tools. Never call a `terminal/*` method whose capability is false (Brief A §3 gating rule).
- `fs.readTextFile` / `fs.writeTextFile` absent: `Read`/`Write`/`Edit` are **omitted** (we do not silently fall back to touching local disk in shell mode — that would violate the shell-ownership contract); `Grep`/`Glob`/`Bash` may still run if `terminal` is present. Noted in the snapshot.

---

## 14. Receipt-ready event invariants (PR1 acceptance criteria)

The receipt *emitter* is an optional, derived, vendor-neutral follow-on (§20). **PR1 must nonetheless guarantee** that a receipt can be reconstructed *without log-scraping*. The five invariants and where each id comes from:

| # | Invariant | Source (reuse core id vs mint+map) |
|---|---|---|
| **a** | **One stable turn/session id** shared by `session/new`+`session/prompt`, emitted tool calls, permission requests, and the final stop reason. | **Reuse core.** Session id = `AgentState.session_id` = ACP `sessionId` (returned from `session/new`). Turn id = `reply_id` (on every event; carried on tool-call updates via correlation). The adapter binds the ACP `sessionId` to `AgentState.session_id` at `session/new` so they are the *same* string. |
| **b** | **Initialized capability snapshot**, including whether fs/terminal are **shell-delegated** or **Workspace-backed** for that session. | **Mint+map at adapter.** Captured at `initialize` (client caps) + resolved at `session/new` (authority mode from env). Stored on the `Session`; attachable under `_meta` on the first `session/update`. |
| **c** | **Stable operation id for every `fs/*`, `terminal/*`, and permission-gated tool call.** | **Reuse core id, threaded via ContextVar.** Tool-call op id = `tool_call_id` (= ACP `toolCallId`). Because `BackendBase.{read_file,write_file,exec_shell}` receive **no** enclosing `tool_call_id`, the adapter sets a module-level `ContextVar` (`_CURRENT_TOOL_CALL_ID`) when it observes `ToolResultStartEvent` (execution-begin, which precedes any backend call for that tool in the shared async-generator context); `ClientBackend` reads it to bind every `fs/*`/`terminal/*` call to the enclosing `tool_call_id`. `terminal/create`'s returned `terminalId` is also recorded against the op id. For a (rare) client call made outside any tool, the adapter mints a `uuid4` op id. The ContextVar reliance is a soft core gap (§15 gap 5) — a first-class backend-call context would be more robust. |
| **d** | **Permission decision bound to the operation id**, not only to prompt text. | **Reuse core.** `session/request_permission.toolCall.toolCallId` = `tool_call_id`; the returned outcome is mapped into a `ConfirmResult(tool_call=<same id>)`. The decision record keys on `tool_call_id`. |
| **e** | **Terminal result state taxonomy:** `completed` / `denied` / `canceled` / `failed` / `interrupted`. | **Mint+map at adapter,** derived from `ToolResultState` + ACP outcomes: `SUCCESS→completed`; `ERROR→failed`; `INTERRUPTED→interrupted` (from `asyncio` cancel converted by `toolkit.call_tool`); `DENIED→denied`; turn/`session/cancel`→`canceled`. Terminal exit info (`exitCode`/`signal` from `terminal/wait_for_exit`) attaches to the op id. |

The adapter therefore reuses `session_id`, `reply_id`, `tool_call_id`, `block_id` directly (Brief B §2 "run-receipt correlation"), and mints only (b) the capability snapshot, (c) op ids for out-of-tool client calls, and (e) the taxonomy label. `Msg.append_event` (Brief B §6) can replay the same event stream into a `Msg` — useful for the follow-on receipt emitter — but PR1 only needs to *preserve and attach* the ids above.

---

## 15. Public-API-only implementation notes

Exact public symbols the example imports (all from AgentScope subpackages; top-level `agentscope` only re-exports `logger, setup_logger, set_id_factory, __version__`):

- **`agentscope.agent`**: `Agent`, `ContextConfig`, `ReActConfig`, `ModelConfig` (config seams for `build_agent()`).
- **`agentscope.agent.Agent.reply_stream`** — the turn/streaming API (`AsyncGenerator[AgentEvent, None]`); `Agent.reply` as the non-streaming fallback.
- **`agentscope.event`**: the `AgentEvent` union and every subtype used in §10 — `ReplyStartEvent`, `ReplyEndEvent`, `ModelCallStartEvent`, `ModelCallEndEvent`, `TextBlock{Start,Delta,End}Event`, `ThinkingBlock{Start,Delta,End}Event`, `DataBlock{Start,Delta,End}Event`, `HintBlockEvent`, `ToolCall{Start,Delta,End}Event`, `ToolResult{Start,TextDelta,DataDelta,End}Event`, `ExceedMaxItersEvent`, `RequireUserConfirmEvent`, `RequireExternalExecutionEvent`, `UserConfirmResultEvent`, `ExternalExecutionResultEvent`, `CustomEvent`; `EventType`.
- **`agentscope.permission`**: `PermissionEngine`, `PermissionMode`, `PermissionBehavior`, `PermissionRule`, `PermissionContext`, `PermissionDecision`; feed-back via `UserConfirmResultEvent` + `ConfirmResult` (from `agentscope.event`/`message`).
- **`agentscope.tool`**: `Toolkit`; builtin tools `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`; backend seam `BackendBase`, `LocalBackend`, `ExecResult`.
- **`agentscope.workspace`**: `WorkspaceBase`, `LocalWorkspace`, and (opt-in sandbox) `DockerBackend`, `E2BBackend`.
- **`agentscope.message`**: `Msg`, `UserMsg`, `AssistantMsg`, `SystemMsg`; blocks `TextBlock`, `ThinkingBlock`, `ToolCallBlock`, `ToolResultBlock`, `DataBlock`, `HintBlock`, `Base64Source`, `URLSource`; enums `ToolCallState`, `ToolResultState`; `Msg.append_event` (receipt replay).
- **`agentscope.state`**: `AgentState` (session id + context + `permission_context`), `model_dump`/`model_validate` for DIY persistence.
- **Deserialization pattern (reused idea, not import):** `pydantic.TypeAdapter(Annotated[AgentEvent, Field(discriminator="type")])` — the same discriminated-union approach as `_deserialize_event` in the AG-UI middleware. We do **not** import `ProtocolMiddlewareBase`/`AGUIProtocolMiddleware` (HTTP/`service`-scoped).

**None of the above requires a core change.** Confirmed against Brief B's capability table.

### Core gaps to raise separately (NOT patched in this doc)

1. **Hard interrupt of a running turn — MISSING.** No `Agent.interrupt()/stop()/cancel()`. The example cancels the child `asyncio.Task` wrapping `reply_stream`; `toolkit.call_tool` converts the cancellation into a `ToolResultState.INTERRUPTED` result (cooperative only). A first-class interrupt API would be a core change — raise as a separate issue.
2. **Confirm-feedback ergonomics.** Feeding a permission result back is supported but only via a *resume* `reply_stream(inputs=UserConfirmResultEvent(...))` (the generator returns first). Workable, but a live in-generator callback would be cleaner — note as a potential ergonomic gap, not a blocker.
3. **Backend injection via `LocalWorkspace`.** `LocalWorkspace._backend = LocalBackend()` is hard-coded (not a constructor param). Avoidable by wiring tools directly with `backend=...`; flag as a minor gap if Workspace-level injection is ever wanted.
4. **`usage_update` fidelity.** AgentScope exposes per-call `input_tokens`/`output_tokens`, not context-window `used`/`size`. Emitting a faithful ACP `usage_update` (which requires *both* `used` and `size`) would need a total-window signal — until then we omit `usage_update` entirely (§10). Note as a data gap, not a core change request.
5. **No mechanism to thread the enclosing `tool_call_id` into a `BackendBase` call.** The three backend primitives (`read_file(path)`, `write_file(path,data)`, `exec_shell(command,*,cwd,timeout)`) carry no call context, so a backend cannot natively bind its `fs/*`/`terminal/*` calls to the originating tool call. The example works around it with the `_CURRENT_TOOL_CALL_ID` ContextVar set at `TOOL_RESULT_START` (§14 c), which relies on the async generator sharing the consumer task's context. A first-class backend-call context (contextvar or parameter threaded by core) would be more robust — raise separately.
6. **No public API to add a `PermissionRule` to a running `Agent`'s engine.** `Agent._engine` is a private attribute with no public accessor. `allow_always`/`reject_always` rules are therefore fed back **only** via `ConfirmResult.rules` on the resume `UserConfirmResultEvent` (the agent applies them internally); the example never touches `agent._engine`. If direct installation is ever wanted, raise it as a core gap.

All six are **flagged**, not fixed here.

---

## 16. API sketch (Python)

`build_agent()` factory (`agent.py`) — the single fixed-vs-exposed seam:

```python
# agent.py
import os
from agentscope.agent import Agent
from agentscope.tool import Toolkit, Read, Write, Edit, Bash, Grep, Glob, BackendBase
from agentscope.state import AgentState
from agentscope.permission import PermissionMode
# from agentscope.tool import LocalBackend
# from agentscope.workspace import DockerBackend, E2BBackend  # opt-in sandbox


def _make_model():
    """Model + credentials strictly from env (nothing vendor-hardcoded structurally)."""
    provider = os.environ.get("ACP_MODEL_PROVIDER", "dashscope")
    name = os.environ.get("ACP_MODEL_NAME", "qwen-max")
    # ... construct and return a ChatModelBase from the chosen provider + *_API_KEY ...
    return _build_chat_model(provider, name)


def _shell_tools(backend: BackendBase, cwd: str, caps) -> list:
    """Gate client-delegated tools on the client's fs/terminal capabilities (§13).

    Every builtin file tool calls exec_shell (file_exists/is_dir/mkdir/ripgrep/find),
    so all of them need `terminal`; Read/Write/Edit additionally need fs read/write.
    """
    fs_ok = bool(caps and caps.fs and caps.fs.read_text_file and caps.fs.write_text_file)
    term_ok = bool(caps and caps.terminal)
    tools: list = []
    if fs_ok and term_ok:                       # content via fs/*, exists/dir/mkdir via terminal/*
        tools += [Read(backend=backend), Write(backend=backend), Edit(backend=backend)]
    if term_ok:                                 # ripgrep / find / shell -> exec_shell -> terminal/*
        tools += [Grep(backend=backend), Glob(backend=backend), Bash(cwd=cwd, backend=backend)]
    return tools                                # may be empty -> agent runs with no file tools


def build_agent(*, cwd: str, state: AgentState, conn, caps) -> Agent:
    """The ONLY place agent construction lives. Forkers edit this function.

    - `conn`   : the ACP Client-side connection (implements acp.interfaces.Client)
    - `caps`   : the initialize-time capability snapshot (invariant b)
    - authority mode chosen from env: 'shell' (default) or 'workspace'
    """
    mode = os.environ.get("ACP_AUTHORITY", "shell")

    if mode == "workspace":
        # OPT-IN sandbox: ONE backend implements all three primitives natively;
        # no client fs/terminal capability required.
        backend: BackendBase = _make_workspace_backend()   # LocalBackend / DockerBackend / E2BBackend
        tools = [Read(backend=backend), Write(backend=backend), Edit(backend=backend),
                 Grep(backend=backend), Glob(backend=backend), Bash(cwd=cwd, backend=backend)]
    else:
        # DEFAULT: shell delegation. ONE ClientBackend implements ALL THREE BackendBase
        # primitives: read_file/write_file -> fs/*, exec_shell -> terminal/*.
        from .bridge import ClientBackend                    # deferred: only shell mode needs it
        backend = ClientBackend(conn=conn, session_id=state.session_id, cwd=cwd)
        tools = _shell_tools(backend, cwd, caps)             # gated on fs.* + terminal caps (§13)

    toolkit = Toolkit(tools=tools)

    # Permission posture. DEFAULT maps Write/Edit/Bash -> ASK so no side effect ever
    # happens without exactly one client prompt (§12). Rules editable here by a forker.
    state.permission_context.mode = PermissionMode(
        os.environ.get("ACP_PERMISSION_MODE", "default")
    )

    return Agent(
        name=os.environ.get("ACP_AGENT_NAME", "agentscope-acp"),
        system_prompt=_CODING_ASSISTANT_SYSTEM_PROMPT,
        model=_make_model(),
        toolkit=toolkit,
        state=state,
    )
```

Serve-over-stdio entrypoint (`server.py` + `__main__.py`), using the official SDK (`agent-client-protocol==0.10.1`, import `acp`; Brief C). The turn runs in a **child task** the handler awaits; `session/cancel` cancels **that child** (never the request handler), and `_run_turn` converts cancellation into `stop_reason="cancelled"`:

```python
# server.py
import asyncio
from typing import Any

from acp import (
    Agent as AcpAgentBase, PROTOCOL_VERSION, run_agent, RequestError,
    InitializeResponse, NewSessionResponse, PromptResponse,
)
from acp.interfaces import Client
from acp.schema import AgentCapabilities, ClientCapabilities, Implementation

from agentscope.state import AgentState
from agentscope.event import (
    UserConfirmResultEvent, RequireUserConfirmEvent,
    ToolResultStartEvent, ReplyEndEvent, ExceedMaxItersEvent,
)
from .agent import build_agent
from .session import Session, SessionManager
from .translate import event_to_update            # AgentEvent -> session/update
from .bridge import request_permission_for, _CURRENT_TOOL_CALL_ID
# request_permission_for(): RequireUserConfirmEvent -> session/request_permission -> ConfirmResult | None


def _stop_reason_for(terminal_event) -> str:
    if isinstance(terminal_event, ExceedMaxItersEvent):
        return "max_turn_requests"
    return "end_turn"                              # ReplyEndEvent (or normal completion)


class AcpAgent(AcpAgentBase):
    _conn: Client

    def __init__(self) -> None:
        self._client_caps: ClientCapabilities | None = None
        self._sessions = SessionManager()

    def on_connect(self, conn: Client) -> None:
        self._conn = conn                          # how the kernel talks back to the shell

    async def initialize(self, protocol_version: int,
                         client_capabilities: ClientCapabilities | None = None,
                         client_info: Implementation | None = None,
                         **kw: Any) -> InitializeResponse:
        self._client_caps = client_capabilities    # snapshot (invariant b)
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(),           # minimal (§8)
            agent_info=Implementation(name="agentscope-acp",
                                      title="AgentScope ACP Agent", version="0.1.0"),
        )

    async def new_session(self, cwd: str, mcp_servers=None,
                         additional_directories=None, **kw: Any) -> NewSessionResponse:
        state = AgentState()                        # its session_id becomes the ACP sessionId (invariant a)
        agent = build_agent(cwd=cwd, state=state, conn=self._conn,
                            caps=self._client_caps)
        self._sessions.add(Session(id=state.session_id, agent=agent, state=state,
                                   caps=self._client_caps, authority=_authority_env()))
        return NewSessionResponse(session_id=state.session_id, modes=None)

    async def prompt(self, prompt: list, session_id: str, **kw: Any) -> PromptResponse:
        sess = self._sessions.get(session_id)
        if not sess.try_begin_turn():               # single-active-turn-per-session (§18)
            raise RequestError(code=-32603,
                               message="a turn is already in progress for this session")
        try:
            user_msg = _prompt_blocks_to_msg(prompt)            # §11
            # Run the turn in a CHILD task so session/cancel can cancel the turn,
            # not this request handler. _run_turn converts cancellation to "cancelled".
            sess.turn_task = asyncio.create_task(self._run_turn(sess, inputs=user_msg))
            stop = await sess.turn_task
        finally:
            sess.end_turn()
        return PromptResponse(stop_reason=stop)     # ALWAYS returned, even on cancel

    async def _run_turn(self, sess: Session, inputs) -> str:
        """One ACP turn = a reply_stream, possibly resumed across permission gates."""
        try:
            while True:
                pending_confirm: RequireUserConfirmEvent | None = None
                terminal_event = None
                async for event in sess.agent.reply_stream(inputs=inputs):
                    if isinstance(event, RequireUserConfirmEvent):
                        pending_confirm = event
                        break
                    if isinstance(event, ToolResultStartEvent):
                        _CURRENT_TOOL_CALL_ID.set(event.tool_call_id)   # op-id binding (invariant c)
                    if isinstance(event, (ReplyEndEvent, ExceedMaxItersEvent)):
                        terminal_event = event                          # remember how the turn ended
                    update = event_to_update(event, sess)               # -> None or a session/update
                    if update is not None:
                        await self._conn.session_update(session_id=sess.id, update=update)
                if pending_confirm is None:
                    return _stop_reason_for(terminal_event)             # end_turn | max_turn_requests
                # Resolve permission at the CLIENT, bound to the toolCallId (invariant d):
                confirm_results = await request_permission_for(self._conn, sess, pending_confirm)
                if confirm_results is None:                             # client -> {"outcome":"cancelled"}
                    return "cancelled"
                inputs = UserConfirmResultEvent(reply_id=pending_confirm.reply_id,
                                                confirm_results=confirm_results)
        except asyncio.CancelledError:
            await sess.abort_pending_permission()   # cancel any awaiting request_permission future
            return "cancelled"                      # convert cancel -> stop reason (do NOT re-raise)

    async def cancel(self, session_id: str, **kw: Any) -> None:
        sess = self._sessions.get(session_id)
        task = sess.turn_task
        if task is not None and not task.done():
            task.cancel()                           # cancels the CHILD turn task, not this handler


async def main() -> None:
    await run_agent(AcpAgent())      # binds stdin/stdout, 50 MB buffer, runs until disconnect


# __main__.py
# import asyncio; from .server import main; asyncio.run(main())
```

The `Session` (`session.py`) holds `agent`, `state`, `caps`, `authority`, `turn_task`, a single-active-turn guard (`try_begin_turn()`/`end_turn()`), the awaiting-permission future (`abort_pending_permission()`), and the per-turn op-id registry (§14).

**Vendored fallback (if the SDK is judged immature).** Replace `run_agent(AcpAgent())` with a local newline-delimited JSON-RPC-over-stdio peer: `readuntil(b"\n")` framing (raise the stdin limit for multimodal), `json.dumps(obj)+"\n"` + flush per message, a monotonic id counter with a `pending[id]->Future` map, a method→handler table for `session/*`, and a **task-per-inbound-request** receive loop so `prompt` can `await` `fs/*`/`terminal/*`/`session/request_permission` mid-turn and `session/cancel` dispatches concurrently (Brief C "If you ever had to vendor…"). This is exactly the machinery the SDK already provides — hence the SDK is preferred and pinned in `examples/acp/requirements.txt`, **not** a core dependency.

---

## 17. End-to-end sequence (desktop, Agent role)

```
Shell (ACP Client, e.g. Zed)                    examples/acp/ kernel (ACP Agent)
────────────────────────────                    ────────────────────────────────
1. spawn subprocess: `python -m acp_example`  ─▶ acp.run_agent binds stdin/stdout;
                                                  on_connect(conn) stores the Client handle
2. → initialize { protocolVersion:1,
      clientCapabilities:{fs:{readTextFile,      ◀─ InitializeResponse { protocolVersion:1,
      writeTextFile}, terminal} }                     agentCapabilities:{}, agentInfo }
                                                   (snapshot clientCapabilities — invariant b)
3. → session/new { cwd:"/abs/proj",
      mcpServers:[...] }                          build_agent(cwd=..., state=..., conn=conn, caps=...);
                                                  ◀─ NewSessionResponse { sessionId = state.session_id }
                                                   (sessionId == AgentState.session_id — invariant a)
4. → session/prompt { sessionId,
      prompt:[text_block("fix the bug in x.py")]}  try_begin_turn(); turn runs in a CHILD task:
                                                     agent.reply_stream(inputs=UserMsg)
                                                   (request stays OPEN for the whole turn)
5.                                             ◀─ session/update agent_thought_chunk (messageId=block_id)
6.                                             ◀─ session/update agent_message_chunk (streamed text)
7.                                             ◀─ session/update tool_call
                                                     { toolCallId, title:"Read", kind:"read",
                                                       status:"pending" }        (op id — invariant c)
8.  [permission gate: engine returns ASK]      ◀─ session/request_permission
                                                     { sessionId, toolCall:{toolCallId}, options:[
                                                        allow_once, allow_always,
                                                        reject_once, reject_always] }
9. → { outcome:"selected", optionId:"allow_once" }
                                                   map -> ConfirmResult(confirmed=True, tool_call=<id>)
                                                   resume: reply_stream(inputs=UserConfirmResultEvent)
                                                   (decision bound to toolCallId — invariant d)
10.                                            ◀─ session/update tool_call_update {status:"in_progress"}
                                                   (_CURRENT_TOOL_CALL_ID := toolCallId — invariant c)
11.  [Read: file_exists/is_dir via exec_shell] ◀─ terminal/create { command:"test", args:["-e", ...] } ...
                                               ◀─ fs/read_text_file { path:"/abs/proj/x.py", sessionId }
    → { content:"...file (incl. unsaved buffer)..." }
12.  [Edit: write via fs/*]                    ◀─ fs/write_text_file { path, content }
    → {}
13.                                            ◀─ session/update tool_call_update
                                                     { status:"completed", content:[diff/text], rawOutput }
                                                   (ToolResultState.SUCCESS -> completed — invariant e)
14.  [model has no more tool calls -> ReplyEndEvent]
                                               ◀─ PromptResponse { stopReason:"end_turn" }  (closes step 4)
```

If the user cancels mid-turn: `→ session/cancel {sessionId}` (notification) arrives concurrently → the kernel cancels the **child turn task** (`sess.turn_task.cancel()`, **not** the request handler) → any running tool yields `INTERRUPTED`. For an outstanding `session/request_permission` (Agent→Client), the **client** MUST return `{"outcome":"cancelled"}`; the kernel cancels its awaiting future, resolves the turn cancelled, and responds to the still-open `session/prompt` with `stopReason:"cancelled"` (§13/§18).

---

## 18. Error handling & edge cases

| Case | Handling |
|---|---|
| **JSON-RPC errors** | Map handler exceptions to JSON-RPC `Error{code,message}` (SDK does this). Use `-32602` invalid params for malformed request payloads, `-32601` method not found for unknown/optional methods, `-32603` internal error otherwise, `-32002` resource-not-found when the client reports a missing file. Notifications never get responses. |
| **Cancellation mid-tool** | `session/cancel` → cancel the **child turn task** (`sess.turn_task.cancel()`), never the request-handler task. `_run_turn` catches `CancelledError`, aborts any awaiting `session/request_permission` future, and returns `"cancelled"`; `toolkit.call_tool` catches `asyncio.CancelledError` inside a running tool and emits `ToolResultState.INTERRUPTED`. Do **not** leak as a JSON-RPC error — the open `session/prompt` returns `stopReason:"cancelled"` (Brief A §13). Receipt taxonomy: `interrupted` (tool) / `canceled` (turn). |
| **Cancellation mid-permission** | If `session/cancel` arrives while a `session/request_permission` is outstanding, the **client** MUST return `{"outcome":"cancelled"}`; the kernel also cancels its awaiting future (`abort_pending_permission()`) and resolves the turn `cancelled`. Any nested `terminal/*` requests may receive `$/cancel_request`/`-32800`. |
| **Second `session/prompt` while a turn is active** | Rejected. One `Agent`/`AgentState` per session must not be driven by two concurrent `reply_stream`s (context/state corruption). The `Session` guards a single-active-turn flag (`try_begin_turn()`); a second prompt for a busy `sessionId` returns a JSON-RPC error (`-32603`). **Single-active-turn-per-session** invariant; different sessions remain fully independent. |
| **Permission denial** | `reject_once`/`reject_always` → `ConfirmResult(confirmed=False)` → agent emits a `DENIED` `ToolResultEnd` → `tool_call_update{status:"failed"}` (taxonomy `denied`). Turn continues (model sees the denial) unless `ReActConfig.stop_on_reject=True`. |
| **fs/terminal errors** | Client returns a JSON-RPC error for `fs/*`/`terminal/*`; the backend surfaces it as an `ExecResult` non-zero exit or a raised read/write error, which the tool converts into a `ToolResultState.ERROR` → `tool_call_update{status:"failed"}`. Absolute-path violations rejected before the call. |
| **Capability absent** | Never call an fs/terminal method whose capability is false (§13). Degrade (omit the affected tool in `build_agent()`) rather than fall back to silently touching local disk in shell mode. |
| **Large tool outputs / streaming backpressure** | ACP `terminal/create.outputByteLimit` truncates from the beginning; `ToolResultTextDelta` buffering keyed by `tool_call_id` sends accumulated `content` (which *replaces* the collection). SDK's 50 MB buffer covers large multimodal frames; for very large text we rely on `ContextConfig.tool_result_limit` (default 50000) inside AgentScope to cap what re-enters context. |
| **Malformed frames** | One JSON object per line, no embedded newlines (serialize compactly). SDK handles `readuntil(b"\n")`/`LimitOverrunError`; a malformed inbound frame → `-32700` parse error. The kernel MUST NOT write non-ACP text to stdout (logs go to **stderr**). |
| **Concurrent client calls during a turn** | The SDK receive loop is non-blocking (task-per-inbound), so the child turn task can `await` `fs/*`/`terminal/*`/`session/request_permission` while the loop keeps reading, and `session/cancel` dispatches concurrently. Multiple sessions on one connection are independent (`dict[SessionId, Session]`); each enforces its own single-active-turn guard. |
| **Unknown/underscore methods** | Custom `_`-prefixed requests we don't implement → `-32601`; custom notifications we don't recognize → ignore. |

---

## 19. Testing & demo

1. **Mock ACP Client harness.** A pytest fixture that drives the kernel over an in-memory pair of streams (or a spawned subprocess): sends `initialize` → `session/new` → `session/prompt`, records every `session/update`, answers `session/request_permission`, and serves `fs/*`/`terminal/*`. Asserts the turn ends with the expected `stopReason`. Reuse the SDK's client-side classes for realism.
2. **Unit tests for `translate.py`.** Feed synthetic `AgentEvent` sequences (built from `agentscope.event` types) through `event_to_update` and assert exact `sessionUpdate` variants + fields (§10), including `block_id→messageId` and `tool_call_id→toolCallId` correlation, and that `usage_update` is omitted when window `size` is unknown.
3. **Permission round-trip.** Assert `RequireUserConfirmEvent` → `session/request_permission{toolCall.toolCallId==tool_call_id}` → each `PermissionOptionKind` → correct `ConfirmResult` (with `allow_always`/`reject_always` carrying `rules`), and that resume via `UserConfirmResultEvent` continues the turn.
4. **Cancellation & concurrency.** Assert `session/cancel` cancels the child turn task and the open `session/prompt` returns `stopReason:"cancelled"` (not a JSON-RPC error); assert a cancel during an outstanding `session/request_permission` resolves `cancelled`; assert a second `session/prompt` on a busy session is rejected (`-32603`) while a different session proceeds.
5. **e2e against a real shell (Zed).** Point Zed's ACP agent config at `python -m acp_example`; run a scripted coding task (read a file, propose an edit, approve, write). Capture the transcript in `README.md`.
6. **Conformance vs the JSON schema.** Validate outbound frames against ACP `schema/v1/schema.json` (1.17.x). The pinned SDK's generated Pydantic models give most of this for free; add a schema-validation test for hand-built `_meta`.
7. **Invariants as acceptance criteria.** A test asserts all five invariants (§14) hold across a full turn: single `sessionId`/`reply_id` threaded through; capability snapshot present; every gated `fs/*`/`terminal/*`/tool op carries a stable op id bound via `_CURRENT_TOOL_CALL_ID`; permission decision keyed on `toolCallId`; terminal/tool taxonomy correctly labeled.

---

## 20. Phasing / milestones

**Phase 1 — `examples/acp/` (this doc).**
- **PR1 (minimal end-to-end, demoable against Zed):** stdio peer (SDK) + session manager (with single-active-turn guard) + `build_agent()` + `translate.py` (text/thinking/tool-call/tool-result/stop-reason) + `session/request_permission` bridge + the single `ClientBackend` delivering **shell delegation over both `fs/*` and `terminal/*`**, enabling `Read`/`Write`/`Edit` (content via `fs/*`, existence/dir/mkdir via `terminal/*`) and `Grep`/`Glob`/`Bash` (exec via `terminal/*`), gated on client `fs`/`terminal` capabilities. Turn runs in a cancellable child task; `session/cancel` yields `stopReason:"cancelled"`. Satisfies the five invariants (§14).
- **Follow-ons within the example:**
  1. **Receipt emitter** — optional, derived, vendor-neutral; consumes the invariant-carrying stream (reuse `Msg.append_event`).
  2. **`session/load`** — flip `loadSession=true`; persist/replay via `AgentState.model_dump`/`model_validate`; replay history as `session/update` chunks before responding.
  3. **Workspace sandbox mode** — `ACP_AUTHORITY=workspace` fully wired and exercised with `LocalBackend`/`DockerBackend`/`E2BBackend`.
  4. **Session modes / config options** — expose `modes`/`configOptions` and implement `session/set_mode` / `session/set_config_option`.

**Phase 2 — reassess SDK promotion.** Once the example proves which abstractions matter and ACP stabilizes, decide among: an in-tree `agentscope/acp/` module, a separate `agentscope-acp` package, or an in-between. Merging proven functionality is easier than removing unproven functionality.

---

## 21. Open questions

**Resolved (recorded for the discussion):**
- **fs/terminal default → RESOLVED: shell-delegated.** The ACP Client owns the workspace and unsaved buffers by default; Workspace (Local/Docker/E2B) is opt-in. Shell mode delegates content via `fs/*` and existence/dir/exec via `terminal/*` through one `ClientBackend`, so both capabilities are required in PR1 (§13).
- **Positioning → RESOLVED: in-between.** Fixed runnable default agent, `build_agent()` seam, env config (§7).

**Genuinely open (surfaced by research):**
1. **Hard interrupt.** No `Agent.interrupt()`; the example cancels the child `asyncio.Task` (cooperative). Should a first-class interrupt be raised as a core gap? (Recommended: yes, separately — §15 gap 1.)
2. **Threading `tool_call_id` into the backend.** The `_CURRENT_TOOL_CALL_ID` ContextVar workaround (§14 c) relies on shared async-generator context. Is a first-class backend-call context worth a core proposal? (§15 gap 5.)
3. **`stopReason` fidelity.** `max_tokens` and `refusal` have no clean `AgentEvent` source; PR1 falls back to `end_turn`/`max_turn_requests`. Do we need core signals for these?
4. **`usage_update` fidelity.** Per-call `input_tokens`/`output_tokens` vs ACP's context-window `used`/`size` (both required) — PR1 omits `usage_update`. Worth a core total-window signal?
5. **Plan mapping.** No first-class plan event in AgentScope; `sessionUpdate:"plan"` is unmapped in PR1. Worth a `CustomEvent`-based convention?
6. **SDK maturity / version churn.** `agent-client-protocol` is pre-1.0 (0.10.1, schema pinned to upstream `v0.13.6`); ACP itself is pre-1.0 with a known v1→v2 migration (`authenticate`→`auth/login`, `session/set_mode` removed, `fs/*`+`terminal/*` restructured out of the stable v2 client registry — Brief A §17). Pin `agent-client-protocol==0.10.1`, target protocol version `1`, and keep the thin adapter so a rename is a one-file change.
7. **Confirm-feedback ergonomics.** Resume-via-`UserConfirmResultEvent` works but is indirect; is a live callback worth a future core proposal? (§15 gap 2.)
8. **Rule installation on a running engine.** Rules are fed back only via `ConfirmResult.rules` (no public `Agent`-level engine accessor; §15 gap 6). Should a public API to add a `PermissionRule` to a live `Agent` be raised as a core gap?

---

## 22. References

**ACP specification (target protocol version `1`, schema 1.17.x)** — `github.com/agentclientprotocol/agent-client-protocol`:
- `docs/protocol/v1/initialization.mdx` — `initialize`, capability negotiation, baseline methods.
- `docs/protocol/v1/session-setup.mdx` / prompt-turn / tool-calls / content / file-system / terminals / session-modes.
- `schema/v1/schema.json` (release 1.17.0), `schema/v1/meta.json` (`"version": 1`); v2 deltas in `schema/v2/meta.json` / `meta.unstable.json`.
- JSON-RPC 2.0; stdio transport (newline-delimited, UTF-8, no embedded newlines; logs on stderr).

**ACP Python SDK** — `github.com/agentclientprotocol/python-sdk`; PyPI `agent-client-protocol` (import `acp`), **pin `==0.10.1`**; `acp.PROTOCOL_VERSION = 1`; `acp.run_agent`, `acp.AgentSideConnection`, `acp.Agent`, `acp.interfaces.Client`, `acp.schema`, `acp.helpers`, `acp.contrib`; `docs/libraries/python.mdx`; examples `agent.py` / `echo_agent.py`.

**AgentScope public modules** (under `src/agentscope/`, imported via subpackages):
- `agentscope.agent` — `Agent`, `reply_stream`, `ContextConfig`, `ReActConfig`, `ModelConfig`.
- `agentscope.event` — `AgentEvent` union + subtypes, `EventType`.
- `agentscope.permission` — `PermissionEngine`, `PermissionMode`, `PermissionBehavior`, `PermissionRule`, `PermissionContext`, `PermissionDecision`.
- `agentscope.tool` — `Toolkit`, `Read`/`Write`/`Edit`/`Bash`/`Grep`/`Glob`, `BackendBase` (three abstract primitives: `exec_shell`, `read_file`, `write_file`), `LocalBackend`, `ExecResult`.
- `agentscope.workspace` — `WorkspaceBase`, `LocalWorkspace`, `DockerBackend`, `E2BBackend`.
- `agentscope.message` — `Msg`, `UserMsg`/`AssistantMsg`/`SystemMsg`, block types, `ToolCallState`/`ToolResultState`, `Msg.append_event`.
- `agentscope.state` — `AgentState`.
- **Reference only (not subclassed):** `agentscope.app.middleware._protocol` — `ProtocolMiddlewareBase`, `AGUIProtocolMiddleware` (HTTP/`service`-scoped; source of the `TypeAdapter(Annotated[AgentEvent, Field(discriminator="type")])` mapping idea).