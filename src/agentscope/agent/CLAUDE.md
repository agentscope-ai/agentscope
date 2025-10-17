# Agent Module

**Location:** `src/agentscope/agent/`
**Parent:** [Project Root](../CLAUDE.md)

## Overview

The agent module provides the core building blocks for creating and managing agents in AgentScope-easy. This module implements various agent types including ReAct agents, user agents, and custom agent implementations.

## Key Files

### Core Agent Classes
- **`_agent_base.py`** - Base classes for all agents including `AgentBase`
- **`_react_agent.py`** - ReAct (Reasoning + Action) agent implementation
- **`_user_agent.py`** - User interaction agents and input handling
- **`__init__.py`** - Module exports and public interfaces

### Advanced Features
- **`_agent_meta.py`** - Meta-programming capabilities for agent construction
- **`_react_agent_base.py`** - Base ReAct agent functionality
- **`_subagent_base.py`** - Delegation skeleton derived from `AgentBase`; injects shared resources, enforces filesystem namespace, loads delegation context, wraps errors.
- **`_subagent_tool.py`** - Factory helpers (`make_subagent_tool`, specs) that expose `SubAgentBase` subclasses as Toolkit tool functions.

## Dependencies

- Model providers (`src/agentscope/model/`)
- Memory management (`src/agentscope/memory/`)
- Tool framework (`src/agentscope/tool/`)
- Formatter system (`src/agentscope/formatter/`)

## Entry Points

### Main Exports
```python
from agentscope.agent import AgentBase, ReActAgent, UserAgent
```

### Feature Support
- **Async execution** - All agents support asynchronous operations
- **Realtime steering** - Native interruption support with memory preservation
- **Automatic state management** - Built-in state tracking and recovery

## SubAgent Skeleton (Framework Scope)

- Responsibility: Provide a minimal `AgentBase` derivative that can be treated as a Toolkit tool while staying free of business logic. Shared resources (logger, tracing, filesystem, session, long-term memory) are injected during `export_agent`; filesystem access is restricted to `/workspace/subagents/<name>/`; short-term state, toolkit (allowlist clone), and hooks remain isolated per call.
- Key Methods:
  - `SubAgentBase.export_agent(permissions, parent_context, task, *, delegation_context, run_healthcheck=False, tools_allowlist=None, host_toolkit=None)` → fresh subagent instance; only runs `healthcheck()` when explicitly requested (registration stage).
  - `SubAgentBase._pre_context_compress(parent_context, task)` → normalized `DelegationContext` payload produced by the host before delegation.
  - `SubAgentBase.delegate(task_summary, delegation_context, **kwargs)` → loads delegation context, calls the subclass `reply`, folds outputs into a single `ToolResponse` with `is_last=True`, wraps failures using `metadata["unavailable"]=True` and audit fields (`subagent`, `supervisor`).
  - `make_subagent_tool(cls, spec, tool_name)` → registers subagent tool functions; runs health check once at registration, clones allowlisted tools, injects permissions/context before each call.
- Invariants: no live streaming toward parent queues, no implicit access to host toolkit or hooks, exceptions never leak raw stack traces.
- Call Path: `ReActAgent._acting` (detect tool name) → `make_subagent_tool` wrapper → `SubAgentBase.export_agent(..., delegation_context=host_summary)` → `delegate` → `ToolResponse`.

## Testing

- Current coverage: `react_agent_test.py`, `hook_test.py`, `user_input_test.py`.
- Subagent skeleton verification: `tests/agent/test_subagent_tool.py`, `test_subagent_memory_isolation.py`, `test_subagent_allowlist_schema.py`, `test_subagent_fs_namespace.py`, `test_subagent_parallel.py`, `test_subagent_error_propagation.py`, `test_subagent_context_compress.py`, `test_subagent_lifecycle.py`.

## Usage Examples

```python
# ReAct Agent with reasoning capabilities
agent = ReActAgent(
    name="Researcher",
    model=OpenAIChatModel("gpt-4"),
    memory=InMemoryMemory(),
    formatter=OpenAIChatFormatter(),
    toolkit=Toolkit()
)

# User interaction agent
user = UserAgent(name="Human")
```

Subagent business implementations (e.g., research or retrieval assistants) must live in `examples/` or downstream projects; the core module only ships the delegation skeleton described above.

## Related Modules

- **[Model Integration](../model/CLAUDE.md)**
- **[Memory Management](../memory/CLAUDE.md)**
- **[Tool Management](../tool/CLAUDE.md)**

---

[Back to Project Root](../CLAUDE.md)
