# FileSystemMemory middleware

This directory contains an example for `FileSystemMemoryMiddleware`, a
lightweight long-term memory implementation backed by Markdown files inside an
AgentScope workspace.

FileSystemMemory is intentionally transparent: developers and users can inspect,
edit, version, copy, or back up the memory with ordinary filesystem tools.
The middleware uses the workspace supplied as `Agent.offloader`; backend file
access remains an internal implementation detail.

The runnable entry point is:

- `demo.py`: runs the same memory-management task with `static_control`,
  `agent_control`, and `both`, then prints memory-tool calls, assistant
  replies, and persisted Markdown files.

## Model requirements

FileSystemMemory does **not** require or construct a separate chat model. The
middleware has no `chat_model` constructor argument. In `static_control` and
`both` modes it reuses the chat model already configured on the active Agent
through `agent.model.generate_structured_output(...)`. In `agent_control`
mode, memory decisions are made through ordinary tool calls by that same Agent
model.

The demo uses `DashScopeChatModel` only to make the example runnable with one
concrete provider. DashScope is not a FileSystemMemory dependency; any
AgentScope chat model that supports the Agent workflow can be used instead. In
`static_control`/`both`, that model must support AgentScope structured-output
generation. No embedding model is needed because `memory_search` is lexical.

## Install

For the included DashScope-based demo:

```bash
pip install agentscope

# PowerShell
$env:DASHSCOPE_API_KEY = "sk-..."

# bash / zsh
export DASHSCOPE_API_KEY=sk-...
```

## Import path

The middleware is exported from the public middleware package:

```python
from agentscope.middleware import FileSystemMemoryMiddleware
```

No optional memory provider, extra chat model, embedding model, or vector
database is required by FileSystemMemory.

## Middleware parameters

The constructor is deliberately small:

```python
FileSystemMemoryMiddleware(
    mode="both",
    extraction_interval=8,
    extract_on_compaction=True,
    memory_dir="Memory",
    user_max_chars=2_000,
    memory_max_chars=4_000,
    daily_max_chars=8_000,
    memory_instructions="",
)
```

There is intentionally no model, workspace, backend, or fallback-workspace
parameter. Static extraction resolves the active Agent at hook time and uses
that Agent's existing `model`. Storage resolves from `Agent.offloader`; if no
workspace is supplied, the middleware lazily creates one internal
fallback workspace under `.agentscope/workspaces`.


| Parameter               | Type / default                                                | Description                                                                                                                                                                              |
| ----------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mode`                  | `Literal["static_control", "agent_control", "both"] = "both"` | Selects periodic model-driven extraction, agent-controlled `memory_manage`, or both. All modes still inject USER/MEMORY and expose read/search tools.                                    |
| `extraction_interval`   | `int = 8`                                                     | Number of completed user/assistant turns between static extraction calls. Applies only to `static_control` and `both`; must be at least 1. The counter is persisted in `.ltm.meta.json`. |
| `extract_on_compaction` | `bool = True`                                                 | When static extraction has pending turns, flush them before context compression removes old messages. Applies only to `static_control` and `both`.                                       |
| `memory_dir`            | `str = "Memory"`                                              | Workspace-relative root containing `USER.md`, `MEMORY.md`, metadata, and dated daily files. Paths remain constrained to the workspace backend.                                           |
| `user_max_chars`        | `int = 2_000`                                                 | Maximum resulting size of `USER.md`. A managed or extracted update that would exceed the cap is rejected.                                                                                |
| `memory_max_chars`      | `int = 4_000`                                                 | Maximum resulting size of `MEMORY.md`.                                                                                                                                                   |
| `daily_max_chars`       | `int = 8_000`                                                 | Maximum resulting size of any one `YYYY-MM-DD.md` daily notebook.                                                                                                                        |
| `memory_instructions`   | `str = ""`                                                    | Optional extra prompt text appended after the default USER/MEMORY snapshot instructions. Use it for project-specific framing, not to replace the default memory protocol.                |


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

### `static_control`

The middleware periodically extracts memory without requiring the agent to
call a write tool:

1. A completed user/assistant turn increments the persistent turn counter.
2. Every `extraction_interval` turns, the Agent's compressed summary and full
  live context are reused without flattening their content blocks. A final
   `HintBlock` supplies current `USER.md`, `MEMORY.md`, and today's daily file,
   then the temporary message list is sent to the Agent's chat model as a
   structured extraction request. This preserves multimodal context and uses
   the same model already assigned to `Agent(model=...)`.
3. The model returns section-aware `add`, `replace`, and `remove` edits for all
  three documents. Today's daily file can contain any useful working notes;
   it is not constrained to fixed facts/decisions/todos categories.
4. The metadata file records the completed extraction turn.

If context compression is about to remove old messages,
`extract_on_compaction=True` flushes pending turns before compression.

### `agent_control`

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

Performs lightweight lexical search over Markdown sections. It does not use
embeddings, vector databases, semantic reranking, or recency weighting.

The method is intentionally simple:

- Daily filenames are filtered by the requested `days` lookback window before
file reads.
- Each Markdown file is split into level-two (`##`) sections.
- The query and section content are normalized with case folding and whitespace
compaction.
- Exact normalized phrase hits rank above token-only hits.
- English-like text is matched with word tokens; CJK text is expanded into
character bigrams.
- Ranking is deterministic: phrase hit, number of matched terms, then source
name.

Important inputs:

- `query`: text to find.
- `scope="daily"`: search dated daily files only.
- `scope="all"`: also include `MEMORY.md` and `USER.md`.
- `days`: daily-memory lookback window.
- `limit`: maximum number of returned sections.

### `memory_manage`

