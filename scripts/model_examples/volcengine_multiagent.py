# -*- coding: utf-8 -*-
"""Example of Volcengine Ark calls with its multi-agent formatter."""
import asyncio
import os

from _utils import stream_and_collect
from agentscope.credential import VolcengineCredential
from agentscope.formatter import VolcengineMultiAgentFormatter
from agentscope.message import Msg, TextBlock
from agentscope.model import VolcengineChatModel


async def example_multiagent() -> None:
    """Ask a Doubao model to summarize a multi-agent conversation."""
    model = VolcengineChatModel(
        credential=VolcengineCredential(
            api_key=os.environ["VOLCENGINE_API_KEY"],
        ),
        model="doubao-seed-2-1-pro-260628",
        stream=True,
        parameters=VolcengineChatModel.Parameters(thinking_enable=True),
        formatter=VolcengineMultiAgentFormatter(),
    )
    msgs = [
        Msg(
            name="system",
            content="You are a helpful moderator.",
            role="system",
        ),
        Msg(
            name="alice",
            content=[TextBlock(text="The weather is sunny today.")],
            role="user",
        ),
        Msg(
            name="bob",
            content=[TextBlock(text="It is a good day for a walk.")],
            role="assistant",
        ),
        Msg(
            name="moderator",
            content="Summarize the conversation in one sentence.",
            role="user",
        ),
    ]

    print("=== Multi-Agent Formatter Call ===")
    await stream_and_collect(await model(msgs))


if __name__ == "__main__":
    asyncio.run(example_multiagent())
