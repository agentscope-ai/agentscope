 # Mem0LongTermMemory

## Overview

**Note**: We are working on merging Mem0LongTermMemory into the main AgentScope repository.

Mem0LongTermMemory is a long-term memory implementation built on top of the mem0 library, designed to provide persistent, semantic memory storage for AgentScope agents. It enables agents to record, store, and retrieve conversation history, reasoning processes, and contextual information across sessions, supporting advanced memory management and knowledge retention.

This example demonstrates how to use Mem0LongTermMemory to create persistent memory systems that can store and retrieve information based on semantic similarity, enabling agents to maintain context and learn from past interactions.

## Core Features

### Persistent Memory Storage
- **Vector-based Storage**: Uses Qdrant vector database for efficient semantic search and retrieval
- **Configurable Backends**: Support for multiple embedding models (OpenAI, DashScope) and vector stores
- **Async Operations**: Full async support for non-blocking memory operations

### Semantic Memory Management
- **Content Recording**: Store conversation messages, tool usage, and reasoning processes
- **Thinking Integration**: Record agent thinking processes alongside content for better context
- **Flexible Input Formats**: Support for strings, Msg objects, and dictionaries

### Agent Integration
- **Direct AgentScope Integration**: Seamless integration with AgentScope's ReActAgent
- **Memory Modes**: Support for agent_control, dev_control, and both modes
- **Tool Response Format**: Returns structured ToolResponse objects for easy integration

## File Structure

```
memory_by_mem0/
├── README.md                           # This documentation file
├── long_term_memory_by_mem0.py         # Core Mem0LongTermMemory implementation
├── memory_example.py                   # Standalone examples demonstrating memory operations
├── conversation_agent_with_longterm_mem.py  # Interactive conversation example with ReActAgent
└── utils.py                           # AgentScope integration utilities for mem0
```

## Prerequisites

### Clone the AgentScope Repository
This example depends on AgentScope. Please clone the full repository.

### Install Dependencies
**Recommended**: Python 3.10+

Install the following dependencies:
```bash
pip install mem0ai
```

### API Keys
By default, the example uses DashScope/OpenAI for embedding and LLM. Set your API key:

```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
export DASHSCOPE_API_BASE_URL='YOUR_API_BASE_URL'
export DASHSCOPE_MODEL_4_MEMORY='USED_MODEL_NAME'
export DASHSCOPE_EMBEDDING_MODEL='text-embedding-v2'
```

## How It Works

### 1. Configuration
The memory system uses a `MemoryConfig` that specifies:
- **Embedder**: Configuration for embedding models (OpenAI, DashScope)
- **LLM**: Configuration for language models used in memory processing
- **Vector Store**: Configuration for vector database (Qdrant with on-disk storage as default)

### 2. Memory Structure
- **Mem0LongTermMemory**: Inherits from `LongTermMemoryBase` and maintains an async memory server
- **Single AsyncMemory Instance**: Uses one AsyncMemory instance for all storage and retrieval operations
- **Agent/User Context**: Maintains separate memory spaces for different agent-user combinations

### 3. Memory Recording Flow
1. **Input Processing**: Formats various input types (strings, Msg objects, dictionaries) into standardized format
2. **Content Combination**: Merges thinking processes with content for comprehensive memory storage
3. **Vector Storage**: Stores processed content with metadata in the vector database
4. **Response Formatting**: Returns structured ToolResponse objects for easy integration

### 4. Memory Retrieval Flow
1. **Semantic Search**: Performs vector similarity search in the memory database
2. **Response Formatting**: Returns retrieved memories in structured format

## Usage Examples

### Basic Usage

Run the standalone memory examples to see the complete memory operations:

```bash
python ./memory_example.py
```


### Example Scenarios

The example demonstrates several typical use cases:

