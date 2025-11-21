# -*- coding: utf-8 -*-
# pylint: disable=wrong-import-position
"""The main entry point of the MemoryWithCompress example."""
import asyncio
import os
import sys
from pathlib import Path

# Add the project root to Python path to enable imports
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Imports must come after sys.path manipulation
from agentscope.agent import ReActAgent  # noqa: E402
from agentscope.formatter import DashScopeChatFormatter  # noqa: E402
from agentscope.message import Msg  # noqa: E402
from agentscope.model import DashScopeChatModel  # noqa: E402
from agentscope.tool import Toolkit  # noqa: E402
from agentscope.token import OpenAITokenCounter  # noqa: E402

from examples.react_memory._memory_with_compress import (  # noqa: E402
    MemoryWithCompress,
)


async def main() -> None:
    """The main entry point of the MemoryWithCompress example."""

    toolkit = Toolkit()

    # Create model for agent and memory compression
    model = DashScopeChatModel(
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        model_name="qwen-max",
        stream=False,
    )

    # Create MemoryWithCompress instance
    # max_token: maximum token count before compression
    memory_with_compress = MemoryWithCompress(
        model=model,
        formatter=DashScopeChatFormatter(),
        max_token=300,  # Set a lower value for testing compression
        token_counter=OpenAITokenCounter(model_name="qwen-max"),
    )

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=model,
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=memory_with_compress,
    )

    query_1 = Msg(
        "user",
        "Please introduce Einstein",
        "user",
    )
    await agent(query_1)
    current_memory = await memory_with_compress.get_memory()
    print(f"The memory after the first query is: {current_memory}")

    query_2 = Msg(
        "user",
        "What is his most renowned achievement?",
        "user",
    )
    await agent(query_2)
    current_memory = await memory_with_compress.get_memory()
    print(f"The memory after the second query is: {current_memory}")


asyncio.run(main())
