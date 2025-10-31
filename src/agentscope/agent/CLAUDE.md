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
- **`_subagent_base.py`** - Delegation skeleton derived from `AgentBase`; injects shared resources, enforces filesystem namespace, loads delegation context, wraps errors; owns a fresh `Toolkit` and supports bulk registration of provided tools.
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

- Responsibility: Provide a minimal `AgentBase` derivative that can be treated as a Toolkit tool while staying free of business logic. Shared resources (logger, tracing, filesystem, session, long-term memory) are injected during `export_agent`; filesystem access is restricted to `/workspace/subagents/<name>/`; short-term state, a self‑owned toolkit (bulk‑registered from a provided tool list), and hooks remain isolated per call.
- Key Methods:
  - `SubAgentBase.get_input_model()` → returns the Pydantic model declaring the tool's input contract (required; raises if absent).
  - `SubAgentBase._pre_context_compress(parent_context, input_obj)` → normalizes host context into `DelegationContext` with `input_payload`, recent events, workspace pointers, safety flags, and optional preview text.
  - `SubAgentBase.export_agent(permissions, parent_context, input_obj, *, delegation_context=None, tools=None)` → fresh subagent instance with shared resources injected; used for registration probe and every invocation.
  - `SubAgentBase.delegate(input_obj, *, delegation_context)` → restores context, caches the validated model (`self._current_input`), calls the subclass `reply(input_obj, **kwargs)`, folds its `Msg` into a single `ToolResponse(is_last=True)`, marks failures with `metadata["unavailable"]=True`.
  - `make_subagent_tool(cls, spec, tool_name)` → loads `InputModel`, generates OpenAI function schema via `model_json_schema()`, runs a zero-arg registration probe (requires defaults), validates runtime payloads with `model_validate`, annotates host messages, and orchestrates export/delegate per call.
- Invariants: no live streaming toward parent queues, no implicit access to host toolkit or hooks, exceptions never leak raw stack traces.
- Call Path: `ReActAgent._acting` (detect tool name) → `make_subagent_tool` wrapper → `InputModel.model_validate(arguments)` → `_pre_context_compress` → `SubAgentBase.export_agent(..., input_obj=payload)` → `delegate` → `ToolResponse`.

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

### Search SubAgent Implementation

**Location:** `examples/agent_search_subagent/`
**Status:** Production-ready example of SubAgent pattern

#### Overview
The Search SubAgent provides intelligent multi-provider search capabilities with automatic fallback mechanisms. It aggregates Google, Bing, Sogou, Wikipedia, and GitHub search engines into a unified interface, demonstrating full SubAgent pattern compliance.

#### Key Components

**Input Model (`SearchSubAgentInput`)**
- **Zero-Deviation Compliance**: Strictly `{query: str}` parameter only
- **Context Handling**: Via simple concatenation (`query + " " + context`)
- **Validation**: Length limits, whitespace normalization
- **Reference**: `examples/agent_search_subagent/search_input.py:15`

**Main Class (`SearchSubAgent`)**
- **Intelligent Routing**: Code→GitHub, Academic→Wiki, General→Google/Bing
- **Fallback Chain**: Automatic degradation to next available provider
- **Result Processing**: Deduplication, aggregation, format standardization
- **Reference**: `examples/agent_search_subagent/search_subagent.py:85`

**Tool Integration (`tools.py`)**
- **Provider Management**: Centralized search tool registration
- **Query Classification**: Automatic provider selection based on query type
- **Result Formatting**: Unified output across different providers
- **Reference**: `examples/agent_search_subagent/tools.py:35`

#### Call Graph
```
HostAgent._acting → make_subagent_tool → InputModel.model_validate → SearchSubAgent.delegate → SearchSubAgent.reply → _execute_intelligent_search → format_search_results → ToolResponse → HostAgent
```

#### Testing Coverage
- **Unit Tests**: `tests/agent/test_search_subagent.py` (input validation, tool integration)
- **Integration Tests**: `tests/agent/test_search_subagent_integration.py` (delegation flow, memory isolation)
- **Demo Script**: `examples/agent_search_subagent/demo.py` (end-to-end usage examples)

#### Usage Example
```python
from examples.agent_search_subagent import SearchSubAgent
from src.agentscope.tool._subagent_tool import make_subagent_tool, SubAgentSpec

# Register with host agent (minimal tool set since intelligence moved to tool layer)
search_spec = SubAgentSpec(name="search_web", tools=[])
tool_func, schema = make_subagent_tool(SearchSubAgent, search_spec, "search_web")
host_agent.toolkit.register_tool_function(tool_func, json_schema=schema)
```

#### Architecture Correction
Original implementation violated SubAgent patterns - corrected to proper minimal wrapper:
- **Before**: Complex routing logic in agent layer (wrong)
- **After**: Simple delegation to intelligent search tool (correct)
- **Code size**: From 300+ lines to ~30 lines (Linus style)

## Related Modules

- **[Model Integration](../model/CLAUDE.md)**
- **[Memory Management](../memory/CLAUDE.md)**
- **[Tool Management](../tool/CLAUDE.md)**
- **[Search Tools](../tool/_search/CLAUDE.md)**

---

[Back to Project Root](../CLAUDE.md)
