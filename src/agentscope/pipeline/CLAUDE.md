# Pipeline Module

**Location:** `src/agentscope/pipeline/`
**Parent:** [AgentScope Root](../CLAUDE.md)

## 🧩 Overview

The pipeline module provides workflow orchestration capabilities for multi-agent systems, including sequential processing, concurrent execution, and dynamic message routing.

## 📚 Key Components

### Core Orchestration
- **Message Hub (MsgHub)** - Central coordination for multi-agent conversations
- **Sequential pipelines** - Ordered execution of agents
- **Concurrent execution** - Parallel agent processing

### Workflow Types
- **Sequential flows** - One agent after another
- **Concurrent flows** - Multiple agents executing simultaneously

### Multi-Agent Support
- **Dynamic participant management** - Add/remove agents during runtime
- **Broadcast messaging** - Send messages to multiple agents concurrently

## 🎯 Features

### Coordination Patterns
- **Message routing** - Intelligent message distribution based on agent capabilities
- **Handoff mechanisms** - Seamless agent-to-agent transitions

## 🚀 Usage Examples

```python
from agentscope.pipeline import MsgHub, sequential_pipeline

async def multi_agent_conversation():
    async with MsgHub(participants=[agent1, agent2, agent3]) as hub:
    # Sequential execution
    await sequential_pipeline([agent1, agent2, agent3])

    # Dynamic management
    hub.add(agent4)
    hub.delete(agent3)

    # Broadcast to all participants
    await hub.broadcast(Msg("Host", "Goodbye!"))
```

## 🔗 Related Modules

- **[Agent Framework](../agent/CLAUDE.md)**