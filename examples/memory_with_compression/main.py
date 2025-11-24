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

from _memory_with_compress import (  # noqa: E402, E501
    MemoryWithCompress,
)

# Imports must come after sys.path manipulation
from agentscope.agent import ReActAgent  # noqa: E402
from agentscope.formatter import DashScopeChatFormatter  # noqa: E402
from agentscope.message import Msg  # noqa: E402
from agentscope.model import DashScopeChatModel  # noqa: E402
from agentscope.tool import Toolkit  # noqa: E402
from agentscope.token import OpenAITokenCounter  # noqa: E402


async def main() -> None:
    """The main entry point of the MemoryWithCompress example."""

    toolkit = Toolkit()

    # Create model for agent and memory compression
    model = DashScopeChatModel(
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        model_name="qwen-max",
        stream=False,
    )

    async def trigger_compression(msgs: list[Msg]) -> bool:
        # Trigger compression if the number of messages in self._memory
        # exceeds 2 and the last message is from the assistant
        return len(msgs) > 2 and msgs[-1].role == "assistant"

    # Create MemoryWithCompress instance
    # max_token: maximum token count before compression
    memory_with_compress = MemoryWithCompress(
        model=model,
        formatter=DashScopeChatFormatter(),
        max_token=3000,  # Set a lower value for testing compression
        token_counter=OpenAITokenCounter(model_name="qwen-max"),
        compression_trigger_func=trigger_compression,  # Trigger compression
        # if the number of messages in self._memory exceeds 2
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
    print(
        f"\n\n\n******************The memory after the first query is: "
        f"******************\n{current_memory}\n\n",
    )

    query_2 = Msg(
        "user",
        "What is his most renowned achievement?",
        "user",
    )
    await agent(query_2)
    current_memory = await memory_with_compress.get_memory()
    print(
        f"\n\n\n******************The memory after the second query is: "
        f"******************\n{current_memory}\n\n",
    )


asyncio.run(main())
