# MemoryWithCompress

## Overview

MemoryWithCompress is a memory management system designed for AgentScope's `ReActAgent`. It automatically compresses conversation history when the memory size exceeds a specified token limit, using a Large Language Model (LLM) to create concise summaries that preserve key information. This allows agents to maintain context over long conversations while staying within token constraints.

The system maintains two separate storage mechanisms:
- **`_chat_history`**: Stores the complete, unmodified conversation history
- **`_memory`**: Stores messages that may be compressed when token limits are exceeded

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

## File Structure

```
react_memory/
├── README.md                   # This documentation file
├── main_mc.py                  # Example demonstrating MemoryWithCompress usage
├── _memory_with_compress.py    # Core MemoryWithCompress implementation
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

You can easily switch to other models by modifying the configuration in `main_mc.py`.

## How It Works

### 1. Memory Addition Flow
1. **Message Input**: New messages are added via the `add()` method
2. **Dual Storage**: Messages are deep-copied and added to both `_chat_history` and `_memory`
3. **No Immediate Compression**: Messages are stored as-is until retrieval

### 2. Memory Retrieval and Compression Flow
When `get_memory()` is called:
1. **Token Counting**: The system calculates the total token count of all messages in `_memory`
2. **Compression Check**: If the token count exceeds `max_token`, compression is triggered
3. **LLM Compression**: All messages in `_memory` are sent to the LLM with a compression prompt
4. **Structured Output**: The LLM returns a structured response containing the compressed summary
5. **Memory Replacement**: The entire `_memory` list is replaced with a single compressed message
6. **Filtering & Selection**: Optional filtering and recent_n selection are applied
7. **Return**: The processed memory is returned

### 3. Compression Process
The compression uses a structured output approach:
- **Prompt**: Instructs the LLM to summarize conversation history while preserving key information
- **Schema**: Uses `MemoryCompressionSchema` (Pydantic model) to ensure consistent output format
- **Output Format**: Returns a message with content wrapped in `<compressed_memory>` tags

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
from examples.react_memory._memory_with_compress import MemoryWithCompress

# 1. Create the model for agent and memory compression
model = DashScopeChatModel(
    api_key=os.environ.get("DASHSCOPE_API_KEY"),
    model_name="qwen-max",
    stream=False,
)

# 2. Initialize MemoryWithCompress
memory_with_compress = MemoryWithCompress(
    model=model,
    formatter=DashScopeChatFormatter(),
    max_token=300,  # Compress when memory exceeds 300 tokens
    token_counter=OpenAITokenCounter(model_name="qwen-max"),
)

# 3. Initialize ReActAgent with the memory instance
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
async def custom_compress(messages: List[Msg]) -> Msg:
    # Your custom compression logic
    compressed_content = "..."
    return Msg("assistant", compressed_content, "assistant")

memory_with_compress = MemoryWithCompress(
    model=model,
    formatter=formatter,
    max_token=300,
    compress_func=custom_compress,
)
```

## API Reference

### MemoryWithCompress Class

#### `__init__(...)`
Initializes the memory system. Key parameters include:

- `model` (ChatModelBase): The LLM model to use for compression
- `formatter` (FormatterBase): The formatter to use for formatting messages
- `max_token` (int): The maximum token count for `_memory`. Default: 28000. Compression is triggered when exceeded
- `token_counter` (Optional[TokenCounterBase]): The token counter for counting tokens. Default: `OpenAITokenCounter`
- `compress_func` (Callable[[List[Msg]], Msg] | None): Custom compression function. If None, uses the default `_compress_memory` method

#### Main Methods

**`add(msgs: Union[Sequence[Msg], Msg, None])`**
- Adds new messages to both `_chat_history` and `_memory`
- Messages are deep-copied to avoid modifying originals
- Raises `TypeError` if non-Msg objects are provided

**`get_memory(recent_n=None, filter_func=None)`**
- Retrieves memory content, automatically compressing if token limit is exceeded
- Parameters:
  - `recent_n` (Optional[int]): Return only the most recent N messages
  - `filter_func` (Optional[Callable[[int, Msg], bool]]): Custom filter function that takes (index, message) and returns bool
- Returns: `list[Msg]` - The memory content (potentially compressed)

**`delete(index: Union[Iterable, int])`**
- Deletes memory fragments from both `_chat_history` and `_memory`
- Indices are sorted in descending order to avoid index shifting issues

**`size() -> int`**
- Returns the number of messages in `_chat_history`

**`clear()`**
- Clears all memory from both `_chat_history` and `_memory`

**`state_dict() -> dict`**
- Returns a dictionary containing the serialized state:
  - `chat_history`: List of message dictionaries
  - `memory`: List of message dictionaries
  - `max_token`: The max_token setting

**`load_state_dict(state_dict: dict, strict: bool = True)`**
- Loads memory state from a dictionary
- Restores `_chat_history`, `_memory`, and `max_token` settings

**`retrieve(*args, **kwargs)`**
- Not implemented. Use `get_memory()` instead.
- Raises `NotImplementedError`

## Internal Methods

**`_compress_memory() -> Msg`**
- Internal method that compresses all messages in `_memory` using the LLM
- Uses structured output with `MemoryCompressionSchema`
- Returns a single `Msg` object containing the compressed summary

## Utility Functions

The `_mc_utils.py` module provides:

- **`format_msgs(msgs)`**: Formats a list of `Msg` objects into a list of dictionaries
- **`count_words(token_counter, text)`**: Counts tokens in text (supports both string and list[dict] formats)
- **`MemoryCompressionSchema`**: Pydantic model for structured compression output

## Best Practices

- **Token Limit Selection**: Choose `max_token` based on your model's context window and typical conversation length
- **Compression Timing**: Compression happens during `get_memory()` calls, so be aware of potential latency
- **State Persistence**: Use `state_dict()` and `load_state_dict()` to save/restore conversation state between sessions
- **Custom Compression**: For domain-specific compression needs, implement a custom `compress_func`

## Troubleshooting

- **Compression Not Triggering**: Check that `max_token` is set appropriately and that `get_memory()` is being called
- **Structured Output Errors**: Ensure your model supports structured output (e.g., DashScope models with `structured_model` parameter)
- **Token Counting Issues**: Verify that your `token_counter` is compatible with your model and correctly configured

## Reference

- [AgentScope Documentation](https://github.com/agentscope-ai/agentscope)
- [Pydantic Documentation](https://docs.pydantic.dev/)
