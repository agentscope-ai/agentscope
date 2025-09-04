# Deep Research Agent Example

## What This Example Demonstrates

This example shows a **DeepResearch Agent** implementation using the AgentScope framework. The DeepResearch Agent specializes in performing multi-step research to collect and integrate information from multiple sources, and generates comprehensive reports to solve complex tasks.
## Prerequisites

- Python 3.10 or higher
- Node.js and npm (for the MCP server)
- DashScope API key from [Alibaba Cloud](https://dashscope.console.aliyun.com/)
- Tavily search API key from [Tavily](https://www.tavily.com/)

## How to Run This Example
1. **Set Environment Variable**:
   ```bash
   export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
   export TAVILY_API_KEY="your_tavily_api_key_here"
   export AGENT_OPERATION_DIR="your_own_direction_here"
   ```
2. **Test Tavily MCP Server**:
    ```bash
    npx -y tavily-mcp@latest
    ```

2. **Run the script**:
    ```bash
   python main.py
   ```

## Connect to Web Search MCP client
The DeepResearch Agent only supports web search through the Tavily MCP client currently. To use this feature, you need to start the MCP server locally and establish a connection to it.
```
from agentscope.mcp import StdIOStatefulClient

tavily_search_client= StdIOStatefulClient(
    name="tavily_mcp",
    command="npx",
    args=["-y", "tavily-mcp@latest"],
    env={"TAVILY_API_KEY": os.getenv("TAVILY_API_KEY", "")},
)
await tavily_search_client.connect()
```

## Replace the Model and Formatter
AgentScope provides several built-in model implementations that you can easily switch between. You can also implement your own custom model if needed.

For example, to use the OpenAI model, you can replace the model and formatter as follows:
```
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

# Replace the model and formatter with:
agent = DeepResearchAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant named Friday.",
    model=OpenAIChatModel(
        api_key=os.environ.get("OPENAI_API_KEY"),
        model_name="gpt-4",
        stream=True,
    ),
    formatter=OpenAIChatFormatter(),  # Use matching formatter
    memory=InMemoryMemory(),
    search_mcp_client=tavily_search_client,
    tmp_file_storage_dir=agent_working_dir,
)
```

## Notes

⚠️ Formatter Compatibility: Always use the formatter that matches your model. Mismatched formatters can cause communication errors between the agent and the model.

🔧 Tool Usage: Ensure your chosen model supports function calling capabilities to maintain full ReAct functionality with tool usage.

🏠 Local Models: For local models, ensure the model service (like Ollama) is running before starting the agent.