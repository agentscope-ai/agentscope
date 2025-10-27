# ReMe Long-Term Memory in AgentScope

This example demonstrates how to

- use ReMe (Reflection Memory) to provide three specialized types of persistent memory storage for AgentScope agents,
- record and retrieve personal information, task execution trajectories, and tool usage patterns across sessions,
- integrate long-term memory with agents for context-aware conversations and task learning, and
- configure DashScope embedding models and vector stores for comprehensive memory management.

## Overview

ReMe (Reflection Memory) provides three types of long-term memory:

1. **Personal Memory** - Records and retrieves persistent personal information about users
2. **Task Memory** - Learns from execution trajectories and retrieves relevant task experiences
3. **Tool Memory** - Records tool execution results and provides usage guidelines

## Prerequisites

- Python 3.10 or higher
- DashScope API key from Alibaba Cloud

## QuickStart

Install agentscope and ensure you have a valid DashScope API key in your environment variables.

> Note: The examples are built with DashScope chat model and embedding model. If you want to use OpenAI models instead,
> modify the model initialization in the example code accordingly.

```bash
# Install agentscope from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .
# Install dependencies
pip install reme-ai python-dotenv
```

Set up your API key:

```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
```

Run the examples:

```bash
# Personal Memory Example
python personal_memory_example.py

# Task Memory Example
python task_memory_example.py

# Tool Memory Example
python tool_memory_example.py
```

The examples will:
1. Initialize the appropriate ReMe memory type with DashScope models
2. Demonstrate all core interfaces for recording and retrieval
3. Show real-world usage patterns for each memory type
4. Display results with clear success indicators

## Key Features

- **Specialized Memory Types**: Three distinct memory types for different use cases
- **Vector-based Storage**: Uses vector database for efficient semantic search and retrieval
- **Flexible Configuration**: Support for multiple embedding models and vector stores
- **Async Operations**: Full async support for non-blocking memory operations
- **Agent Integration**: Seamless integration with AgentScope's agent system and tools

## Basic Usage

### 1. Personal Memory

Record and retrieve persistent personal information about users.

```python
import os
from agentscope.memory.reme import ReMePersonalMemory
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel

# Initialize with DashScope models
long_term_memory = ReMePersonalMemory(
    agent_name="Friday",
    user_name="user_123",
    model=DashScopeChatModel(
        model_name="qwen3-max",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        stream=False,
    ),
    embedding_model=DashScopeTextEmbedding(
        model_name="text-embedding-v4",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        dimensions=1024,
    ),
)

# Use async context manager
async with long_term_memory:
    # Record personal information
    await long_term_memory.record_to_memory(
        thinking="The user is sharing their travel preferences",
        content=[
            "I prefer to stay in homestays when traveling to Hangzhou",
            "I like to visit the West Lake in the morning",
        ],
    )
    
    # Retrieve memories
    result = await long_term_memory.retrieve_from_memory(
        keywords=["Hangzhou travel"],
        limit=3,
    )
```

### 2. Task Memory

Learn from execution trajectories and retrieve relevant task experiences.

```python
from agentscope.memory.reme import ReMeTaskMemory

# Initialize task memory
long_term_memory = ReMeTaskMemory(
    agent_name="TaskAssistant",
    workspace_id="task_workspace_123",
    model=DashScopeChatModel(
        model_name="qwen3-max",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        stream=False,
    ),
    embedding_model=DashScopeTextEmbedding(
        model_name="text-embedding-v4",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        dimensions=1024,
    ),
)

# Use async context manager
async with long_term_memory:
    # Record execution trajectories with scores
    await long_term_memory.record_to_memory(
        trajectories=[
            {
                "messages": [
                    {"role": "user", "content": "Help me create a project plan"},
                    {"role": "assistant", "content": "Let's break it down into phases..."},
                ],
                "score": 0.9  # Success score (0.0 to 1.0)
            }
        ],
    )
    
    # Retrieve relevant experiences
    result = await long_term_memory.retrieve_from_memory(
        query="What are best practices for project planning?",
        top_k=3,
    )
```

