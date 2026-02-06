# MemoryWithCompress

- [ ] TODO: The memory module with compression will be added to the agentscope library in the future.

## Overview

MemoryWithCompress is a memory management system designed for AgentScope's `ReActAgent`. It automatically compresses conversation history when the memory size exceeds a specified token limit, using a Large Language Model (LLM) to create concise summaries that preserve key information. This allows agents to maintain context over long conversations while staying within token constraints.

The system maintains two separate storage mechanisms:
- **`chat_history_storage`**: Stores the complete, unmodified conversation history (uses `MessageStorageBase` interface)
- **`memory_storage`**: Stores messages that may be compressed when token limits are exceeded (uses `MessageStorageBase` interface)

Both storage mechanisms are abstracted through the `MessageStorageBase` interface, allowing for flexible storage backends. By default, `InMemoryMessageStorage` is used for both.

## Core Features

### Automatic Memory Compression
- **Token-based Triggering**: Automatically compresses memory when the total token count exceeds `max_token`
- **LLM-Powered Summarization**: Uses an LLM to intelligently compress conversation history while preserving essential information
- **Structured Output**: Uses Pydantic schemas to ensure consistent compression format

### Dual Storage System
- **Complete History**: Maintains original, unmodified messages in `_chat_history` for reference
- **Compressed Memory**: Stores potentially compressed messages in `_memory` for efficient context management

### Flexible Memory Management
- **Filtering Support**: Provides `filter_func` parameter for custom memory filtering
- **Recent N Retrieval**: Supports retrieving only the most recent N messages
- **State Persistence**: Includes `state_dict()` and `load_state_dict()` methods for saving and loading memory state
- **Storage Abstraction**: Uses `MessageStorageBase` interface for flexible storage backends
- **Compression Triggers**: Supports both token-based and custom trigger functions for compression
- **Compression Timing Control**: Configurable compression on add (`compression_on_add`) and get (`compression_on_get`) operations

## File Structure

```
memory_with_compression/
├── README.md                   # This documentation file
├── main.py                     # Example demonstrating MemoryWithCompress usage
├── _memory_with_compress.py    # Core MemoryWithCompress implementation
├── _memory_storage.py          # Storage abstraction layer (MessageStorageBase, InMemoryMessageStorage)
├── _mc_utils.py                # Utility functions (formatting, token counting, compression schema)

```

## Prerequisites

### Clone the AgentScope Repository
This example depends on AgentScope. Please clone the full repository to your local machine.

### Install Dependencies
**Recommended**: Python 3.10+

Install the required dependencies:
```bash
pip install agentscope
```

### API Keys
This example uses DashScope APIs by default. You need to set your API key as an environment variable:
```bash
export DASHSCOPE_API_KEY='YOUR_API_KEY'
```

You can easily switch to other models by modifying the configuration in `main.py`.

## How It Works

### 1. Memory Addition Flow
1. **Message Input**: New messages are added via the async `add()` method
2. **Dual Storage**: Messages are deep-copied and added to both `chat_history_storage` and `memory_storage`
3. **Optional Compression on Add**: If `compression_on_add=True`, compression may be triggered immediately after adding messages

### 2. Memory Retrieval and Compression Flow
When `get_memory()` is called (if `compression_on_get=True`):
1. **Token Counting**: The system calculates the total token count of all messages in `memory_storage`
2. **Compression Check**:
   - First checks if token count exceeds `max_token` (automatic compression)
   - Then checks if `compression_trigger_func` returns `True` (custom trigger)
3. **LLM Compression**: If compression is needed, all messages in `memory_storage` are sent to the LLM with a compression prompt
4. **Structured Output**: The LLM returns a structured response containing the compressed summary
5. **Memory Replacement**: The entire `memory_storage` is updated with the compressed message(s)
6. **Filtering & Selection**: Optional filtering and recent_n selection are applied
7. **Return**: The processed memory is returned

### 3. Compression Process
The compression uses a structured output approach:
- **Prompt**: Instructs the LLM to summarize conversation history while preserving key information
- **Customizable Prompt**: Supports `customized_compression_prompt` parameter for custom prompt templates
- **Schema**: Uses `MemoryCompressionSchema` (Pydantic model) to ensure consistent output format
- **Output Format**: Returns a message with content wrapped in `<compressed_memory>` tags
- **Async Support**: All compression operations are asynchronous

## Usage Examples

### Running the Example
To see `MemoryWithCompress` in action, run the example script:
```bash
python ./main.py
```

### Basic Initialization
Here is a snippet from `main.py` showing how to set up the agent and memory:

