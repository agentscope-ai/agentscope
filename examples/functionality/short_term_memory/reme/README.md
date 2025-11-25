# ReMe Short-Term Memory in AgentScope

This example demonstrates how to

- use ReMeShortTermMemory to provide automatic working memory management for AgentScope agents,
- handle long conversation contexts with intelligent summarization and compaction,
- integrate short-term memory with ReAct agents for efficient tool usage and context management, and
- configure DashScope models for memory operations.

## Prerequisites

- Python 3.10 or higher
- DashScope API key from Alibaba Cloud


## QuickStart

Install agentscope and ensure you have a valid DashScope API key in your environment variables.

> Note: The example is built with DashScope chat model. If you want to use OpenAI models instead,
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

Or create a `.env` file:

```bash
DASHSCOPE_API_KEY=YOUR_API_KEY
```

Run the example:

```bash
python short_term_memory_example.py
```

The example will:
1. Initialize a ReMeShortTermMemory instance with DashScope models
2. Demonstrate automatic memory compaction for long tool responses
3. Show integration with ReActAgent for context-aware conversations
4. Use grep and read_file tools to search and retrieve information from files

## Key Features

- **Automatic Memory Management**: Intelligent summarization and compaction of working memory to handle long contexts
- **Tool Response Optimization**: Automatic truncation and summarization of large tool responses to stay within token limits
- **Flexible Configuration**: Configurable thresholds for compaction ratio, token limits, and recent message retention
- **ReAct Agent Integration**: Seamless integration with AgentScope's ReActAgent and tool system
- **Async Operations**: Full async support for non-blocking memory operations

## Basic Usage

### Initialize Memory

```python
import os
from agentscope.memory import ReMeShortTermMemory
from agentscope.model import DashScopeChatModel

# Initialize with DashScope model
llm = DashScopeChatModel(
    model_name="qwen3-coder-30b-a3b-instruct",
    api_key=os.environ.get("DASHSCOPE_API_KEY"),
)

short_term_memory = ReMeShortTermMemory(
    model=llm,
    working_summary_mode="auto",
    compact_ratio_threshold=0.75,
    max_total_tokens=20000,
    max_tool_message_tokens=2000,
    keep_recent_count=1,
    store_dir="working_memory",
)
```

### Integrate with ReAct Agent

```python
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.tool import Toolkit
from agentscope.message import Msg

# Create a ReAct agent with short-term memory
toolkit = Toolkit()
# Register your tools here
# toolkit.register_tool_function(your_tool_function)

agent = ReActAgent(
    name="react",
    sys_prompt="You are a helpful assistant.",
    model=llm,
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
    memory=short_term_memory,
    max_iters=20,
)

# Use the agent with async context manager
async with short_term_memory:
    msg = Msg(
        role="user",
        content="Your question here",
        name="user"
    )
    response = await agent(msg)
```

### Using Async Context Manager

`ReMeShortTermMemory` implements the async context manager protocol, which ensures proper initialization and cleanup of resources. There are two ways to use it:

#### Recommended: Using `async with` Statement

The recommended approach is to use the `async with` statement, which automatically handles resource management:

```python
async with short_term_memory:
    # Memory is initialized here
    await short_term_memory.add(messages)
    response = await agent(msg)
    # Memory is automatically cleaned up when exiting the block
```

#### Alternative: Manual `__aenter__` and `__aexit__` Calls

You can also manually call `__aenter__` and `__aexit__` if you need more control:

```python
# Manually initialize
await short_term_memory.__aenter__()

try:
    # Use the memory
    await short_term_memory.add(messages)
    response = await agent(msg)
finally:
    # Manually cleanup
    await short_term_memory.__aexit__(None, None, None)
```

> **Note**: It's recommended to use the `async with` statement as it ensures proper resource cleanup even if an exception occurs.

## Configuration Parameters

- **`working_summary_mode`** (`str`): Mode for working memory summarization. Options: `"auto"`, `"manual"`, or `"off"`. Default: `"auto"`.
- **`compact_ratio_threshold`** (`float`): Threshold ratio (0-1) that triggers memory compaction when exceeded. Default: `0.75`.
- **`max_total_tokens`** (`int`): Maximum total tokens allowed in memory before compaction. Default: `20000`.
- **`max_tool_message_tokens`** (`int`): Maximum tokens allowed for a single tool response message. Default: `2000`.
- **`group_token_threshold`** (`int | None`): Token threshold for grouping messages during compaction. Default: `None`.
- **`keep_recent_count`** (`int`): Number of recent messages to keep during compaction. Default: `1`.
- **`store_dir`** (`str`): Directory path for storing working memory data. Default: `"working_memory"`.

## Advanced Configuration

You can customize the ReMe config by passing a config path:

```python
short_term_memory = ReMeShortTermMemory(
    model=llm,
    reme_config_path="path/to/your/config.yaml",  # Pass your custom ReMe configuration
    # ... other parameters
)
```

For more configuration options, refer to the [ReMe documentation](https://github.com/agentscope-ai/ReMe).

## What's in the Example

The `short_term_memory_example.py` file demonstrates:

1. **Tool Integration**: Registering `grep` and `read_file` tools for searching and reading files
2. **Memory Initialization**: Setting up ReMeShortTermMemory with appropriate parameters for handling long contexts
3. **Long Context Handling**: Adding a large tool response (README content × 10) to demonstrate automatic memory compaction
4. **ReAct Agent Usage**: Using the agent with short-term memory to answer questions based on retrieved information

## Example Workflow

The example shows a typical workflow:

1. User asks to search for project information
2. Agent uses `grep` tool to find relevant content
3. Agent uses `read_file` tool to read specific sections
4. Large tool responses are automatically compacted by the memory system
5. Agent answers the user's question based on the retrieved information

## Reference

- [ReMe Documentation](https://github.com/agentscope-ai/ReMe)
- [AgentScope Documentation](https://github.com/modelscope/agentscope)

