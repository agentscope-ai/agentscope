# -*- coding: utf-8 -*-
"""
A ReAct agent example that demonstrates audio output capability.
Note: When audio output is enabled, tool calling functionality may be disabled.
"""
import asyncio
import os
from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import OpenAIChatFormatter

from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel


async def main() -> None:
    """The main entry point for the ReAct audio agent example."""

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant",
        model=OpenAIChatModel(
            model_name="qwen3-omni-flash",
            client_args={
                "base_url": "https://dashscope.aliyuncs.com/"
                "compatible-mode/v1",
            },
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            stream=True,
            # More options can be found in the DashScope API docs:
            # https://help.aliyun.com/zh/model-studio/qwen-omni
            generate_kwargs={
                "modalities": ["text", "audio"],
                "audio": {"voice": "Cherry", "format": "wav"},
            },
        ),
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
    )

    user = UserAgent("Bob")

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)


asyncio.run(main())