```python
from agentscope.agent import ReActAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.token import OpenAITokenCounter
from agentscope.message import Msg
from _memory_with_compress import MemoryWithCompress

# 1. Create the model for agent and memory compression
model = DashScopeChatModel(
    api_key=os.environ.get("DASHSCOPE_API_KEY"),
    model_name="qwen-max",
    stream=False,
)

# 2. Optional: Define a custom compression trigger function
async def trigger_compression(msgs: list[Msg]) -> bool:
    # Trigger compression if the number of messages exceeds 2
    # and the last message is from the assistant
    return len(msgs) > 2 and msgs[-1].role == "assistant"

# 3. Initialize MemoryWithCompress
memory_with_compress = MemoryWithCompress(
    model=model,
    formatter=DashScopeChatFormatter(),
    max_token=3000,  # Compress when memory exceeds 3000 tokens
    token_counter=OpenAITokenCounter(model_name="qwen-max"),
    compression_trigger_func=trigger_compression,  # Optional custom trigger
    compression_on_add=False,  # Don't compress on add (default)
    compression_on_get=True,   # Compress on get (default)
)

# 4. Initialize ReActAgent with the memory instance
agent = ReActAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant named Friday.",
    model=model,
    formatter=DashScopeChatFormatter(),
    memory=memory_with_compress,
)
```

### Custom Compression Function
You can provide a custom compression function:

```python
async def custom_compress(messages: List[Msg]) -> List[Msg]:
    # Your custom compression logic
    # Must return a List[Msg], not a single Msg
    compressed_content = "..."
    return [Msg("assistant", compressed_content, "assistant")]

memory_with_compress = MemoryWithCompress(
    model=model,
    formatter=formatter,
    max_token=300,
    compress_func=custom_compress,
)
```

### Custom Storage Backend
You can provide custom storage backends by implementing the `MessageStorageBase` interface:

```python
from _memory_storage import MessageStorageBase

class CustomStorage(MessageStorageBase):
    # Implement required methods: start, stop, health, add, delete, clear, get, replace, __aenter__, __aexit__
    ...

memory_with_compress = MemoryWithCompress(
    model=model,
    formatter=formatter,
    max_token=300,
    chat_history_storage=CustomStorage(),
    memory_storage=CustomStorage(),
)
```

## API Reference

### MemoryWithCompress Class

#### `__init__(...)`
Initializes the memory system. Key parameters include:

- `model` (ChatModelBase): The LLM model to use for compression
- `formatter` (FormatterBase): The formatter to use for formatting messages
- `max_token` (int): The maximum token count for `memory_storage`. Default: 28000. Compression is triggered when exceeded
- `chat_history_storage` (MessageStorageBase): Storage backend for complete chat history. Default: `InMemoryMessageStorage()`
- `memory_storage` (MessageStorageBase): Storage backend for compressed memory. Default: `InMemoryMessageStorage()`
- `token_counter` (Optional[TokenCounterBase]): The token counter for counting tokens. Default: None. If None, it will return the character count of the JSON string representation of messages (i.e., len(json.dumps(messages, ensure_ascii=False))).
- `compress_func` (Callable[[List[Msg]], Awaitable[List[Msg]]] | None): Custom compression function. Must be async and return `List[Msg]`. If None, uses the default `_compress_memory` method
- `compression_trigger_func` (Callable[[List[Msg]], Awaitable[bool]] | None): Optional function to trigger compression when token count is below `max_token`. Must be async and return `bool`. If None, compression only occurs when token count exceeds `max_token`
- `compression_on_add` (bool): Whether to check and compress memory when adding messages. Default: False
- `compression_on_get` (bool): Whether to check and compress memory when getting messages. Default: True
- `customized_compression_prompt` (str | None): Optional customized compression prompt template. Should include placeholders: `{max_token}`, `{messages_list_json}`, `{schema_json}`. Default: None (uses default template)

#### Main Methods

**`async add(msgs: Union[Sequence[Msg], Msg, None], compress_func=None, compression_trigger_func=None)`**
- Adds new messages to both `chat_history_storage` and `memory_storage`
- Messages are deep-copied to avoid modifying originals
- Raises `TypeError` if non-Msg objects are provided
- Parameters:
  - `msgs`: Messages to be added
  - `compress_func` (Optional): Override the instance-level compression function for this call
  - `compression_trigger_func` (Optional): Override the instance-level trigger function for this call
- If `compression_on_add=True`, may trigger compression after adding

**`async direct_update_memory(msgs: Union[Sequence[Msg], Msg, None])`**
- Directly updates the `memory_storage` with new messages (does not update `chat_history_storage`)
- Useful for replacing memory content directly

**`async get_memory(recent_n=None, filter_func=None, compress_func=None, compression_trigger_func=None)`**
- Retrieves memory content, automatically compressing if token limit is exceeded (if `compression_on_get=True`)
- Parameters:
  - `recent_n` (Optional[int]): Return only the most recent N messages
  - `filter_func` (Optional[Callable[[int, Msg], bool]]): Custom filter function that takes (index, message) and returns bool
  - `compress_func` (Optional): Override the instance-level compression function for this call
  - `compression_trigger_func` (Optional): Override the instance-level trigger function for this call
