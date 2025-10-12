# Agent Module

**Location:** `src/agentscope/agent/`
**Parent:** [AgentScope Root](../CLAUDE.md)

## 🧩 Overview

The agent module provides the core building blocks for creating and managing agents in AgentScope. This module implements various agent types including ReAct agents, user agents, and custom agent implementations.

## 📚 Key Files

### Core Agent Classes
- **`_agent_base.py`** - Base classes for all agents including `AgentBase`
- **`_react_agent.py`** - ReAct (Reasoning + Action) agent implementation
- **`_user_agent.py`** - User interaction agents and input handling
- **`__init__.py`** - Module exports and public interfaces

### Advanced Features
- **`_agent_meta.py`** - Meta-programming capabilities for agent construction
- **`_react_agent_base.py`** - Base ReAct agent functionality

## 🔧 Dependencies

- Model providers (`src/agentscope/model/`)
- Memory management (`src/agentscope/memory/`)
- Tool framework (`src/agentscope/tool/`)
- Formatter system (`src/agentscope/formatter/`)

## 🎯 Entry Points

### Main Exports
```python
from agentscope.agent import AgentBase, ReActAgent, UserAgent
```

### Feature Support
- **Async execution** - All agents support asynchronous operations
- **Realtime steering** - Native interruption support with memory preservation
- **Automatic state management** - Built-in state tracking and recovery

## 🧪 Testing

- Test files located in `tests/` directory
- Look for `*agent*_test.py` files

## 🚀 Usage Examples

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

## 🔗 Related Modules

- **[Model Integration](../model/CLAUDE.md)**
- **[Memory Management](../memory/CLAUDE.md)**
- **[Tool Management](../tool/CLAUDE.md)**

---

🏠 [Back to AgentScope Root](../CLAUDE.md)