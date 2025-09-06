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
    # Best-effort load .env without adding hard dependency
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        # Fallback: minimal .env loader (KEY=VALUE, ignores comments)
        def _load_env_file(p: Path) -> None:
            if not p.exists():
                return
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
            except Exception:
                pass

        # Look in CWD, example dir, and project root
        _load_env_file(Path.cwd() / ".env")
        _load_env_file(Path(__file__).resolve().parent / ".env")
        _load_env_file(Path(__file__).resolve().parents[2] / ".env")

    # Initialize AgentScope (logging only; no Studio/tracing unless provided)
    ascope.init(project="OpenAIQuickstart")

    # Toolkit with a couple of useful tools (optional)
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(execute_shell_command)

    # Configure OpenAI model and formatter
    # Require an API key
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "Missing OPENAI_API_KEY. Set it in your environment or in a .env file."
        )

    model = OpenAIChatModel(
        model_name=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        stream=True,
    )
    formatter = OpenAIChatFormatter()

    # Create the ReAct agent
    agent = ReActAgent(
        name="Friday",
        sys_prompt=(
            "You're a helpful assistant named Friday."
            " Think step by step, and use tools only when helpful."
        ),
        model=model,
        formatter=formatter,
        memory=InMemoryMemory(),
        toolkit=toolkit,
        parallel_tool_calls=False,
    )

    # Create a simple interactive user
    user = UserAgent(name="user")

    # Conversation loop
    msg = None
    while True:
        msg = await agent(msg)
        msg = await user(msg)
        if (msg.get_text_content() or "").strip().lower() == "exit":
            break


if __name__ == "__main__":
    asyncio.run(main())
