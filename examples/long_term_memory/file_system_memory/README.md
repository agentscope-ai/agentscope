# Agentic Memory Middleware

This example demonstrates `AgenticMemoryMiddleware`, a long-term memory middleware backed by human-readable Markdown files.

No vector database or embedding model is required.

## What the demo shows

`demo.py` runs two separate Agent instances against the same workspace directory:

1. **First Agent**
   - Receives mock user input containing durable user information.
   - Is explicitly asked to remember that information.
   - Uses the built-in `Read` / `Write` tools to create or update files under `demo_workspace/Memory`.

2. **Second Agent**
   - Is freshly initialized, so it does not share conversation state with the first Agent.
   - Uses the same `workdir`.
   - Is asked about the earlier user information.
   - Can answer from the Markdown memory files persisted by the first Agent.

After the first turn, the script prints the generated Markdown files so you can inspect exactly what was persisted.

## Install

For the included DashScope-based demo:

```bash
pip install agentscope
export DASHSCOPE_API_KEY=sk-...
```

PowerShell:

```powershell
$env:DASHSCOPE_API_KEY = "sk-..."
```

DashScope is only used as the chat model provider in this demo. `AgenticMemoryMiddleware` itself is not tied to DashScope.

## Run

From the repository root:

```bash
python examples/long_term_memory/file_system_memory/demo.py
```

The demo workspace is created at:

```text
examples/long_term_memory/file_system_memory/demo_workspace/
```

By default, `RESET_DEMO_WORKSPACE = True` in `demo.py`, so every run starts from an empty memory directory. Set it to `False` if you want to observe persistence across process runs.

## Minimal integration pattern

```python
from agentscope.agent import Agent
from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.tool import Read, Toolkit, Write

workdir = "./my_agent_workspace"
memory = AgenticMemoryMiddleware(workdir=workdir)

agent = Agent(
   name="assistant",
   system_prompt="You are a helpful assistant.",
   model=model,
   toolkit=Toolkit(tools=[Read(), Write()]),
   middlewares=[memory],
)
```

`Read` and `Write` are not provided by the middleware automatically. They are ordinary tools that let the Agent inspect and update the Markdown files described by the memory instructions.

For unattended local demos, configure permissions so `Write` can update the memory directory. `demo.py` uses `PermissionMode.ACCEPT_EDITS` and adds the demo workspace as an allowed working directory.

## Constructor

```python
AgenticMemoryMiddleware(
    *,
    workdir: str,
    memory_dir: str = "Memory",
    parameters: AgenticMemoryMiddleware.Parameters | None = None,
    backend: BackendBase | None = None,
)
```

Important parameters:

| Parameter | Description |
| --- | --- |
| `workdir` | Workspace directory that contains the memory directory. |
| `memory_dir` | Directory name under `workdir`; defaults to `Memory`. |
| `parameters.memory_max_tokens` | Maximum estimated tokens from `MEMORY.md` injected into the system prompt. |
| `parameters.retrieval_async` | Whether to start asynchronous relevance retrieval during `Agent.reply`. |
| `parameters.retrieval_max_files` | Maximum number of topic Markdown files considered for relevance selection. |
| `parameters.retrieval_max_tokens_per_md` | Maximum estimated tokens read from each selected memory file. |
| `backend` | Optional filesystem backend. When omitted, local filesystem storage is used. |

## Markdown layout

The middleware creates this directory automatically:

```text
<workdir>/Memory/
`-- MEMORY.md
```

The Agent should write each durable memory into its own Markdown file with frontmatter, then add a short pointer to `MEMORY.md`:

```markdown
---
name: User profile
description: User lives in Hangzhou and prefers concise Chinese answers
type: user
---

Alice Chen lives in Hangzhou and prefers concise Chinese answers.
```

`MEMORY.md` is an index, not the memory body:

```markdown
- [User profile](user_profile.md) — User location and answer-style preference.
```

On future turns, `MEMORY.md` is always included in the system prompt. The middleware can then select relevant topic files by filename and frontmatter description and inject their contents as a hint.

## Notes

- Memory is workspace-scoped: reuse the same `workdir` to reuse the same Markdown memory.
- A fresh Agent instance can recall previous facts because they are stored on disk, not in `Agent.state`.
- The Agent is responsible for deciding what to save when the user asks it to remember something.
- `MEMORY.md` should stay concise because it is included in every system prompt.
- Topic files are ordinary Markdown and can be inspected, edited, committed, copied, or deleted with normal filesystem tools.