- Returns: `list[Msg]` - The memory content (potentially compressed)

**`async delete(indices: Union[Iterable[int], int])`**
- Deletes memory fragments from `memory_storage` (note: does not delete from `chat_history_storage`)
- Indices can be a single int or an iterable of ints

**`async size() -> int`**
- Returns the number of messages in `chat_history_storage`

**`async clear()`**
- Clears all memory from both `chat_history_storage` and `memory_storage`

**`state_dict() -> dict`**
- Returns a dictionary containing the serialized state:
  - `chat_history_storage`: List of message dictionaries from chat history
  - `memory_storage`: List of message dictionaries from memory
  - `max_token`: The max_token setting
- Note: This method handles async operations internally, so it can be called from both sync and async contexts

**`load_state_dict(state_dict: dict, strict: bool = True)`**
- Loads memory state from a dictionary
- Restores `chat_history_storage`, `memory_storage`, and `max_token` settings
- Note: This method handles async operations internally, so it can be called from both sync and async contexts

**`async retrieve(*args, **kwargs)`**
- Not implemented. Use `get_memory()` instead.
- Raises `NotImplementedError`

## Internal Methods

**`async _compress_memory(msgs: List[Msg]) -> List[Msg]`**
- Internal method that compresses messages using the LLM
- Uses structured output with `MemoryCompressionSchema`
- Returns a `List[Msg]` containing the compressed summary (typically a single message)
- Supports both streaming and non-streaming models

**`async _check_length_and_compress(compress_func=None) -> bool`**
- Checks if memory token count exceeds `max_token` and compresses if needed
- Returns `True` if compression was triggered, `False` otherwise

**`async check_and_compress(compress_func=None, compression_trigger_func=None, memory=None) -> tuple[bool, List[Msg]]`**
- Checks if compression is needed based on `compression_trigger_func`
- Returns a tuple: (was_compressed: bool, compressed_memory: List[Msg])
- If `memory` is provided, checks that instead of `memory_storage`

## Utility Functions

The `_mc_utils.py` module provides:

- **`format_msgs(msgs)`**: Formats a list of `Msg` objects into a list of dictionaries
- **`async count_words(token_counter, text)`**: Counts tokens in text (supports both string and list[dict] formats). Must be awaited.
- **`MemoryCompressionSchema`**: Pydantic model for structured compression output
- **`DEFAULT_COMPRESSION_PROMPT_TEMPLATE`**: Default prompt template for compression (includes placeholders: `{max_token}`, `{messages_list_json}`, `{schema_json}`)

## Storage Abstraction

The `_memory_storage.py` module provides:

- **`MessageStorageBase`**: Abstract base class for message storage backends
  - Required async methods: `start()`, `stop()`, `health()`, `add()`, `delete()`, `clear()`, `get()`, `replace()`, `__aenter__()`, `__aexit__()`
- **`InMemoryMessageStorage`**: Default in-memory implementation
  - Stores messages in a simple list
  - Suitable for most use cases

## Best Practices

- **Token Limit Selection**: Choose `max_token` based on your model's context window and typical conversation length
- **Compression Timing**:
  - Set `compression_on_get=True` (default) for compression during retrieval
  - Set `compression_on_add=False` (default) to avoid compression during add operations, as it may not complete before `get_memory()` is called
- **Async Operations**: All main methods are async, so use `await` when calling them
- **State Persistence**: Use `state_dict()` and `load_state_dict()` to save/restore conversation state between sessions
- **Custom Compression**: For domain-specific compression needs, implement a custom `compress_func` (must be async and return `List[Msg]`)
- **Compression Triggers**: Use `compression_trigger_func` for custom compression logic beyond token limits (e.g., compress after N messages, compress on specific conditions)
- **Storage Backends**: Implement custom `MessageStorageBase` subclasses for persistent storage (e.g., database, file system)

## Troubleshooting

- **Compression Not Triggering**:
  - Check that `compression_on_get=True` if you expect compression during retrieval
  - Verify that `max_token` is set appropriately
  - Ensure `get_memory()` is being called (and awaited)
  - If using `compression_trigger_func`, verify it returns `True` when compression should occur
- **Structured Output Errors**: Ensure your model supports structured output (e.g., DashScope models with `structured_model` parameter)
- **Token Counting Issues**: Verify that your `token_counter` is compatible with your model and correctly configured
- **Async/Await Errors**: Remember that most methods are async - use `await` when calling them
- **Storage Issues**: If using custom storage backends, ensure all required methods are properly implemented and async

## Reference

- [AgentScope Documentation](https://github.com/agentscope-ai/agentscope)
- [Pydantic Documentation](https://docs.pydantic.dev/)
