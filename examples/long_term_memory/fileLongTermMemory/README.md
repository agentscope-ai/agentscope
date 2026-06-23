# File-based long-term memory middleware

This directory contains runnable examples for
`FileLongTermMemoryMiddleware`, a lightweight long-term memory (LTM)
implementation backed by Markdown files inside an AgentScope workspace.

Unlike vector-store-based memory, file LTM is intentionally transparent:
developers and users can inspect, edit, version, copy, or back up the memory
with ordinary filesystem tools. The middleware works with `LocalWorkspace`,
`DockerWorkspace`, and `E2BWorkspace` through their shared `BackendBase`
filesystem interface.

Two examples are provided:

- `oss_demo.py` runs two independent `Agent` sessions against one local
  workspace and prints tool calls, replies, and persisted Markdown files.
- `app_demo.py` attaches one shared middleware instance to agents assembled
  by the AgentScope FastAPI service.

## Model requirements

File LTM does **not** require or construct a separate chat model. The
middleware has no `chat_model` constructor argument. In `static` and `both`
modes it reuses the chat model already configured on the active Agent through
`agent.model.generate_structured_output(...)`. In `auto` mode, memory decisions
are made through ordinary tool calls by that same Agent model.

The standalone demo uses `DashScopeChatModel` only to make the example
runnable with one concrete provider. DashScope is not a File LTM dependency;
any AgentScope chat model that supports the Agent workflow can be used instead.
In `static`/`both`, that model must support AgentScope structured-output
generation. No embedding model is needed because `memory_search` is lexical.

## Install

For the included DashScope-based standalone demo:

```bash
pip install agentscope

# PowerShell
$env:DASHSCOPE_API_KEY = "sk-..."

# bash / zsh
export DASHSCOPE_API_KEY=sk-...
```

The service example additionally needs the service and storage extras, Redis,
and Uvicorn:

```bash
pip install "agentscope[service,storage]" uvicorn
docker run --rm -p 6379:6379 redis:7
```

## Import path

The middleware is exported from the public middleware package:

```python
from agentscope.middleware import FileLongTermMemoryMiddleware
```

No optional memory provider, extra chat model, embedding model, or vector
database is required by File LTM.

## Middleware parameters

The complete constructor shape is:

```python
FileLongTermMemoryMiddleware(
    workspace=None,
    mode="both",
    extraction_interval=8,
    extraction_window=20,
    extract_on_compaction=True,
    memory_dir="Memory",
    user_max_chars=2_000,
    memory_max_chars=4_000,
    daily_max_chars=8_000,
    max_prompt_chars=12_000,
    fallback_workspace_root=".agentscope/workspaces",
    workspace_key=None,
    memory_instructions=DEFAULT_MEMORY_INSTRUCTIONS,
    manage_instructions=DEFAULT_MANAGE_INSTRUCTIONS,
)
```

There is intentionally no model parameter. Static extraction resolves the
active Agent at hook time and uses that Agent's existing `model`.

| Parameter | Type / default | Description |
| --- | --- | --- |
| `workspace` | `Optional[WorkspaceBase] = None` | Explicit workspace used by every Agent attached to this middleware. It takes precedence over `Agent.offloader`. The caller owns its initialization and shutdown. |
| `mode` | `Literal["static", "auto", "both"] = "both"` | Selects periodic model-driven extraction, agent-controlled `memory_manage`, or both. All modes still inject USER/MEMORY and expose read/search tools. |
| `extraction_interval` | `int = 8` | Number of completed user/assistant turns between static extraction calls. Applies only to `static` and `both`; must be at least 1. The counter is persisted in `.ltm.meta.json`. |
| `extraction_window` | `int = 20` | Maximum number of recent context messages rendered into a static extraction request. Applies only to `static` and `both`; must be at least 2. System messages and synthetic memory messages are excluded. |
| `extract_on_compaction` | `bool = True` | When static extraction has pending turns, flush them before context compression removes old messages. Applies only to `static` and `both`. |
| `memory_dir` | `str = "Memory"` | Workspace-relative root containing `USER.md`, `MEMORY.md`, metadata, and dated daily files. Paths remain constrained to the workspace backend. |
| `user_max_chars` | `int = 2_000` | Maximum resulting size of `USER.md`. A managed or extracted update that would exceed the cap is rejected. |
| `memory_max_chars` | `int = 4_000` | Maximum resulting size of `MEMORY.md`. |
| `daily_max_chars` | `int = 8_000` | Maximum resulting size of any one `YYYY-MM-DD.md` daily notebook. |
| `max_prompt_chars` | `int = 12_000` | Character cap applied to the generated memory snapshot block containing instructions, `USER.md`, and `MEMORY.md`. In `auto`/`both`, manage instructions are appended after this cap. Daily memory is not part of the normal snapshot block. |
| `fallback_workspace_root` | `str = ".agentscope/workspaces"` | Host root for middleware-owned `LocalWorkspace` fallbacks when neither `workspace` nor a workspace offloader exists. Relative values are resolved from the process working directory. |
| `workspace_key` | `Optional[str] = None` | Stable subdirectory key for a fallback workspace. When omitted, a filesystem-safe form of `agent.name` is used. Use distinct middleware instances with distinct keys when same-named agents must not share fallback memory. |
| `memory_instructions` | `str = DEFAULT_MEMORY_INSTRUCTIONS` | Prompt text introducing the USER/MEMORY snapshot. Override it to customize framing while retaining the generated document sections. |
| `manage_instructions` | `str = DEFAULT_MANAGE_INSTRUCTIONS` | Additional tool-usage guidance appended only in `auto` and `both`. It tells the Agent to read the current target before managing sections. |

