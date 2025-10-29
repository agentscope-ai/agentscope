# ReMe Long-Term Memory in AgentScope

This example demonstrates how to:

- Use ReMe (Reflection Memory) to provide three specialized types of persistent memory storage for AgentScope agents
- Record and retrieve personal information, task execution trajectories, and tool usage patterns across sessions
- Integrate long-term memory with ReActAgent for context-aware conversations and continuous learning
- Configure DashScope embedding models and vector stores for efficient memory management

## Overview

ReMe (Reflection Memory) provides three types of long-term memory for intelligent agents:

1. **Personal Memory** (`ReMePersonalLongTermMemory`) - Records and retrieves persistent personal information, preferences, and facts about users
2. **Task Memory** (`ReMeTaskLongTermMemory`) - Learns from task execution trajectories and retrieves relevant past experiences for similar tasks
3. **Tool Memory** (`ReMeToolLongTermMemory`) - Records tool execution results and generates usage guidelines to improve tool calling

## Prerequisites

- Python 3.12 or higher
- DashScope API key from Alibaba Cloud (for the examples)

## QuickStart

### Installation

```bash
# Install agentscope from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .

# Install required dependencies
pip install reme-ai python-dotenv
```

### Setup

Set up your API key:

```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
```

Or create a `.env` file:

```bash
DASHSCOPE_API_KEY=YOUR_API_KEY
```

### Run Examples

```bash
# Personal Memory Example - 5 core interfaces
python personal_memory_example.py

# Task Memory Example - 5 core interfaces
python task_memory_example.py

# Tool Memory Example - Complete workflow with ReActAgent
python tool_memory_example.py
```

> **Note**: The examples use DashScope models by default. To use OpenAI or other models, modify the model initialization in the example code accordingly.

## Key Features

- **Three Specialized Memory Types**: Personal, Task, and Tool memory for different use cases
- **Dual Interface Design**: Both tool functions (for agent calling) and direct methods (for programmatic use)
- **Vector-based Retrieval**: Efficient semantic search using embedding models and vector stores
- **Async-first Architecture**: Full async/await support for non-blocking operations
- **ReActAgent Integration**: Seamless integration with AgentScope's ReActAgent and Toolkit
- **Automatic Context Management**: Uses async context managers for proper resource handling

## Core Concepts

### Memory Types and Their Use Cases

| Memory Type | Purpose | When to Use |
|------------|---------|-------------|
| **Personal Memory** | Store user preferences, habits, and personal facts | User profiles, personalized assistants, long-term user context |
| **Task Memory** | Learn from task execution trajectories | Problem-solving, debugging, repeated workflows, learning from past successes |
| **Tool Memory** | Record tool usage patterns and generate guidelines | Tool-using agents, improving tool call accuracy, avoiding past errors |

### Interface Design

**Personal Memory** and **Task Memory** provide **5 core interfaces**:

1. **`record_to_memory()`** - Tool function for agents to record memories (returns `ToolResponse`)
2. **`retrieve_from_memory()`** - Tool function for agents to retrieve memories (returns `ToolResponse`)
3. **`record()`** - Direct method for programmatic recording (returns `None`)
4. **`retrieve()`** - Direct method for programmatic retrieval (returns `str`)
5. **ReActAgent Integration** - Use memory with `long_term_memory` and `long_term_memory_mode` parameters

**Tool Memory** provides **2 core interfaces** (no tool functions):

1. **`record()`** - Direct method for recording tool execution results (returns `None`)
2. **`retrieve()`** - Direct method for retrieving tool usage guidelines (returns `str`)

## Usage Examples

### 1. Personal Memory

**Use Case**: Record and retrieve user preferences, habits, and personal information.

