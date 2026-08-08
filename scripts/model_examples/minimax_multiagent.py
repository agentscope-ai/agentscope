# -*- coding: utf-8 -*-
"""Example of MiniMax chat model calls with MiniMaxMultiAgentFormatter.

The multi-agent formatter wraps prior conversation history in
``<history></history>`` tags, enabling MiniMax-M3 to handle multi-agent
conversations where more than one non-user agent is involved.
"""

import asyncio
import os

from _utils import stream_and_collect
from agentscope.credential import MiniMaxCredential
from agentscope.formatter import MiniMaxMultiAgentFormatter
from agentscope.message import Msg, TextBlock
from agentscope.model import MiniMaxChatModel


async def example_multiagent() -> None:
    """Simulate a multi-agent conversation and let MiniMax-M3 summarize.

    Alice and Bob discuss the weather, then a moderator (the model) is asked
    to summarize the conversation.
    """
    formatter = MiniMaxMultiAgentFormatter()

    model = MiniMaxChatModel(
        credential=MiniMaxCredential(
            api_key=os.environ["MINIMAX_API_KEY"],
        ),
        model="MiniMax-M3",
        stream=True,
        parameters=MiniMaxChatModel.Parameters(
            thinking_enable=True,
            thinking_budget=1024,
        ),
        formatter=formatter,
    )

    msgs = [
        Msg(
            name="system",
            content=[
                TextBlock(
                    text=(
                        "You are a helpful moderator. Summarize the "
                        "conversation."
                    ),
                ),
            ],
            role="system",
        ),
        Msg(
            name="alice",
            content=[
                TextBlock(
                    text="Hi Bob! What do you think about the weather today?",
                ),
            ],
            role="user",
        ),
        Msg(
            name="bob",
            content=[
                TextBlock(
                    text=(
                        "It's quite sunny and warm, Alice. Perfect for a "
                        "walk!"
                    ),
                ),
            ],
            role="assistant",
        ),
        Msg(
            name="alice",
            content=[
                TextBlock(text="Agreed! I might head to the park later."),
            ],
            role="user",
        ),
        Msg(
            name="bob",
            content=[
                TextBlock(
                    text="Great idea. I'll join you if I finish work early.",
                ),
            ],
            role="assistant",
        ),
        Msg(
            name="moderator",
            content=[
                TextBlock(
                    text=(
                        "Please summarize the conversation above in one "
                        "sentence."
                    ),
                ),
            ],
            role="user",
        ),
    ]

    print("=== Multi-Agent Formatter Call ===")
    await stream_and_collect(await model(msgs))


if __name__ == "__main__":
    asyncio.run(example_multiagent())
