# Return Direct

This example demonstrates the `return_direct` feature for tool functions in AgentScope.

## What is Return Direct?

When a tool has `return_direct=True`, its output will be returned directly to the user without further LLM processing. The agent will stop the reasoning-acting loop immediately after the tool execution.

This is useful when:
- The tool output is already user-friendly and doesn't need LLM interpretation
- You want to reduce latency by skipping the LLM reasoning step
- The tool performs a final action that should end the conversation loop

## Usage

### Method 1: Set at Registration Time

```python
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

def get_weather(city: str) -> ToolResponse:
    """Get weather for a city."""
    return ToolResponse(
        content=[TextBlock(type="text", text=f"Weather in {city}: Sunny, 25°C")],
    )

toolkit = Toolkit()
toolkit.register_tool_function(
    get_weather,
    return_direct=True,  # Output returns directly to user
)
```

### Method 2: Set Dynamically in ToolResponse

```python
def search_database(query: str, return_immediately: bool = False) -> ToolResponse:
    """Search database with optional direct return."""
    results = f"Found results for '{query}'"
    return ToolResponse(
        content=[TextBlock(type="text", text=results)],
        return_direct=return_immediately,  # Dynamic control
    )
```

### Method 3: For MCP Clients

```python
await toolkit.register_mcp_client(
    mcp_client,
    return_direct=True,  # Apply to all tools from this client
)

# Or specify per-tool
await toolkit.register_mcp_client(
    mcp_client,
    return_direct={
        "tool_a": True,
        "tool_b": False,
    },
)
```

## How to Run

```bash
export DASHSCOPE_API_KEY="your_api_key"
python main.py
```

## Example Interaction

```
User: What's the weather in Beijing?
Assistant: Beijing: Sunny, 25°C, Humidity 40%
```

When `return_direct=True`, the weather result is returned directly without the LLM adding extra commentary.
