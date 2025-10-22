 # Mem0 Long-Term Memory in AgentScope

This example demonstrates how to

- use Mem0LongTermMemory to provide persistent semantic memory storage for AgentScope agents,
- record and retrieve conversation history and user preferences across sessions,
- integrate long-term memory with ReAct agents for context-aware conversations, and
- configure DashScope embedding models and Qdrant vector store for memory management.

## Prerequisites

- Python 3.10 or higher
- DashScope API key from Alibaba Cloud


## QuickStart

Install agentscope and ensure you have a valid DashScope API key in your environment variables.

> Note: The example is built with DashScope chat model and embedding model. If you want to use OpenAI models instead,
> modify the model initialization in the example code accordingly.

```bash
# Install agentscope from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .
# Install dependencies
pip install mem0ai
```

Set up your API key:

```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
```

Run the example:

```bash
python memory_example.py
```

The example will:
1. Initialize a Mem0LongTermMemory instance with DashScope models and Qdrant vector store
2. Record a basic conversation to long-term memory
3. Retrieve memories using semantic search
4. Demonstrate ReAct agent integration with long-term memory for storing and retrieving user preferences

## Key Features

- **Vector-based Storage**: Uses Qdrant vector database for efficient semantic search and retrieval
- **Flexible Configuration**: Support for multiple embedding models (OpenAI, DashScope) and vector stores
- **Async Operations**: Full async support for non-blocking memory operations
- **ReAct Agent Integration**: Seamless integration with AgentScope's ReActAgent and tool system

## Basic Usage

### Initialize Memory

```python
import os
from agentscope.memory import Mem0LongTermMemory
from agentscope.model import DashScopeChatModel
from agentscope.embedding import DashScopeTextEmbedding
from mem0.vector_stores.configs import VectorStoreConfig

# Initialize with DashScope models and Qdrant vector store
long_term_memory = Mem0LongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    model=DashScopeChatModel(
        model_name="qwen-max-latest",
        api_key=os.environ.get("DASHSCOPE_API_KEY")
    ),
    embedding_model=DashScopeTextEmbedding(
        model_name="text-embedding-v3",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        dimensions=1024
    ),
    vector_store_config=VectorStoreConfig(
        provider="qdrant",
        config={
            "on_disk": True,
            "path": "./qdrant_data",  # Your customized storage path
            "embedding_model_dims": 1024
        }
    )
)
```

> **Important**: If you change to a different embedding model or modify `embedding_model_dims`, you must either set a new storage path or delete the existing database files. Otherwise, a dimension mismatch error will occur.

### Integrate with ReAct Agent

```python
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit

# Create a ReAct agent with long-term memory
toolkit = Toolkit()
agent = ReActAgent(
    name="Friday",
    sys_prompt=(
        "You are a helpful assistant named Friday. "
        "If you think there is relevant information about "
        "the user's preferences, you can record it to long-term "
        "memory using the tool `record_to_memory`. "
        "If you need to retrieve information from long-term "
        "memory, use the tool `retrieve_from_memory`."
    ),
    model=DashScopeChatModel(
        model_name="qwen-max-latest",
        api_key=os.environ.get("DASHSCOPE_API_KEY")
    ),
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
    long_term_memory=long_term_memory,
    long_term_memory_mode="both"
)

# Use the agent
msg = Msg(
    role="user",
    content="When I travel to Hangzhou, I prefer to stay in a homestay",
    name="user"
)
response = await agent(msg)
```

## Advanced Configuration

You can customize the mem0 config by directly set :

```python
long_term_memory = Mem0LongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    mem0_config=your_mem0_config  # Pass your custom mem0 configuration
)
```

For more configuration options, refer to the [mem0 documentation](https://github.com/mem0ai/mem0).

## What's in the Example

The `memory_example.py` file demonstrates:

1. **Basic Memory Recording**: Recording user conversations to long-term memory
2. **Memory Retrieval**: Searching for stored memories using semantic similarity
3. **ReAct Agent Integration**: Using long-term memory with ReAct agents to store and retrieve user preferences automatically

## Reference

- [mem0 Documentation](https://github.com/mem0ai/mem0)
- [Qdrant Vector Database](https://qdrant.tech/)