Available in `agent_control` and `both` modes. It supports `add`, `replace`,
and `remove` operations.

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

## Agent Integration

An explicit offloader workspace is the clearest setup:

```python
from agentscope.agent import Agent
from agentscope.middleware import FileSystemMemoryMiddleware
from agentscope.tool import Toolkit

# Use any initialized AgentScope workspace instance.
workspace = ...
await workspace.initialize()

memory = FileSystemMemoryMiddleware(
    mode="both",
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

The middleware takes the workspace directly from `Agent.offloader`; neither
the workspace nor its backend is passed into the middleware constructor.

The middleware never registers its tools implicitly. This keeps toolkit
composition under application control and mirrors the other AgentScope LTM
middlewares. Notice that the middleware constructor receives no model: the
`model` passed to `Agent` above is also used for static memory extraction.

## Sharing and Isolation

Sharing is determined entirely by the middleware and workspace instances passed
to each Agent.

```python
ltm = FileSystemMemoryMiddleware()

agent_a = Agent(..., middlewares=[ltm], offloader=workspace)
agent_b = Agent(..., middlewares=[ltm], offloader=workspace)
```

These Agents share the same `FileSystemMemoryMiddleware`, workspace store,
Markdown files, and asynchronous write lock. They read and write the same
`Memory/USER.md`, `Memory/MEMORY.md`, and daily-memory files.

One middleware can also serve different workspaces:

```python
agent_a = Agent(..., middlewares=[ltm], offloader=workspace_a)
agent_b = Agent(..., middlewares=[ltm], offloader=workspace_b)
```

The middleware creates an independent store for each `workspace_id`, so their
memory does not mix.

When several Agents share a middleware but none supplies an `offloader`, they
all use the same middleware-owned fallback workspace and therefore share one
FileSystemMemory store.


| Middleware instances | Workspace instances | Result                                                                                       |
| -------------------- | ------------------- | -------------------------------------------------------------------------------------------- |
| Same                 | Same                | Memory is fully shared.                                                                      |
| Same                 | Different           | Middleware is shared; memory is isolated by workspace.                                       |
| Same                 | All omitted         | Agents share the automatically created fallback workspace and memory.                        |
| Different            | Same                | Files are shared, but caches and async locks are not; concurrent writes are not recommended. |
| Different            | Different           | Memory is fully isolated.                                                                    |


When one workspace represents one Agent's long-term identity, prefer sharing
the same middleware instance among Agents that use that workspace.

## Workspace Resolution

For each Agent call, the middleware resolves the LTM workspace in this order:

1. `agent.offloader` when it is a `WorkspaceBase`.
2. A lazily initialized internal fallback workspace at `.agentscope/workspaces`.

The fallback workspace is private to the middleware and does not replace
`agent.offloader`. Therefore enabling FileSystemMemory does not unexpectedly
enable or change context/tool-result offloading. Call `await memory.close()`
during shutdown when the middleware owns this fallback workspace.

Stores, locks, and prompt snapshots are keyed by workspace ID, not merely by
session ID. One middleware instance can therefore serve many sessions and
workspaces without mixing their memory.

Prompt snapshots are versioned by the modification times of `USER.md` and
`MEMORY.md`. Repeated system-prompt construction only stats those files and
reuses their cached contents while both timestamps are unchanged. External
edits are loaded automatically after an mtime change; middleware-managed and
static USER/MEMORY writes also invalidate the cache immediately.

## Backend and persistence behavior

The middleware reads `workdir` and the current backend from the resolved
workspace, then constructs a `FileSystemMemoryStore(backend, workdir)`.
`BackendBase` therefore does not need a public `workdir` property, and
middleware users never configure a backend directly. Persistence follows the
resolved workspace's storage lifecycle: if the workspace storage persists,
FileSystemMemory persists; if the workspace storage is temporary,
FileSystemMemory is temporary too.

At the start of each Agent hook, the middleware compares the workspace's
current backend with the one bound to its store. If the workspace replaces its
backend, the middleware rebuilds the store with the new backend while retaining
the same workspace-relative files.

## Running the demo

```powershell
python .\examples\long_term_memory\fileSystemMemory\demo.py
```

`demo.py` runs one memory-management conversation with each FileSystemMemory
mode. Edit the `MODES` tuple to run only `static_control`, `agent_control`, or
`both` and reduce model calls.

Set `RESET_DEMO_WORKSPACE=True` for reproducible empty workspaces, or leave it
false to observe persistence across separate process runs.

## Naming note

The current public class name, `FileSystemMemoryMiddleware`, is broad but
accurate: it describes a filesystem-backed LTM rather than a specific retrieval
algorithm. If a more specific name is desired before this API settles,
`MarkdownLongTermMemoryMiddleware` or `WorkspaceMarkdownMemoryMiddleware`
would make the implementation style clearer, similar to how agent tools often
use terms like "memory bank" for human-readable Markdown/project memories and
"semantic memory" for embedding-backed stores. Renaming the exported class
after release would be a public API break, so this README keeps the current
name and documents the Markdown-backed behavior explicitly.

## Operational notes

- Memory writes are serialized per workspace with an async lock.
- `USER.md`, `MEMORY.md`, and daily files have configurable character limits.
- Duplicate additions are ignored using normalized text matching.
- The static-extraction prompt instructs the model not to copy raw dialogue,
tool output, passwords, API keys, tokens, or unsupported sensitive
inferences into memory.
- Markdown files remain the source of truth; manual edits are visible on the
next read or prompt snapshot refresh.
- FileSystemMemory is intentionally lexical and lightweight. Use a
vector-backed LTM such as mem0 when semantic retrieval across a large memory
corpus is needed.