```python
import asyncio
import os
from agentscope.memory import ReMePersonalLongTermMemory
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel


async def main():
    # Initialize personal memory
    personal_memory = ReMePersonalLongTermMemory(
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

    # Use async context manager (required!)
    async with personal_memory:
        # Interface 1: record_to_memory (tool function)
        result = await personal_memory.record_to_memory(
            thinking="User sharing travel preferences",
            content=[
                "I prefer to stay in homestays when traveling to Hangzhou",
                "I like to visit the West Lake in the morning",
                "I enjoy drinking Longjing tea",
            ],
        )

        # Interface 2: retrieve_from_memory (tool function)
        result = await personal_memory.retrieve_from_memory(
            keywords=["Hangzhou travel", "tea preference"],
        )

        # Interface 3: record (direct method)
        await personal_memory.record(
            msgs=[
                Msg(role="user", content="I work as a software engineer", name="user"),
                Msg(role="assistant", content="Got it!", name="assistant"),
            ],
        )

        # Interface 4: retrieve (direct method)
        memories = await personal_memory.retrieve(
            msg=Msg(role="user", content="What do you know about my work?", name="user"),
        )
        print(memories)


asyncio.run(main())
```

**Integration with ReActAgent** (Interface 5):

```python
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit

async def use_with_agent():
    personal_memory = ReMePersonalLongTermMemory(...)

    async with personal_memory:
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are Friday with long-term memory. Always record user information and retrieve memories when needed.",
            model=DashScopeChatModel(...),
            formatter=DashScopeChatFormatter(),
            toolkit=Toolkit(),
            memory=InMemoryMemory(),
            long_term_memory=personal_memory,  # Attach personal memory
            long_term_memory_mode="both",  # Enable both record and retrieve tools
        )

        # Agent can now use record_to_memory and retrieve_from_memory as tools
        msg = Msg(role="user", content="I prefer staying in homestays", name="user")
        response = await agent(msg)
```

### 2. Task Memory

**Use Case**: Learn from task execution trajectories and retrieve relevant experiences.

```python
from agentscope.memory import ReMeTaskLongTermMemory


async def main():
    # Initialize task memory
    task_memory = ReMeTaskLongTermMemory(
        agent_name="TaskAssistant",
        user_name="task_workspace_123",  # Acts as workspace_id
        model=DashScopeChatModel(...),
        embedding_model=DashScopeTextEmbedding(...),
    )

    async with task_memory:
        # Interface 1: record_to_memory with score
        result = await task_memory.record_to_memory(
            thinking="Recording successful debugging approach",
            content=[
                "For API 404 errors: Check route definition, verify URL path, ensure correct port",
                "Always use linter to catch typos in route paths",
            ],
            score=0.95,  # High score for successful trajectory
        )

        # Interface 2: retrieve_from_memory
        result = await task_memory.retrieve_from_memory(
            keywords=["debugging", "API errors"],
        )

        # Interface 3: record with score in direct method
        await task_memory.record(
            msgs=[
                Msg(role="user", content="I'm getting a 404 error", name="user"),
                Msg(role="assistant", content="Let's check the route path...", name="assistant"),
                Msg(role="user", content="Found the typo!", name="user"),
            ],
            score=0.95,  # Optional score for this trajectory
        )

        # Interface 4: retrieve (direct method)
        experiences = await task_memory.retrieve(
            msg=Msg(role="user", content="How to debug API errors?", name="user"),
        )
        print(experiences)


asyncio.run(main())
```

**Integration with ReActAgent** (Interface 5):

```python
async def use_with_agent():
    task_memory = ReMeTaskLongTermMemory(...)

    async with task_memory:
        agent = ReActAgent(
            name="TaskAssistant",
            sys_prompt="You are a task assistant. Record solutions and retrieve past experiences before solving problems.",
            model=DashScopeChatModel(...),
            formatter=DashScopeChatFormatter(),
            toolkit=Toolkit(),
            memory=InMemoryMemory(),
            long_term_memory=task_memory,
            long_term_memory_mode="both",
        )

        # Agent learns from task executions over time
        msg = Msg(role="user", content="How should I optimize database queries?", name="user")
        response = await agent(msg)
```

### 3. Tool Memory

