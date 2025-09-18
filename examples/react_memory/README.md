# ReActMemory

## Overview

ReActMemory is an advanced memory management system designed for AgentScope's `ReActAgent`. It enables intelligent, persistent memory storage and retrieval by leveraging a Large Language Model (LLM) to process and understand conversational context. The system analyzes chat messages, extracts meaningful information, and stores it in a vector database for efficient semantic retrieval. This allows agents to maintain long-term context, learn from interactions, and handle complex, multi-step tasks.

This example demonstrates how to integrate `ReActMemory` with a `ReActAgent` to build a powerful assistant capable of sophisticated reasoning and memory management.

## Core Features

### Intelligent Memory Processing
- **LLM-Powered Analysis**: Uses an LLM to analyze new chat messages in the context of existing memories, deciding whether to add new information or update existing records.
- **Automated Long-Context Handling**: Automatically processes and compresses long tool results and chat histories to prevent exceeding token limits, including saving original content to the file system.
- **File System Integration**: Stores and retrieves original long-form content from the file system, which can be accessed by the agent via the `retrieve_from_memory` tool.

### Persistent & Semantic Storage
- **Vector-based Storage**: Employs a vector database (Qdrant by default) for efficient semantic search, allowing the agent to retrieve memories based on meaning rather than just keywords.
- **Configurable Backends**: Supports custom vector store implementations through a base class, allowing for flexibility with different database backends.

### Flexible Memory Management
- **Multiple Retrieval Modes**: Offers different strategies for memory retrieval:
    - `source`: Retrieves raw, unprocessed chat history.
    - `processed`: Retrieves LLM-refined and structured memories.
    - `auto`: Automatically switches between `source` and `processed` based on context length.
- **Direct Memory Control**: Provides methods for direct, granular manipulation of memory records, bypassing the default LLM-based processing when needed.

## File Structure

```
react_memory/
├── README.md                   # This documentation file
├── main.py                     # Example of how to use ReActMemory with a ReActAgent
├── _react_memory.py            # Core ReActMemory implementation
├── _mem_record.py              # Data structure for memory records (MemRecord)
├── config/
│   └── prompts.py              # Prompts used for memory processing and summarization
└── vector_factories/
    ├── base.py                 # Base class for vector store implementations
    └── qdrant.py               # Qdrant vector store implementation
```

## Prerequisites

### Clone the AgentScope Repository
This example depends on AgentScope. Please clone the full repository to your local machine.

### Install Dependencies
**Recommended**: Python 3.10+

Install the required dependencies:
```bash
pip install langchain qdrant-client transformers
```

### API Keys
This example uses DashScope APIs for both chat and embedding models by default. You need to set your API key as an environment variable:
```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
```
You can easily switch to other models by modifying the configuration in `main.py`.

## How It Works

### 1. Memory Processing Flow
1.  **Message Input**: The agent receives a new message from the user or a tool.
2.  **Chat History Storage**: The raw message is appended to a temporary `_chat_history` list.
3.  **Long-Context Pre-processing**: If a new message is too long, it is compressed and the original content is stored in file system before further processing.
4.  **Intelligent Processing (if enabled)**: If `process_w_llm` is `True`, `ReActMemory` triggers its LLM-based processing logic:
    a.  **Semantic Retrieval**: It searches the vector store for existing memories that are semantically related to the new message.
    b.  **LLM Analysis**: It sends the new message and retrieved memories to an LLM using the `update_memory_prompt`.
    c.  **Action Generation**: The LLM returns a list of actions: `ADD` a new memory or `UPDATE` an existing one.
5.  **Memory Update**: `ReActMemory` executes the generated actions. If `process_w_llm` is `False`, new messages are added directly to the memory.

### 2. Retrieval Flow
When the agent needs to access its memory, `get_memory()` is called.
- **`source` mode**: Returns the raw, unprocessed chat history.
- **`processed` mode**: Returns the structured, LLM-refined memories.
- **`auto` mode**: If the chat history exceeds `max_chat_len`, it returns the `processed` memory; otherwise, it returns the `source` history.

### 3. Automatic Summarization
If the total length of the `_memory` list exceeds `max_memory_len`, `ReActMemory` automatically calls `summarize_global()` to condense older entries, preserving key information while managing context size.

## Usage Examples

### Running the Example
To see `ReActMemory` in action, run the example script:
```bash
python ./main.py
```

