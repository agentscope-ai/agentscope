# ReMe Long-Term Memory Examples

This directory contains examples demonstrating how to use the `RemeLongTermMemory` class for persistent memory storage and retrieval in AgentScope.

## Overview

`RemeLongTermMemory` integrates with the ReMe library to provide:
- Personal memory storage for agents
- Semantic search and retrieval
- Multi-provider support for both LLM and embedding models

## Prerequisites

### Install Dependencies

```bash
pip install reme-ai python-dotenv
```

### Set Up API Keys

Create a `.env` file in the project root with your API keys:

```bash
# For DashScope
DASHSCOPE_API_KEY=your_dashscope_api_key

# For OpenAI
OPENAI_API_KEY=your_openai_api_key
```

For Ollama, you don't need API keys, but you need to have Ollama running locally:

```bash
# Install Ollama from https://ollama.ai
# Then pull the required models:
ollama pull qwen2.5:latest
ollama pull bge-m3:latest
```

## Examples

### 1. Basic Example (`memory_example.py`)

Demonstrates basic usage with DashScope models:
- Recording conversations to long-term memory
- Retrieving memories based on queries
- Using long-term memory with ReActAgent

Run:
```bash
python memory_example.py
```

### 2. Multi-Provider Example (`memory_example_multi_providers.py`)

Shows how to use different embedding model providers:

#### DashScope Embedding
```python
embedding_model = DashScopeTextEmbedding(
    model_name="text-embedding-v3",
    api_key=os.environ.get("DASHSCOPE_API_KEY"),
    dimensions=1024,
)
```

#### OpenAI Embedding
```python
embedding_model = OpenAITextEmbedding(
    model_name="text-embedding-3-small",
    api_key=os.environ.get("OPENAI_API_KEY"),
    dimensions=1024,
)
```

#### Ollama Embedding
```python
embedding_model = OllamaTextEmbedding(
    model_name="bge-m3:latest",
    dimensions=1024,
    host="http://localhost:11434",
)
```

Run:
```bash
python memory_example_multi_providers.py
```

## Supported Model Combinations

### LLM Models
- `DashScopeChatModel`
- `OpenAIChatModel`
- `OllamaChatModel`

### Embedding Models
- `DashScopeTextEmbedding`
- `OpenAITextEmbedding`
- `OllamaTextEmbedding`

You can mix and match any LLM with any embedding model. For example:
- OpenAI LLM + DashScope Embedding
- Ollama LLM + OpenAI Embedding
- DashScope LLM + Ollama Embedding

## Key Features

### Memory Recording

```python
await long_term_memory.record(
    msgs=[
        Msg(
            role="user",
            content="I prefer coffee over tea",
            name="user",
        ),
    ],
)
```

### Memory Retrieval

```python
result = await long_term_memory.retrieve(
    msg=[
        Msg(
            role="user",
            content="What's my beverage preference?",
            name="user",
        ),
    ],
    limit=5,  # Number of memories to retrieve
)
```

### Tool-Based Recording (with ReActAgent)

```python
toolkit = Toolkit()
agent = ReActAgent(
    name="Friday",
    model=model,
    formatter=formatter,
    toolkit=toolkit,
    memory=InMemoryMemory(),
    long_term_memory=long_term_memory,
    long_term_memory_mode="both",  # Enable both record and retrieve
)
```

## Memory Isolation

The `user_name` parameter is used as `workspace_id` for memory isolation:
- Different users maintain separate memory spaces
- Ensures privacy and data separation

```python
long_term_memory = RemeLongTermMemory(
    agent_name="Friday",
    user_name="user_123",  # Unique identifier for this user
    model=model,
    embedding_model=embedding_model,
)
```

## Error Handling

The examples include basic error handling. In production, you should:
1. Validate API keys before initialization
2. Handle network failures gracefully
3. Implement retry logic for transient errors
4. Monitor memory usage and performance

## Performance Tips

1. **Batch Operations**: When recording multiple memories, batch them in a single call
2. **Caching**: Use embedding cache to avoid repeated API calls
3. **Limit Results**: Set appropriate `limit` values when retrieving memories
4. **Workspace Management**: Use distinct `user_name` values for different users

## Troubleshooting

### "ReMeApp context not started" Error
Make sure to use `async with` context manager:
```python
async with RemeLongTermMemory(...) as long_term_memory:
    # Your code here
```

### Ollama Connection Error
Ensure Ollama is running:
```bash
ollama serve
```

### API Key Issues
Verify your API keys are correctly set in the `.env` file and loaded properly.

## Additional Resources

- [ReMe Documentation](https://github.com/modelscope/agentscope/tree/main/examples/functionality/long_term_memory)
- [AgentScope Documentation](https://modelscope.github.io/agentscope/)
- [DashScope API](https://help.aliyun.com/zh/dashscope/)
- [OpenAI Embeddings](https://platform.openai.com/docs/guides/embeddings)
- [Ollama](https://ollama.ai)

