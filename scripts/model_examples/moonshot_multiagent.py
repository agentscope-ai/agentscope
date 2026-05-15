# -*- coding: utf-8 -*-
"""Example of Moonshot model calls with MoonshotMultiAgentFormatter.

The multi-agent formatter wraps prior conversation history in
<history></history> tags and preserves reasoning_content for Moonshot's
Preserved Thinking feature in multi-turn conversations.
"""
import asyncio
import os

from _utils import stream_and_collect
from agentscope.formatter import MoonshotMultiAgentFormatter
from agentscope.message import Msg
from agentscope.model import MoonshotChatModel
from agentscope.credential import MoonshotCredential


async def example_multiagent() -> None:
    """Simulate a multi-agent conversation and let kimi-k2.6 summarize it.

    Alice and Bob discuss the weather, then a moderator (the model) is asked
    to summarize the conversation.
    """
    formatter = MoonshotMultiAgentFormatter()

    model = MoonshotChatModel(
        credential=MoonshotCredential(
            api_key=os.environ["MOONSHOT_API_KEY"],
        ),
        model="kimi-k2.6",
        stream=True,
        context_size=262_144,
        formatter=formatter,
    )

    # Multi-agent conversation history between Alice and Bob
    msgs = [
        Msg(
            name="system",
            content="You are a helpful moderator. Summarize the conversation.",
            role="system",
        ),
        Msg(
            name="alice",
            content="Hi Bob! What do you think about the weather today?",
            role="user",
        ),
        Msg(
            name="bob",
            content="It's quite sunny and warm, Alice. Perfect for a walk!",
            role="assistant",
        ),
        Msg(
            name="alice",
            content="Agreed! I might head to the park later.",
            role="user",
        ),
        Msg(
            name="bob",
            content="Great idea. I'll join you if I finish work early.",
            role="assistant",
        ),
        Msg(
            name="moderator",
            content="Please summarize the conversation above in one sentence.",
            role="user",
        ),
    ]

    print("=== Multi-Agent Formatter Call ===")
    await stream_and_collect(await model(msgs))


if __name__ == "__main__":
    asyncio.run(example_multiagent())