1. **Basic Conversation Recording**: Store simple user-agent conversations
2. **Tool Usage and Results**: Record tool usage with thinking processes
3. **Multi-step Reasoning**: Store complex reasoning processes step by step
4. **Error Handling**: Record error scenarios and recovery strategies
5. **User Preferences**: Store user preferences and contextual information

## API Reference

### Mem0LongTermMemory Class

#### Main Methods

**`__init__(agent_name=None, user_name=None, run_name=None, model=None, embedding_model=None, vector_store_config=None, mem0_config=None, default_memory_type=None, **kwargs)`**
- Initialize the memory instance with agent, user, and run context
- `agent_name` (str, optional): The name of the agent
- `user_name` (str, optional): The name of the user
- `run_name` (str, optional): The name of the run/session
- `model` (ChatModelBase, optional): The model to use for the long-term memory
- `embedding_model` (EmbeddingModelBase, optional): The embedding model to use
- `vector_store_config` (VectorStoreConfig, optional): Vector store configuration
- `mem0_config` (MemoryConfig, optional): Complete mem0 configuration
- `default_memory_type` (str, optional): Default memory type for storage

**Note**:
1. At least one of `agent_name`, `user_name`, or `run_name` is required.
2. During memory recording, these parameters become metadata for the stored memories.
3. During memory retrieval, only memories with matching metadata values will be returned.

**`record_to_memory(thinking, content, memory_type=None, **kwargs)`**
- Record content with thinking process
- `thinking` (str): Your thinking and reasoning about what to record
- `content` (list[str]): The content to remember, which is a list of strings
- `memory_type` (str, optional): The type of memory to use. Default is None, to create a semantic memory. "procedural_memory" is explicitly used for procedural memories
- Returns: ToolResponse with success/error status

**`retrieve_from_memory(keywords, **kwargs)`**
- Retrieve memories based on keywords
- `keywords` (list[str]): Keywords to search for in the memory, which should be specific and concise, e.g. the person's name, the date, the location, etc.
- `limit_per_search` (int): Number of memories to retrieve per search (default: 5)
- Returns: ToolResponse with retrieved memories

#### Internal Methods

**`record(msgs, **kwargs)`**
- Record message sequences to memory
- `msgs` (Sequence[Msg | None]): Messages to record

**`_record_all(content, thinking=None, memory_type=None, infer=True, **kwargs)`**
- Record content with comprehensive processing
- `content` (list[str] | list[Msg] | list[dict]): The content to remember, which is a list of strings or Msg objects or dict objects
- `thinking` (str, optional): Your thinking and reasoning about what to record, if not provided, the content will be used as the thinking
- `memory_type` (str, optional): The type of memory to use. Default is None, to create a semantic memory. "procedural_memory" is explicitly used for procedural memories
- `infer` (bool): Whether to infer memory type (default: True)
- Handles various input formats and thinking integration

**`retrieve(msg, **kwargs)`**
- Retrieve memories based on message content
- `msg` (Msg | list[Msg] | None): The message to search for in the memory, which should be specific and concise, e.g. the person's name, the date, the location, etc.
- `limit_per_search` (int): Number of results per search (default: 5)
- Returns: list[str] - A list of retrieved memory strings

### Configuration


#### Direct Model Configuration
```python
# Initialize with AgentScope models directly
long_term_memory = Mem0LongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    model=OpenAIChatModel(
        model_name="gpt-4",
        api_key="your_api_key",
        base_url="your_base_url"
    ),
    embedding_model=OpenAITextEmbedding(
        model_name="text-embedding-3-small",
        api_key="your_api_key",
        base_url="your_base_url"
    )
)
```



## Customization & Extension

### Backend Replacement
Easily customize embedding, LLM, or vector store by modifying the configuration:

```python
# Example: Using different embedding model
embedder=EmbedderConfig(
    provider="dashscope",
    config={
        "model": "text-embedding-v1",
        "api_key": "your_dashscope_key"
    }
)
```

