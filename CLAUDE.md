# CLAUDE.md — Logic & Call‑Path Guide

Scope: Entire repository.

Role
- This document serves as **program memory** for the AgentScope-easy framework, providing fast access to module logic and function call paths.
- **Docs-first Policy**: When a function/class usage is unclear, consult CLAUDE.md first. If missing, **add it before touching code**.
- Bridges the gap between high-level SOPs and concrete implementation details.

**Critical Principle**: SOP is the soul, CLAUDE.md is the program memory, `src/` is just regenerative expression.

Conventions
- File references use clickable repo‑relative paths, optionally with line numbers: `src/agentscope/agent/_react_agent.py:120`.
- Prefer references over large code copies. Keep snippets small and purposeful.
- Use `rg -n "<symbol>" src/` to find definitions/usages.
- Keep entries in sync with SOPs in `docs/`.

**Architecture Boundaries**
- `src/` provides **composable scaffolding only** - no direct business logic
- Business behaviors go to plugins/MCP tools/examples/
- Maintain clear separation: framework logic vs application logic
- Each module has single responsibility defined in its SOP

Template (copy per module/feature)

```
Module: <path/to/file.py>
Responsibility: <one line>
Key Types: <classes/TypedDicts/dataclasses>

Key Functions/Methods
- <name>(<signature>)
  - Purpose: <what it does>
  - Inputs: <args of interest>
  - Returns: <shape/contract>
  - Side‑effects: <I/O, network, state, logs>
  - Invariants/Pre/Post: <constraints>
  - Errors: <raises/edge cases>
  - Notes: <perf/thread‑safety/idempotency>
  - References: `<file:line>` (definition) · `<file:line>` (usage)

Call Graph (ASCII/bullets)
- <caller> → <callee> …

Related SOP: `docs/...`
```

Example Entries (abbreviated)

Module: `src/agentscope/agent/_react_agent.py`
Responsibility: ReAct agent orchestration: reasoning → tool use → summarizing.

Key Methods
- `reply(msg, structured_model=None)`
  - Orchestrates memory/RAG hints → `_reasoning()` → `_acting()` (parallelizable) → fallback `_summarizing()`.
  - Side‑effects: appends to short‑term memory; may emit streaming print.
  - References: `src/agentscope/agent/_react_agent.py`

- `_reasoning()` / `_acting(tool_call)` / `generate_response(response, **kwargs)`
  - Reasoning constructs prompts via formatter/model; Acting dispatches to `Toolkit.call_tool_function`; `generate_response` finalizes answer and optional structured output.
  - References: `src/agentscope/agent/_react_agent.py`

Module: `src/agentscope/tool/_toolkit.py`
Responsibility: Tool registry, grouping, JSON‑schema, MCP integration.

Key Methods
- `register_tool_function(func, group_name="basic", ...)` — registers tool, builds schema, handles presets/post‑process.
- `call_tool_function(tool_call)` — executes tool (supports sync/async/generators), returns `ToolResponse`.
  - References: `src/agentscope/tool/_toolkit.py`

Module: `src/agentscope/model/_openai_model.py`
Responsibility: OpenAI chat wrapper; streaming/structured outputs/tools.

Key Methods
- `__call__(messages, tools=None, tool_choice=None, structured_model=None, ...)` — dispatch; stream parsing yields text/thinking/audio/tool_calls.
  - References: `src/agentscope/model/_openai_model.py`

Module: `src/agentscope/session/_json_session.py`
Responsibility: Persist/load state for `StateModule`s to JSON.

Module: `src/agentscope/pipeline/_functional.py`
Responsibility: Sequential/fanout pipelines and stream printing aggregator.
Key Functions
- `sequential_pipeline(agents, msg)` → Msg|list[Msg]|None
  - Steps: for agent in agents: msg = await agent(msg)
- `fanout_pipeline(agents, msg, enable_gather=True, **kwargs)` → list[Msg]
  - Steps: deep‑copy msg per agent; gather or sequential await
- `stream_printing_messages(agents, coroutine_task, end_signal='[END]')`
  - Steps: enable shared queue on agents; run task; yield queue items until end_signal

Module: `src/agentscope/tracing/_trace.py`
Responsibility: OpenTelemetry spans for agent/model/toolkit/formatter.
Key Decorators: `trace`, `trace_llm`, `trace_toolkit`, `trace_format`

Module: `src/agentscope/tool/_registered_tool_function.py`
Responsibility: Store tool metadata and post‑processing hooks.

Module: `src/agentscope/mcp/_http_stateless_client.py`
Responsibility: Disposable MCP HTTP/SSE client listing tools and wrapping call functions.

Cross‑module Call Graphs
- Agent(ReAct).reply → Formatter.format → Model.__call__ (stream/tool/structured)
  → Toolkit.call_tool_function (0..n, possibly parallel) → ToolResponse
  → ReAct.generate_response → Msg(metadata) → Agent.print/broadcast
- Pipeline.stream_printing_messages → Agent.set_msg_queue_enabled(True, queue) → Agent.print → queue → consumer

Key Methods
- `save_session_state(session_id, **modules)` / `load_session_state(session_id, ...)`.
  - References: `src/agentscope/session/_json_session.py`

Notes
- If a flow spans multiple modules (e.g., Agent → Formatter → Model → Toolkit), add a short cross‑module call graph.
- Update or create entries as part of the SOP‑first change process and list in `todo.md` acceptance checklist.

**Workflow Integration**
Follow the strict "Documentation → Planning → Execution → Verification" chain:

1. **Preparation Phase**
   - Read relevant module SOP & CLAUDE.md entries
   - Create execution steps + acceptance checklist in `todo.md`
   - Get approval before touching `src/` code

2. **Execution Phase**
   - Implement only in `src/` following "scaffolding regenerable" principle
   - Keep diffs precise, function signatures clear
   - Run `ruff check src` after each small step
   - If scope changes, update SOP & todo first

3. **Quality Standards**
   - Public APIs must have type annotations
   - No bare `except` or silent failures - use `agentscope._logging.logger`
   - Ruff must be zero-warning before merge
   - Follow PEP 8, 4-space indent, 79-char line width

4. **Verification Phase**
   - Run relevant tests: `pytest`, `mypy src`, `ruff check src`
   - Update examples, READMEs, CLAUDE.md
   - Accept clean history only (rebase/FF)

**Documentation Standards**
- **Emoji-Free Policy**: All documentation files (SOP.md, CLAUDE.md, README.md, etc.) must NOT contain any emoji characters
- Use plain text headings and formatting: `## Title` instead of `## 🎯 Title`
- Maintain professional technical documentation style
- Verify emoji removal during code review before merging
- This ensures compatibility across different viewers and maintains formal documentation tone
