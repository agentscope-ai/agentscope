"""
OpenAI ReAct Agent Example using AgentScope.

Features:
- Loads API keys from .env or environment
- Configures GPT model with streaming output
- Registers basic Python + shell tools
- Uses AgentScope's ReActAgent for reasoning and tool use
- Interactive terminal loop

Author: [Your Name]
"""

import os
import asyncio
from pathlib import Path

import agentscope as ascope
from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, execute_python_code, execute_shell_command


async def main() -> None:
    # Try to load environment variables
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        # Fallback loader: parse .env manually
        def _load_env_file(p: Path) -> None:
            if not p.exists():
                return
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except Exception:
                pass

        _load_env_file(Path.cwd() / ".env")
        _load_env_file(Path(__file__).resolve().parent / ".env")
        _load_env_file(Path(__file__).resolve().parents[2] / ".env")

    # Initialize AgentScope (Studio/tracing optional)
    ascope.init(project="OpenAIQuickstart")

    # Register tools
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(execute_shell_command)

    # Check for API key
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "Missing OPENAI_API_KEY. Add it to environment or .env file."
        )

    # Set up model
    model = OpenAIChatModel(
        model_name=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        stream=True,
    )

    formatter = OpenAIChatFormatter()

    # Define the ReAct agent
    agent = ReActAgent(
        name="Friday",
        sys_prompt=(
            "You're a helpful assistant named Friday. "
            "Think step by step, and use tools only when helpful."
        ),
        model=model,
        formatter=formatter,
        memory=InMemoryMemory(),
        toolkit=toolkit,
        parallel_tool_calls=False,
    )

    user = UserAgent(name="user")

    print("\n💬 Type your message below (type 'exit' to quit):")

    # Conversation loop
    msg = None
    while True:
        msg = await agent(msg)
        msg = await user(msg)
        if (msg.get_text_content() or "").strip().lower() == "exit":
            print("👋 Exiting.")
            break


if __name__ == "__main__":
    asyncio.run(main())
