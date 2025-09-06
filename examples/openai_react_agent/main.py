"""
OpenAI ReAct Agent Example using AgentScope.

This example demonstrates:
- Using OpenAI's GPT models with streaming
- Basic Python and shell command execution tools
- Interactive conversation using ReActAgent
- Proper tool initialization and management
"""

import os
import asyncio

from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import (
    Toolkit,
    execute_python_code,
    execute_shell_command,
)


async def main() -> None:
    """Main entry point for the OpenAI ReAct agent example."""
    # Create toolkit and register basic tools
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(execute_shell_command)

    # Initialize the OpenAI model
    model = OpenAIChatModel(
        model_name="gpt-4",
        api_key=os.getenv("OPENAI_API_KEY"),
        stream=True,
    )

    # System prompt for the agent
    sys_prompt = """You are a helpful AI assistant that can use tools to solve tasks.
You can:
- Execute Python code for computation or data processing
- Run shell commands for system operations

Think through problems step by step and use the appropriate tools when needed.
Always validate your inputs and handle errors gracefully."""

    # Initialize the ReAct agent
    agent = ReActAgent(
        name="Assistant",
        sys_prompt=sys_prompt,
        model=model,
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        enable_meta_tool=True,  # Allows agent to manage its tools
        parallel_tool_calls=True,  # Enable parallel tool execution
    )

    # Initialize user agent
    user = UserAgent(name="User")

    # Interactive loop
    print("OpenAI ReAct Agent initialized. Type 'exit' to quit.")
    msg = None
    while True:
        # Get user input and handle through user agent
        msg = await user(msg)
        text_content = msg.get_text_content()
        if text_content and text_content.lower() == "exit":
            break

        # Let the assistant process and respond
        msg = await agent(msg)


if __name__ == "__main__":
    asyncio.run(main())