**Use Case**: Record tool execution results and generate usage guidelines for better tool calling.

**Complete Workflow**:

```python
import json
from datetime import datetime
from agentscope.memory import ReMeToolLongTermMemory
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import Msg, TextBlock


# Step 1: Define tools
async def web_search(query: str, max_results: int = 5) -> ToolResponse:
    """Search the web for information."""
    result = f"Found {max_results} results for query: '{query}'"
    return ToolResponse(content=[TextBlock(type="text", text=result)])


async def main():
    # Initialize tool memory
    tool_memory = ReMeToolLongTermMemory(
        agent_name="ToolBot",
        user_name="tool_workspace_demo",
        model=DashScopeChatModel(...),
        embedding_model=DashScopeTextEmbedding(...),
    )

    async with tool_memory:
        # Step 2: Record tool execution history (accepts JSON strings in msgs)
        tool_result = {
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "web_search",
            "input": {"query": "Python asyncio tutorial", "max_results": 10},
            "output": "Found 10 results for query: 'Python asyncio tutorial'",
            "token_cost": 150,
            "success": True,
            "time_cost": 2.3
        }

        # Interface 1: record (accepts JSON strings in message content)
        await tool_memory.record(
            msgs=[Msg(role="assistant", content=json.dumps(tool_result), name="assistant")],
        )

        # Step 3: Retrieve tool guidelines
        # Interface 2: retrieve returns summarized guidelines
        guidelines = await tool_memory.retrieve(
            msg=Msg(role="user", content="web_search", name="user"),
        )

        # Step 4: Inject guidelines into agent system prompt
        toolkit = Toolkit()
        toolkit.register_tool_function(web_search)

        base_prompt = "You are ToolBot, a helpful AI assistant."
        enhanced_prompt = f"{base_prompt}\n\n# Tool Guidelines:\n{guidelines}"

        agent = ReActAgent(
            name="ToolBot",
            sys_prompt=enhanced_prompt,  # Guidelines enhance tool usage
            model=DashScopeChatModel(...),
            formatter=DashScopeChatFormatter(),
            toolkit=toolkit,
            memory=InMemoryMemory(),
        )

        # Agent now uses tools with learned guidelines
        msg = Msg(role="user", content="Search for Python design patterns", name="user")
        response = await agent(msg)


asyncio.run(main())
```

> **Note**: Tool Memory does NOT provide `record_to_memory()` and `retrieve_from_memory()` tool functions. It only provides direct `record()` and `retrieve()` methods. Tool Memory is designed to be used programmatically to enhance agent system prompts, not as agent-callable tools.

## API Reference

### Common Parameters

All memory types share these initialization parameters:

```python
ReMePersonalLongTermMemory(
    agent_name: str,           # Name of the agent using this memory
    user_name: str,            # User identifier (acts as workspace_id in ReMe)
    model: ModelWrapper,       # LLM for summarization and processing
    embedding_model: EmbeddingWrapper,  # Embedding model for vector retrieval
    vector_store_dir: str = "./memory_vector_store",  # Storage location
)
```

### Interface Specifications

#### Personal Memory

| Interface | Type | Signature | Returns | Description |
|-----------|------|-----------|---------|-------------|
| `record_to_memory` | Tool Function | `(thinking: str, content: list[str])` | `ToolResponse` | Record personal information with reasoning |
| `retrieve_from_memory` | Tool Function | `(keywords: list[str], limit: int = 3)` | `ToolResponse` | Retrieve memories by keywords |
| `record` | Direct Method | `(msgs: list[Msg])` | `None` | Record message conversations |
| `retrieve` | Direct Method | `(msg: Msg, top_k: int = 3)` | `str` | Query-based retrieval |

**Parameters**:
- `thinking`: Reasoning about what to record
- `content`: List of strings to remember
- `keywords`: Search keywords
- `limit`: Results per keyword (tool function, default: 3)
- `top_k`: Total results to retrieve (direct method, default: 3)

#### Task Memory

