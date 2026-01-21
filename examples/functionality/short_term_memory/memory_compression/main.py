# -*- coding: utf-8 -*-
"""The main entry point of the MemoryWithCompress example."""
import asyncio
import os
from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.token import CharTokenCounter


async def main() -> None:
    """The main entry point of the MemoryWithCompress example."""

    # Create model for agent and memory compression
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            model_name="qwen3-max",
        ),
        formatter=DashScopeChatFormatter(),
        compression_config=ReActAgent.CompressionConfig(
            enable=True,
            agent_token_counter=CharTokenCounter(),
            # We set a small trigger threshold for demonstration purposes.
            trigger_threshold=1000,
            keep_recent=3,
        ),
    )
    user = UserAgent("User")

    # Simulate a conversation to trigger memory compression
    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)

    print("The memory of the agent:")
    for msg in await agent.memory.get_memory():
        print(msg.to_dict(), end="\n")


asyncio.run(main())