### 3. Tool Memory

Record tool execution results and generate usage guidelines.

```python
from agentscope.memory.reme import ReMeToolMemory
from datetime import datetime

# Initialize tool memory
long_term_memory = ReMeToolMemory(
    agent_name="ToolAssistant",
    workspace_id="tool_workspace_123",
    model=DashScopeChatModel(
        model_name="qwen3-max",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        stream=False,
    ),
    embedding_model=DashScopeTextEmbedding(
        model_name="text-embedding-v4",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        dimensions=1024,
    ),
)

# Use async context manager
async with long_term_memory:
    # Record tool execution results
    await long_term_memory.add_tool_call_result(
        tool_call_results=[
            {
                "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tool_name": "web_search",
                "input": {"query": "Python asyncio", "max_results": 10},
                "output": "Found 10 relevant articles...",
                "token_cost": 150,
                "success": True,
                "time_cost": 2.3
            }
        ],
    )
    
    # Generate usage guidelines
    result = await long_term_memory.summary_tool_memory(
        tool_names="web_search",
    )
    
    # Retrieve tool guidelines
    guidelines = await long_term_memory.retrieve_tool_memory(
        tool_names="web_search",
    )
```

## Advanced Configuration

### Memory Interfaces

Each memory type provides two types of interfaces:

**Tool Functions** (for agent tool calling):
- `record_to_memory()` - Returns `ToolResponse` objects for agent integration
- `retrieve_from_memory()` - Returns formatted text output for agents

**Direct Methods** (for programmatic use):
- `record()` - Returns simple types (None, str) for flexible workflows
- `retrieve()` - Direct retrieval without tool response wrapping

### Using Async Context Manager

All ReMe memory types must be used with async context managers:

```python
async with long_term_memory:
    # All memory operations must be within this context
    await long_term_memory.record(msgs=[...])
    result = await long_term_memory.retrieve(msg=...)
```

### Custom Configuration

You can customize the ReMe initialization by passing custom parameters:

```python
long_term_memory = ReMePersonalMemory(
    agent_name="Friday",
    user_name="user_123",
    model=your_model,
    embedding_model=your_embedding_model,
    vector_store_dir="./custom_memory_path",  # Custom storage path
)
```

## What's in the Examples

The example files demonstrate:

### `personal_memory_example.py`
1. **4 Core Interfaces**: Tool functions and direct methods for personal memory
2. **Memory Recording**: Recording user preferences and personal information
3. **Keyword Retrieval**: Searching for stored information using keywords
4. **Query-based Retrieval**: Finding relevant memories using natural language queries

### `task_memory_example.py`
1. **4 Core Interfaces**: Recording and retrieving task execution trajectories
2. **Trajectory Learning**: Learning from successful and failed task attempts
3. **Score-based Recording**: Associating success scores (0.0-1.0) with trajectories
4. **Experience Retrieval**: Finding relevant past experiences for new tasks

### `tool_memory_example.py`
1. **5 Core Interfaces**: Complete tool memory management system
2. **Execution Recording**: Tracking tool inputs, outputs, costs, and success rates
3. **Guideline Generation**: Automatically generating usage guidelines from history
4. **Pattern Learning**: Helping agents learn optimal tool usage patterns

## Architecture Notes

All three memory types inherit from `ReMeBaseLongTermMemory` which:
- Integrates with the ReMe library's `ReMeApp`
- Manages async context for proper initialization
- Provides common interfaces for memory operations
- Supports both tool function calls and direct method calls

## Important Notes

- All examples use DashScope models by default, but you can substitute with other models
- Memory is persisted in the `memory_vector_store/` directory by default
- Each `workspace_id` or `user_name` maintains separate memory storage
- The async context manager ensures proper resource initialization and cleanup

## Reference

- [ReMe Library Documentation](https://github.com/modelscope/ReMe)
- [AgentScope Documentation](https://github.com/modelscope/agentscope)
- [DashScope API](https://dashscope.aliyun.com/)