| Interface | Type | Signature | Returns | Description |
|-----------|------|-----------|---------|-------------|
| `record_to_memory` | Tool Function | `(thinking: str, content: list[str], score: float = 1.0)` | `ToolResponse` | Record task trajectory with score |
| `retrieve_from_memory` | Tool Function | `(keywords: list[str], top_k: int = 5)` | `ToolResponse` | Retrieve experiences by keywords |
| `record` | Direct Method | `(msgs: list[Msg], score: float = 1.0)` | `None` | Record message conversations with score |
| `retrieve` | Direct Method | `(msg: Msg, top_k: int = 5)` | `str` | Query-based experience retrieval |

**Parameters**:
- `thinking`: Reasoning about the task execution
- `content`: Task execution information and insights
- `score`: Success score for the trajectory (0.0-1.0, default: 1.0)
- `keywords`: Search keywords (e.g., task type, domain)
- `top_k`: Number of results to retrieve (default: 5)

#### Tool Memory

| Interface | Type | Signature | Returns | Description |
|-----------|------|-----------|---------|-------------|
| `record` | Direct Method | `(msgs: list[Msg])` | `None` | Record tool results as messages (JSON format) |
| `retrieve` | Direct Method | `(msg: Msg)` | `str` | Retrieve guidelines for tools |

**Parameters**:
- `msgs`: List of messages where `content` contains JSON strings with tool execution metadata:
  - `create_time`: Timestamp (`"%Y-%m-%d %H:%M:%S"`)
  - `tool_name`: Tool identifier
  - `input`: Parameters used (dict)
  - `output`: Execution result (str)
  - `token_cost`: Token usage (int)
  - `success`: Execution status (bool)
  - `time_cost`: Duration in seconds (float)
- `msg`: Message containing tool name to retrieve guidelines for
- **Note**: Tool Memory does NOT provide tool functions (`record_to_memory` and `retrieve_from_memory`). It only provides direct methods for programmatic use.

### ReActAgent Integration Modes

When attaching **Personal Memory** or **Task Memory** to ReActAgent, use the `long_term_memory_mode` parameter:

```python
agent = ReActAgent(
    name="Assistant",
    long_term_memory=memory,  # ReMePersonalLongTermMemory or ReMeTaskLongTermMemory
    long_term_memory_mode="both",  # Options: "record", "retrieve", "both"
    # ... other parameters
)
```

**Modes**:
- `"record"`: Only adds `record_to_memory` tool to agent
- `"retrieve"`: Only adds `retrieve_from_memory` tool to agent
- `"both"`: Adds both tools (recommended for most use cases)

> **Note**: Tool Memory does NOT support ReActAgent integration with tool functions. Use Tool Memory programmatically to enhance system prompts as shown in the Tool Memory example.

### Async Context Manager (Required!)

All ReMe memory types **must** be used with async context managers:

```python
async with long_term_memory:
    # All memory operations must be within this context
    await long_term_memory.record(msgs=[...])
    result = await long_term_memory.retrieve(msg=...)
```

This ensures:
- Proper initialization of the ReMe backend
- Resource cleanup after operations
- Vector store connection management

### Custom Configuration

```python
from agentscope.memory import ReMePersonalLongTermMemory

# Custom storage location and models
memory = ReMePersonalLongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    model=your_custom_model,  # Any AgentScope-compatible LLM
    embedding_model=your_embedding,  # Any AgentScope-compatible embedding model
    vector_store_dir="./custom_path",  # Custom storage directory
)
```

## Example Files Overview

### `personal_memory_example.py`

Demonstrates **5 core interfaces** for personal memory:

1. **`record_to_memory()`** - Record user preferences using tool function
2. **`retrieve_from_memory()`** - Search memories by keywords using tool function
3. **`record()`** - Direct recording of message conversations
4. **`retrieve()`** - Direct query-based retrieval
5. **ReActAgent Integration** - Agent autonomously uses memory tools