### Memory Config Replacement
Mem0LongTermMemory supports directly receiving memory configurations defined in mem0, allowing users to easily adopt various memory configurations and backends supported by mem0. This provides flexibility to use different embedding models, LLMs, and vector stores without modifying the core implementation.

```python
# Example: Using a complete mem0 MemoryConfig
from mem0.configs.base import MemoryConfig
from mem0.embeddings.configs import EmbedderConfig
from mem0.llms.configs import LlmConfig
from mem0.vector_stores.configs import VectorStoreConfig

# Create a custom mem0 configuration
mem0_config = MemoryConfig(
    embedder=EmbedderConfig(
        provider="openai",
        config={
            "model": "text-embedding-3-small",
            "api_key": "your_openai_key"
        }
    ),
    llm=LlmConfig(
        provider="openai",
        config={
            "model": "gpt-4",
            "api_key": "your_openai_key"
        }
    ),
    vector_store=VectorStoreConfig(
        provider="qdrant",
        config={
            "on_disk": True,
            "path": "./memory_data"
        }
    )
)

# Initialize with the custom mem0 configuration
long_term_memory = Mem0LongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    mem0_config=mem0_config
)
```

**Note**: In Mem0LongTermMemory, if the `model`, `embedding_model`, or `vector_store_config` parameters are not None, they will override the corresponding configurations in `mem0_config`. This allows for flexible configuration where you can use a base mem0 configuration and selectively override specific components.

### AgentScope Integration
The implementation includes custom AgentScope providers for mem0:

- **AgentScopeLLM**: Integrates AgentScope ChatModelBase with mem0
- **AgentScopeEmbedding**: Integrates AgentScope EmbeddingModelBase with mem0

These providers handle the conversion between mem0's expected format and AgentScope's message/response formats.

### Memory Type Customization
Add custom memory types for different use cases:

```python
# Example: Procedural memory
await memory.record_to_memory(
    content=["Step 1: Analyze input", "Step 2: Process data"],
    thinking="This is a procedural workflow for data processing",
    memory_type="procedural_memory"
)
```


## Best Practices

### Memory Recording
1. **Be Specific**: Record specific, actionable information rather than general statements
2. **Include Context**: Always include relevant context and reasoning when recording
3. **Use Thinking**: Leverage the thinking parameter to explain why information is important
4. **Structured Content**: Use structured formats for complex information

### Memory Retrieval
1. **Specific Keywords**: Use specific, relevant keywords for better search results
2. **Appropriate Limits**: Set reasonable limits based on your use case
3. **Context Awareness**: Consider the current context when retrieving memories
4. **Error Handling**: Always handle potential retrieval errors gracefully

### Performance Optimization
1. **Batch Operations**: Group related memory operations when possible
2. **Efficient Queries**: Use specific keywords to reduce search scope
3. **Memory Cleanup**: Periodically clean up irrelevant or outdated memories
4. **Configuration Tuning**: Optimize vector store and embedding configurations

## Troubleshooting

### Common Issues

**Memory Not Found**
- Check if the memory was properly recorded
- Verify agent_id and user_id consistency
- Ensure vector store is properly configured

**Poor Search Results**
- Use more specific keywords
- Check embedding model configuration
- Verify content was properly formatted during recording

**Performance Issues**
- Optimize vector store configuration
- Reduce search limits
- Consider using on-disk storage for large datasets

**AgentScope Integration Issues**
- Ensure AgentScope models are properly configured
- Check that the custom providers are registered correctly
- Verify message format compatibility

### Debug Mode
Enable debug logging to troubleshoot issues:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Reference

- [mem0 Documentation](https://github.com/mem0ai/mem0)
- [AgentScope Documentation](https://github.com/agentscope-ai/agentscope)
- [Qdrant Vector Database](https://qdrant.tech/)

For further customization or integration, please refer to the full implementation in the `long_term_memory_by_mem0.py` file and the mem0 official documentation.