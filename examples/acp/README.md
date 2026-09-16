# ACP Agent (stdio) example

Expose an AgentScope agent to any [Agent Client Protocol](https://agentclientprotocol.com)
(ACP) desktop client — e.g. [Zed](https://zed.dev) — as a stdio
subprocess. The desktop editor is the ACP **Client** (it owns the UI,
the open buffers, the local filesystem and terminal); the AgentScope
kernel is the ACP **Agent** (it owns planning, tool intent, and the
streamed narration of a turn).

Built **only on AgentScope's public API** — no `agentscope/` core
changes. Design rationale and the full protocol mapping live in
[DESIGN.md](DESIGN.md) (companion to discussion
[#1948](https://github.com/agentscope-ai/agentscope/discussions/1948)).

```
Desktop shell (ACP Client, e.g. Zed)
        │  stdio, newline-delimited JSON-RPC 2.0
        ▼
examples/acp/  (this example, the ACP Agent)
        │  Agent.reply_stream → AgentEvent stream
        ▼
AgentScope Agent  (public API only)
```

## What you get

- A fixed, runnable **general assistant with coding capabilities**:
  the builtin `Read` / `Write` / `Edit` / `Grep` / `Glob` / `Bash`
  tools behind AgentScope's permission engine.
- **Shell delegation** by default: file contents go through the
  client's `fs/read_text_file` / `fs/write_text_file` (so the agent
  sees *unsaved editor buffers*), and every shell command — including
  the tools' internal existence/dir/mkdir checks, ripgrep, and the
  Glob helper — runs in a client-owned terminal via `terminal/*`.
- **Exactly one permission prompt** per gated operation, raised in the
  editor's native UI (`session/request_permission`), with
  `allow/reject once/always` mapped back onto AgentScope
  `PermissionRule`s.
- **Cancellation** that works mid-tool: `session/cancel` interrupts
  the turn gracefully (interrupted tool results, `stopReason:
  "cancelled"`), courtesy of AgentScope's first-class interruption
  support.

## Quick start

```bash
cd examples/acp
python -m venv .venv && source .venv/bin/activate
pip install -e .                  # installs acp_example + its deps
# (to use the repo checkout instead of PyPI agentscope: pip install -e ../..)

export DASHSCOPE_API_KEY=sk-...   # default provider
python -m acp_example             # speaks ACP on stdin/stdout
```

The editable install puts `acp_example` on the venv's `sys.path`, so
`python -m acp_example` works from **any** working directory — which is
what an editor needs, since it spawns the agent with your project as
cwd.

The process is silent until an ACP client drives it (all logs go to
stderr — stdout carries only protocol frames).

### Using it from Zed

Add an entry to Zed's `settings.json`:

```json
{
  "agent_servers": {
    "AgentScope": {
      "command": "/abs/path/to/examples/acp/.venv/bin/python",
      "args": ["-m", "acp_example"],
      "env": { "DASHSCOPE_API_KEY": "sk-..." }
    }
  }
}
```

Then open the Agent Panel, pick **AgentScope**, and prompt away. File
reads/writes and every command execution are mediated — and gated —
by Zed.

### Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `ACP_MODEL_PROVIDER` | `dashscope` | `dashscope` \| `openai` \| `anthropic` \| `deepseek` \| `moonshot` |
| `ACP_MODEL_NAME` | `qwen-max` (dashscope only) | Model name; required for other providers |
| `<PROVIDER>_API_KEY` | — | e.g. `DASHSCOPE_API_KEY`, `OPENAI_API_KEY` |
| `<PROVIDER>_BASE_URL` | — | Optional API base override |
| `ACP_AUTHORITY` | `shell` | `shell` (client owns fs/terminal) \| `workspace` (kernel-owned sandbox) |
| `ACP_WORKSPACE_BACKEND` | `local` | Sandbox backend for workspace mode |
| `ACP_PERMISSION_MODE` | `default` | AgentScope `PermissionMode` |
| `ACP_AGENT_NAME` | `agentscope-acp` | Display name |

Shell mode requires the client to advertise **both**
`fs.readTextFile`/`fs.writeTextFile` **and** `terminal` capabilities;
tools whose channel is missing are omitted rather than silently
touching the local disk. Shell mode also assumes the client machine
has `rg` (ripgrep) and `python3` on PATH — the `Grep` and `Glob`
tools execute them through the client's terminal.

## Forking this into your own agent

All agent construction lives in **one function**:
[`acp_example/agent.py::build_agent()`](acp_example/agent.py). To turn
the example into your own ACP agent:

1. `cp -r examples/acp/ my-acp-agent/`
2. Edit **only** `build_agent()` — swap the model, system prompt, tool
   set, or permission rules.
3. Leave `server.py` / `session.py` / `translate.py` / `bridge.py`
   untouched — the ACP plumbing is generic.
4. Point your editor at `python -m acp_example` in your copy.

## Tests

```bash
cd examples/acp
pip install -e ".[test]"
pytest
```

The suite drives the kernel with a mock ACP client (fs served from a
temp dir, terminals backed by real subprocesses) and covers the turn
lifecycle, the permission round-trip (allow / reject / cancelled),
mid-tool cancellation, capability gating, and a wire-level JSON-RPC
smoke test.

## Scope and follow-ons

This is PR1 of the plan in [DESIGN.md](DESIGN.md) §20: stdio + Agent
role only, `session/load` not yet advertised, prompt capabilities
(image/audio/embedded context) not yet advertised, MCP pass-through
and the receipt emitter as follow-ons. The ACP SDK is pinned
(`agent-client-protocol==0.12.0`, protocol v1) because the protocol
is still evolving.