### Basic Initialization
Here is a snippet from `main.py` showing how to set up the agent and memory:
```python
# 1. Set up the vector store
vector_store = Qdrant(
    collection_name="react_memory",
    embedding_model_dims=1024,
    path="/tmp/vector_store",
)

# 2. Initialize ReActMemory with configuration
react_memory = ReActMemory(
    model_config_name="qwen-max",
    embedding_model="text-embedding-v4",
    vector_store=vector_store,
    retrieve_type="processed",
    update_memory_prompt=UPDATE_MEMORY_PROMPT_DEFAULT,
    summary_working_log_prompt=SUMMARIZE_WORKING_LOG_PROMPT_v2,
    summary_working_log_w_query_prompt=SUMMARIZE_WORKING_LOG_PROMPT_W_QUERY,
    process_w_llm=True,
)

# 3. Initialize ReActAgent with the memory instance
agent = ReActAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant named Friday.",
    model=DashScopeChatModel(
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        model_name="qwen-max",
    ),
    memory=react_memory,
)
```

## API Reference

### ReActMemory Class

#### `__init__(...)`
Initializes the memory system. Key parameters include:
- `model_config_name` (str): The model name for memory processing.
- `embedding_model` (str | Callable): The embedding model for vectorization.
- `emb_tokenizer_model` (str): The tokenizer for calculating embedding text length.
- `chat_tokenizer_model` (str): The tokenizer for calculating chat message length.
- `vector_store` (VectorStoreBase): An instance of a vector database implementation.
- `retrieve_type` (str): The default retrieval mode (`"source"`, `"processed"`, or `"auto"`).
- `max_chat_len` (int): The token threshold for switching from `source` to `processed` in `auto` mode.
- `max_memory_len` (int): The token threshold for triggering global memory summarization.
- `update_memory_prompt` (str): The prompt template for the LLM to decide memory actions.
- `summary_working_log_prompt` (str): The prompt for summarizing memory chunks.
- `summary_working_log_w_query_prompt` (str): The prompt for summarizing memory with a specific query.
- `global_update_allowed` (bool): Whether to allow the LLM to update non-recent memories.
- `process_w_llm` (bool): Whether to enable the intelligent memory processing flow.
- `mount_dir` (str): The directory to store long-context files.
- `compressed_ratio` (float): The ratio of memory to compress during global summarization.

#### Main Methods

**`add(msgs: Union[Sequence[Msg], Msg, None])`**
- The primary method for adding new information to the memory. It triggers the full processing pipeline.

**`get_memory(recent_n=None, retrieve_type=None, ...)`**
- Retrieves memories based on the specified `retrieve_type`.

**`retrieve_from_vector_store(queries: Sequence, top_k=5)`**
- Performs a direct semantic search on the vector store.

**`retrieve_from_memory(query: str, filename: Optional[str] = None)`**
- Searches memory content based on a query. Can be registered as a tool for an agent. If `filename` is provided, it searches within that specific file.

**`summarize_global()`**
- Automatically compresses the memory by summarizing older entries when `max_memory_len` is exceeded.

## Customization & Extension

### Replacing the Vector Store
You can replace the default Qdrant implementation with any other vector database.
1.  Create a new class that inherits from `vector_factories.base.VectorStoreBase`.
2.  Implement the required abstract methods (`insert`, `search`, `delete`, etc.).
3.  Instantiate your custom class and pass it to the `ReActMemory` constructor.

### Customizing Prompts
The intelligence of the memory system is heavily influenced by its prompts. You can customize them by passing your own prompt strings to the `__init__` method. The default prompts are located in `config/prompts.py`.

## Best Practices

- **Prompt Engineering**: The quality of the `update_memory_prompt` is critical for effective memory management.
- **Choosing a Retrieval Strategy**: Use `processed` for high-level understanding, `source` for verbatim history, and `auto` for a general balance.
- **Performance Tuning**: LLM-based processing adds latency. For speed-critical applications, set `process_w_llm=False` or rely on the `source` retrieval mode.

## Troubleshooting

- **Poor Memory Quality**: If the agent's memory seems irrelevant, inspect and refine the `update_memory_prompt`.
- **Vector Store Errors**: Ensure your vector store is properly configured and running. Check connection parameters and collection names.

## Reference

- [AgentScope Documentation](https://github.com/agentscope-ai/agentscope)
- [Qdrant Vector Database](https://qdrant.tech/)
