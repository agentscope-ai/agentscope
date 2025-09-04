# ReAct Agent Example

## What This Example Demonstrates

This example showcases a **ReAct Agent** implementation using the
AgentScope framework. The ReAct agent combines reasoning and action capabilities to solve complex tasks through iterative thought processes and tool usage.

### Key Features:
- **Intelligent Assistant**: Creates an AI assistant named "Friday" that can think and act
- **Tool Integration**: Equipped with practical tools including:
  - Shell command execution
  - Python code execution
  - Text file viewing
- **Interactive Conversation**: Supports real-time conversation between user and agent
- **Streaming Response**: Enables real-time response streaming for better user experience
- **Memory Management**: Maintains conversation context using in-memory storage

The agent follows the ReAct paradigm, where it can:
1. **Reason** about the user's request
2. **Act** by using available tools when necessary
3. **Observe** the results and continue reasoning
4. **Respond** with helpful information or actions

## How to Run This Example
1. **Set Environment Variable**:
   ```bash
   export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
   ```
2. Run the script:
    ```bash
   python main.py
   ```

## Replace the Model and Formatter
AgentScope provides several built-in model implementations that you can easily switch between. You can also implement your own custom model if needed.

For example, to use the OpenAI model, you can replace the model and formatter as follows:
```
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

# Replace the model and formatter with:
agent = ReActAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant named Friday.",
    model=OpenAIChatModel(
        api_key=os.environ.get("OPENAI_API_KEY"),
        model_name="gpt-4",
        stream=True,
    ),
    formatter=OpenAIChatFormatter(),  # Use matching formatter
    toolkit=toolkit,
    memory=InMemoryMemory(),
)
```

## Notes

⚠️ Formatter Compatibility: Always use the formatter that matches your model. Mismatched formatters can cause communication errors between the agent and the model.

🔧 Tool Usage: Ensure your chosen model supports function calling capabilities to maintain full ReAct functionality with tool usage.

🏠 Local Models: For local models, ensure the model service (like Ollama) is running before starting the agent.