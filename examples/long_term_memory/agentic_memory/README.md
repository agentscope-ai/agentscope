# Agentic Memory Middleware

This example demonstrates `AgenticMemoryMiddleware`, a long-term memory middleware backed by human-readable Markdown files.

No vector database or embedding model is required.

## What the demo shows

`main.py` runs a single Agent against one workspace directory for two turns:

1. **Turn 1 — persist**
   - Receives mock user input containing durable user information.
   - Is explicitly asked to remember that information.
   - Uses the built-in `Read` / `Write` tools to create or update files under `demo_workspace/Memory`.

2. **Turn 2 — recall**
   - The same Agent instance is asked about the earlier user information.
   - Answers from the Markdown memory files persisted on disk by the middleware.

After the first turn, the script prints the generated Markdown files so you can inspect exactly what was persisted.

## Quickstart

Install the dependencies by the following commands:

```bash
git clone -b main https://github.com/agentscope-ai/agentscope

uv pip install agentscope
# or from source
# uv pip install -e .
```

Run the example with the commands:

```bash
cd agentscope/examples/long_term_memory/agentic_memory
export DASHSCOPE_API_KEY=sk-...; python main.py
```

The demo workspace is created at:

```text
examples/long_term_memory/agentic_memory/demo_workspace/
```

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
- A fresh Agent instance can still recall previous facts because they are stored on disk, not in `Agent.state`.
- The Agent is responsible for deciding what to save when the user asks it to remember something.
- `MEMORY.md` should stay concise because it is included in every system prompt.
- Topic files are ordinary Markdown and can be inspected, edited, committed, copied, or deleted with normal filesystem tools.

