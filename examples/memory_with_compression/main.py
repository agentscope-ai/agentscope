# -*- coding: utf-8 -*-
"""The main entry point of the MemoryWithCompress example."""
import asyncio
import os
from _memory_with_compress import MemoryWithCompress
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit
from agentscope.token import OpenAITokenCounter


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
        f"******************\n{current_memory}\n\n******************",
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
        f"******************\n{current_memory}\n\n******************",
    )

    print("The state dictionary of the memory with compression:")

    state_dict = memory_with_compress.state_dict()
    print(state_dict.model_dump_json(indent=4))

    memory_with_compress.load_state_dict(state_dict)


asyncio.run(main())