Parameter interactions worth noting:

- An explicit `workspace` is never closed by the middleware. A fallback
  `LocalWorkspace` is owned by the middleware and should be released with
  `await middleware.close()` during application shutdown.
- `workspace_key` and `fallback_workspace_root` have no effect when an
  explicit workspace or workspace offloader is available.
- Document caps are checked after each mutation. They constrain persistence;
  `max_prompt_chars` separately constrains normal prompt injection.
- `extraction_interval`, `extraction_window`, and `extract_on_compaction` do
  not trigger model calls in `auto` mode.
- Tools are not added to an Agent automatically. Register
  `await middleware.list_tools()` in the Agent's `Toolkit`.

## Workspace memory layout

The middleware creates the following layout under the resolved workspace root:

```text
<workspace_dir>/Memory/
|-- MEMORY.md
|-- USER.md
|-- .ltm.meta.json
`-- memory/
    `-- YYYY-MM-DD.md
```

Each file has a distinct responsibility:


| Path                          | Purpose                                                                                      | Added to every system prompt?                                    |
| ----------------------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `Memory/USER.md`              | Stable user profile, preferences, recurring goals, and constraints for this agent/workspace. | Yes                                                              |
| `Memory/MEMORY.md`            | Durable project facts, decisions, reusable knowledge, and lessons.                           | Yes                                                              |
| `Memory/memory/YYYY-MM-DD.md` | The agent's free-form working notebook for one day.                                          | No; read internally by static extraction and retrieved on demand |
| `Memory/.ltm.meta.json`       | Internal static-extraction turn counter and last-update metadata.                            | No                                                               |


`USER.md` and `MEMORY.md` are complete snapshots in the model's normal system
prompt. Daily memory stays out of normal reply context until the agent searches
or reads it, keeping prompt growth bounded. The static extraction call is
separate: it receives today's daily file so it can update the notebook against
its current contents.

## Control modes

The `mode` constructor argument controls who decides when memory is written.
All modes inject `USER.md` and `MEMORY.md` into the system prompt and expose
the read-only `memory_read` and `memory_search` tools.

### `static`

The middleware periodically extracts memory without requiring the agent to
call a write tool:

1. A completed user/assistant turn increments the persistent turn counter.
2. Every `extraction_interval` turns, recent conversation plus current
   `USER.md`, `MEMORY.md`, and today's daily file are sent to the agent's chat
   model as a structured extraction request. This is the same model already
   assigned to `Agent(model=...)`, not a separately configured LTM model.
3. The model returns section-aware `add`, `replace`, and `remove` edits for all
   three documents. Today's daily file can contain any useful working notes;
   it is not constrained to fixed facts/decisions/todos categories.
4. The metadata file records the completed extraction turn.

If context compression is about to remove old messages,
`extract_on_compaction=True` flushes pending turns before compression.

### `auto`

The middleware performs no periodic extraction. It exposes `memory_manage`,
allowing the agent to add, replace, or remove memory when the conversation
requires it. The prompt instructs the agent to call `memory_read` for the same
target before writing.

### `both` (default)

Both paths are active: periodic structured extraction provides a reliable
baseline, while `memory_manage` lets the agent record or correct important
information immediately.

## Memory tools

Tools are returned by `await middleware.list_tools()` and must be passed into
the agent's `Toolkit` explicitly.

### `memory_read`

Reads one complete target:

- `target="user"` reads `USER.md`.
- `target="memory"` reads `MEMORY.md`.
- `target="daily", date="YYYY-MM-DD"` reads one daily file.

The result starts with the existing level-two (`##`) section names. This lets
the agent inspect the current document before a managed update.

### `memory_search`

Performs lightweight lexical search over Markdown sections. It uses exact
phrase and token matching rather than embeddings or a weighted retrieval
pipeline.

Important inputs:

- `query`: text to find.
- `scope="daily"`: search dated daily files only.
- `scope="all"`: also include `MEMORY.md` and `USER.md`.
- `days`: daily-memory lookback window.
- `limit`: maximum number of returned sections.

### `memory_manage`

Available in `auto` and `both` modes. It supports `add`, `replace`, and
`remove` operations.

For `add`, the agent should select an existing `##` section after reading the
target. If no section fits, it may explicitly pass `create_section=true` to
create a new heading in `USER.md`, `MEMORY.md`, or today's daily notebook.
Unknown sections are rejected when this flag is false, preventing accidental
headings caused by spelling mistakes.

Example:

```text
memory_read(target="memory")

memory_manage(
    action="add",
    target="memory",
    section="Architecture Decisions",
    create_section=true,
    thinking="No current section describes architectural decisions.",
    content="Use BackendBase for all workspace filesystem operations.",
)
```

For `replace` and `remove`, `old_text` must occur exactly once. This prevents a
stale or ambiguous edit from silently changing multiple memories.

`thinking` is returned in the tool result for auditability but is never
persisted in the memory files.

## Standalone Agent integration

An explicit workspace is the clearest setup for a standalone agent:

```python
from agentscope.agent import Agent
from agentscope.middleware import FileLongTermMemoryMiddleware
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace

workspace = LocalWorkspace(workdir="./workspace")
await workspace.initialize()

memory = FileLongTermMemoryMiddleware(
    workspace=workspace,
    mode="both",
    extraction_interval=8,
)

agent = Agent(
    name="assistant",
    system_prompt="You are a helpful assistant.",
    model=model,
    toolkit=Toolkit(
        tools=[
            *(await workspace.list_tools()),
            *(await memory.list_tools()),
        ],
        skills_or_loaders=await workspace.list_skills(),
        mcps=await workspace.list_mcps(),
    ),
    middlewares=[memory],
    offloader=workspace,
)

try:
    reply = await agent.reply(...)
finally:
    await workspace.close()
```

The middleware never registers its tools implicitly. This keeps toolkit
composition under application control and mirrors the other AgentScope LTM
middlewares. Notice that the middleware constructor receives no model: the
`model` passed to `Agent` above is also used for static memory extraction.

## Workspace resolution

For each agent call, the middleware resolves the memory workspace in this
order:

1. The explicit `workspace=` passed to the middleware.
2. `agent.offloader` when it is a `WorkspaceBase` instance.
3. A lazily initialized `LocalWorkspace` at
  `.agentscope/workspaces/<agent-name>`.

The fallback workspace is private to the middleware and does not replace
`agent.offloader`. Therefore enabling file LTM does not unexpectedly enable or
change context/tool-result offloading.

Use `fallback_workspace_root` to relocate fallback workspaces. Use
`workspace_key` when agents with the same name need distinct fallback memory.
Call `await memory.close()` during shutdown when the middleware owns fallback
workspaces.

Stores, locks, and prompt snapshots are keyed by `workspace_id`, not merely by
session ID. One middleware instance can therefore serve many app sessions and
workspaces without mixing their memory.

## Service-mode integration

`app_demo.py` uses `LocalWorkspaceManager` to create one workspace per service
agent. `ChatService` supplies that workspace through `Agent.offloader`, so the
middleware resolves the correct files at runtime.

One shared middleware instance must be used by both extension factories:

```python
memory = FileLongTermMemoryMiddleware(mode="both")


async def extra_agent_middlewares(user_id, agent_id, session_id):
    return [memory]


async def extra_agent_tools(user_id, agent_id, session_id):
    return await memory.list_tools()


app = create_app(
    ...,
    workspace_manager=workspace_manager,
    extra_agent_middlewares=extra_agent_middlewares,
    extra_agent_tools=extra_agent_tools,
)
```

The tool objects resolve the active store from the injected `AgentState`, so
they remain correctly bound when the shared middleware serves concurrent
sessions.

## Backend and persistence behavior

All LTM I/O goes through the workspace's current `BackendBase`.

- `LocalWorkspace`: memory is stored under its local `workdir` and survives
  process restarts unless the directory is deleted.
- `DockerWorkspace`: use `host_workdir` when memory must survive container
  removal. Without a bind-mounted host directory, memory is ephemeral.
- `E2BWorkspace`: memory follows the sandbox/workspace lifecycle and survives
  when the same persistent workspace is reattached.

The backend is resolved for every operation because Docker and E2B workspaces
may replace their backend object when reconnecting.

## Running the demos

Standalone demo:

```powershell
python .\examples\long_term_memory\fileLongTermMemory\oss_demo.py
```

The demo intentionally calls `shutil.rmtree(DEMO_WORKSPACE, ...)` at startup
so each run is reproducible. Remove that line, or set the demo's reset switch
to false if you want to observe persistence across separate process runs.

Service demo:

```powershell
docker run --rm -p 6379:6379 redis:7
python .\examples\long_term_memory\fileLongTermMemory\app_demo.py
```

The service listens on port 8000 and stores local agent workspaces under
`examples/long_term_memory/fileLongTermMemory/app_workspaces/`.

## Operational notes

- Memory writes are serialized per workspace with an async lock.
- `USER.md`, `MEMORY.md`, and daily files have configurable character limits.
- Duplicate additions are ignored using normalized text matching.
- The static-extraction prompt instructs the model not to copy raw dialogue,
  tool output, passwords, API keys, tokens, or unsupported sensitive
  inferences into memory.
- Markdown files remain the source of truth; manual edits are visible on the
  next read or prompt snapshot refresh.
- File LTM is intentionally lexical and lightweight. Use a vector-backed LTM
  such as mem0 when semantic retrieval across a large memory corpus is needed.