**Key Features**:
- Recording travel preferences, work habits, and personal information
- Keyword-based and query-based retrieval
- System prompt guidelines for agent memory usage
- Automatic memory tool calling by ReActAgent

### `task_memory_example.py`

Demonstrates **5 core interfaces** for task memory:

1. **`record_to_memory()`** - Record task experiences with scores
2. **`retrieve_from_memory()`** - Retrieve relevant experiences by keywords
3. **`record()`** - Direct recording with trajectory scores
4. **`retrieve()`** - Direct experience retrieval
5. **ReActAgent Integration** - Agent learns from past task executions

**Key Features**:
- Recording project planning, debugging, and development experiences
- Score-based trajectory evaluation (0.0-1.0)
- Learning from successful and failed attempts
- Continuous improvement through experience retrieval

### `tool_memory_example.py`

Demonstrates the **complete workflow** for tool memory:

1. **Mock tools** - Define and register tools to Toolkit
2. **Record tool history** - Store execution results with metadata using `record()`
3. **Retrieve guidelines** - Get summarized usage guidelines using `retrieve()`
4. **Enhance agent prompt** - Inject guidelines into system prompt
5. **Use ReActAgent** - Agent uses tools with learned guidelines

**Key Features**:
- JSON-formatted tool execution recording via direct `record()` method
- Automatic guideline generation through summarization
- Multi-tool guideline retrieval via direct `retrieve()` method
- System prompt enhancement for better tool usage
- **Note**: Tool Memory does NOT provide agent-callable tool functions

## Architecture

### Inheritance Hierarchy

```
ReMeLongTermMemoryBase (abstract base)
├── ReMePersonalLongTermMemory
├── ReMeTaskLongTermMemory
└── ReMeToolLongTermMemory
```

**`ReMeLongTermMemoryBase`** provides:
- Integration with ReMe library's `ReMeApp`
- Async context manager implementation
- Common interface definitions
- Vector store and embedding management

### Memory Storage

- **Location**: `./memory_vector_store/` (configurable)
- **Isolation**: Each `user_name` maintains separate storage
- **Persistence**: Memories persist across sessions
- **Format**: Vector embeddings with metadata

## Best Practices

### 1. System Prompt Design

For agents with long-term memory, clearly specify when to record and retrieve:

```python
sys_prompt = """
You are an assistant with long-term memory.

Recording Guidelines:
- Record when users share personal information, preferences, or important facts
- Record successful task execution approaches and solutions
- Record tool execution results with detailed metadata

Retrieval Guidelines:
- ALWAYS retrieve before answering questions about past information
- Retrieve when dealing with similar tasks to past executions
- Check tool guidelines before using tools
"""
```

### 2. Score Assignment (Task Memory)

Use meaningful scores to prioritize experiences:

```python
# Successful trajectory
await task_memory.record_to_memory(..., score=0.95)

# Partially successful
await task_memory.record_to_memory(..., score=0.6)

# Failed trajectory (still useful to learn from)
await task_memory.record_to_memory(..., score=0.2)
```

### 3. Tool Memory Workflow

Follow this pattern for tool memory:

```
1. Execute tool → 2. Record result → 3. Trigger summarization → 4. Retrieve guidelines → 5. Use in agent
```

## Troubleshooting

### Common Issues

**Issue**: `RuntimeError: Memory not initialized`
- **Solution**: Always use `async with memory:` context manager

**Issue**: No memories retrieved
- **Solution**: Ensure you've recorded memories first and check `user_name` matches

**Issue**: Tool memory not generating guidelines
- **Solution**: Record multiple tool executions to trigger summarization

**Issue**: Agent not using memory tools
- **Solution**: Check `long_term_memory_mode="both"` and verify system prompt encourages memory usage

## References

- [ReMe Library](https://github.com/modelscope/ReMe) - Core memory implementation
- [AgentScope Documentation](https://github.com/modelscope/agentscope) - Framework documentation
- [DashScope API](https://dashscope.aliyun.com/) - Model API for examples
