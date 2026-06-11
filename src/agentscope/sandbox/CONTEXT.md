# Sandbox System — Architecture Notes

## Purpose

The sandbox system provides **lifecycle-managed, isolated execution environments** for agents.
It bridges the Java-style sandbox model (acquire → start → stop → shutdown) with AgentScope's
native Python workspace system (initialize → use → close).

## Core Components

| Component | File | Role |
|-----------|------|------|
| `Sandbox` | `_sandbox.py` | Abstract interface: `start()`, `stop()`, `shutdown()`, `state` |
| `SandboxClient` | `_client.py` | Factory for `create()` and `resume()` sandboxes |
| `SandboxManager` | `_manager.py` | Orchestrates acquire/release/persist/clear with isolation keys |
| `SandboxContext` | `_types.py` | Configuration: `workspace_spec`, `snapshot_spec`, `isolation_scope` |
| `SandboxState` | `_types.py` | Serializable state used for resume across calls |
| `SandboxStateStore` | `_state_store.py` | Persistence backend (`InMemory` or `StorageBacked`) |
| `SandboxLifecycleMiddleware` | `middleware/_sandbox_lifecycle.py` | Injects sandbox acquire/release around each agent `reply()` |

## Bridge to Workspace

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│  SandboxManager │────▶│ WorkspaceSandbox    │────▶│  WorkspaceBase  │
│  (acquire)      │     │  (adapter)          │     │  (Docker/E2B/   │
│  (release)      │     │  start() → init()   │     │   Local)        │
└─────────────────┘     │  stop()   → pass    │     └─────────────────┘
                        │  shutdown()→ close()│
                        └─────────────────────┘
```

`WorkspaceSandbox` wraps any `WorkspaceBase` subclass to satisfy the `Sandbox` contract.
`WorkspaceSandboxClient` is a `SandboxClient` implementation that produces `WorkspaceSandbox`
instances from a user-supplied workspace factory.

## Lifecycle Flow

1. **Acquire** (`SandboxManager.acquire()`)
   - Priority 1: user-managed `external_sandbox`
   - Priority 2: resume from `external_state`
   - Priority 3: resume from persisted `SandboxState` (via `state_store`)
   - Priority 4: fresh `create()`
   - For harness-managed sandboxes (3 & 4), an execution-guard lease is acquired first.

2. **Start** (`Sandbox.start()`)
   - `WorkspaceSandbox.start()` applies `WorkspaceSpec` via `WorkspaceSpecApplier`,
     then calls `workspace.initialize()`.

3. **Use** (agent reply)
   - `SandboxLifecycleMiddleware` injects the sandbox into `agent.workspace`.
   - Agent tools (e.g. `RemoteBash`, `RemoteRead`) delegate to `workspace._exec` / `_read`.

4. **Stop** (`Sandbox.stop()`)
   - `WorkspaceSandbox.stop()` is currently a no-op.
   - Workspace state is persisted by the underlying backend (Docker bind-mount / E2B pause).
   - Future: snapshot archiving could be added here.

5. **Shutdown** (`Sandbox.shutdown()`)
   - `WorkspaceSandbox.shutdown()` calls `workspace.close()` (pause / stop container).

6. **Release** (`SandboxManager.release()`)
   - Calls `stop()` then `shutdown()`.
   - Closes the execution-guard lease.

## Isolation Scopes

| Scope | Key | Sharing |
|-------|-----|---------|
| `SESSION` | `session_id` | Per-session isolation |
| `USER` | `user_id` | Shared across all sessions of the same user (default) |
| `AGENT` | `agent_id` | Shared across all users/sessions of the same agent |
| `GLOBAL` | `"__global__"` | Globally shared |

## WorkspaceSpec Integration

`SandboxContext.workspace_spec` can carry a `WorkspaceSpec` instance. When present,
`WorkspaceSandbox.start()` materialises the spec into the workspace directory before
`initialize()` runs. This supports declarative pre-seeding of files, directories,
and local copies.

**Note:** `GitRepoEntry` inside `WorkspaceSpec` is not yet implemented.
