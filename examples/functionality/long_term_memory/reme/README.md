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

- Python 3.12 or higher
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

    # Retrieve memories (limit defaults to 3)
    result = await long_term_memory.retrieve_from_memory(
        keywords=["Hangzhou travel"],
    )
```

### 2. Task Memory

Learn from execution trajectories and retrieve relevant task experiences.

```python
from agentscope.memory.reme import ReMeTaskMemory

# Initialize task memory
long_term_memory = ReMeTaskMemory(
    agent_name="TaskAssistant",
    user_name="task_workspace_123",  # This serves as workspace_id in ReMe
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
    # Record task execution information
    await long_term_memory.record_to_memory(
        thinking="Recording project planning best practices",
        content=[
            "Break down into phases: Requirements, Design, Development, Testing, Deployment",
            "Use Agile methodology with 2-week sprints",
        ],
        score=0.9,  # Optional: score for this trajectory (default is 1.0)
    )

    # Retrieve relevant experiences using keywords
    result = await long_term_memory.retrieve_from_memory(
        keywords=["project planning", "best practices"],
        # top_k defaults to 5
    )
```

### 3. Tool Memory

Record tool execution results and retrieve usage guidelines.

```python
import json
from agentscope.memory.reme import ReMeToolMemory
from datetime import datetime

# Initialize tool memory
long_term_memory = ReMeToolMemory(
    agent_name="ToolAssistant",
    user_name="tool_workspace_123",  # This serves as workspace_id in ReMe
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
    # Record tool execution results (content must be JSON strings)
    tool_result = {
        "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tool_name": "web_search",
        "input": {"query": "Python asyncio", "max_results": 10},
        "output": "Found 10 relevant articles...",
        "token_cost": 150,
        "success": True,
        "time_cost": 2.3
    }

    await long_term_memory.record_to_memory(
        thinking="Recording web_search tool execution to learn usage patterns",
        content=[json.dumps(tool_result)],
    )

    # Retrieve tool guidelines (automatically includes summarization)
    result = await long_term_memory.retrieve_from_memory(
        keywords=["web_search"],
    )
```

## Advanced Configuration

### Memory Interfaces

#### Personal Memory Interfaces

**Tool Functions** (for agent tool calling):

- `record_to_memory(thinking: str, content: list[str], **kwargs)` - Returns `ToolResponse` objects for agent integration
    - `thinking`: Your reasoning about what to record
    - `content`: List of strings to remember
- `retrieve_from_memory(keywords: list[str], **kwargs)` - Returns `ToolResponse` with formatted text output
    - `keywords`: List of keywords to search for
    - Optional `limit`: Number of results per keyword (defaults to 3)

**Direct Methods** (for programmatic use):

- `record(msgs: list[Msg | None], **kwargs)` - Records message conversations, returns None
- `retrieve(msg: Msg | list[Msg] | None, **kwargs)` - Returns retrieved memories as string
    - Optional `top_k`: Number of results to retrieve (defaults to 3)

#### Task Memory Interfaces

**Tool Functions** (for agent tool calling):

- `record_to_memory(thinking: str, content: list[str], **kwargs)` - Returns `ToolResponse` objects for agent integration
    - `thinking`: Your reasoning about what to record
    - `content`: List of strings representing task execution information
    - Optional `score`: Score for this trajectory (defaults to 1.0)
- `retrieve_from_memory(keywords: list[str], **kwargs)` - Returns `ToolResponse` with formatted text output
    - `keywords`: List of keywords to search for (e.g., task name, execution context)
    - Optional `top_k`: Number of results to retrieve (defaults to 5)

**Direct Methods** (for programmatic use):

- `record(msgs: list[Msg | None], **kwargs)` - Records message conversations, returns None
    - Optional `score` in kwargs: Score for this trajectory (defaults to 1.0)
- `retrieve(msg: Msg | list[Msg] | None, **kwargs)` - Returns retrieved task experiences as string
    - Optional `top_k`: Number of results to retrieve (defaults to 5)

#### Tool Memory Interfaces

**Tool Functions** (for agent tool calling):

- `record_to_memory(thinking: str, content: list[str], **kwargs)` - Returns `ToolResponse` objects for agent integration
    - `thinking`: Your reasoning about what to record
    - `content`: List of JSON strings, each representing a tool_call_result with fields:
        - `create_time`: Timestamp in format "%Y-%m-%d %H:%M:%S"
        - `tool_name`: Name of the tool
        - `input`: Input parameters (dict)
        - `output`: Tool output (string)
        - `token_cost`: Token cost (int)
        - `success`: Success status (bool)
        - `time_cost`: Execution time in seconds (float)
    - Note: Automatically triggers summarization for affected tools
- `retrieve_from_memory(keywords: list[str], **kwargs)` - Returns `ToolResponse` with tool usage guidelines
    - `keywords`: List of tool names to retrieve guidelines for

**Direct Methods** (for programmatic use):

- `record(msgs: list[Msg | None], **kwargs)` - Records messages containing JSON-formatted tool results, returns None
    - Message content should be JSON strings with tool_call_result format
    - Automatically triggers summarization for affected tools
- `retrieve(msg: Msg | list[Msg] | None, **kwargs)` - Returns retrieved tool guidelines as string
    - Message content should contain tool names (comma-separated if multiple)

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

1. **4 Core Interfaces**: Tool functions (`record_to_memory`, `retrieve_from_memory`) and direct methods (`record`,
   `retrieve`)
2. **Task Information Recording**: Recording task execution information with thinking and content
3. **Score-based Recording**: Associating success scores (0.0-1.0) with trajectories (defaults to 1.0)
4. **Keyword-based Retrieval**: Finding relevant past experiences using keywords (defaults to top_k=5)

### `tool_memory_example.py`

1. **4 Core Interfaces**: Tool functions (`record_to_memory`, `retrieve_from_memory`) and direct methods (`record`,
   `retrieve`)
2. **JSON-formatted Recording**: Recording tool execution results as JSON strings with detailed metadata
3. **Automatic Summarization**: Guidelines are automatically generated when recording tool results
4. **Multi-tool Retrieval**: Retrieving guidelines for single or multiple tools at once

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
