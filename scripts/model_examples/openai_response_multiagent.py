# -*- coding: utf-8 -*-
"""Example of OpenAI Responses API model calls with
OpenAIResponseMultiAgentFormatter.

The multi-agent formatter wraps prior conversation history in
<history></history> tags, enabling the model to handle multi-agent
conversations where more than one non-user agent is involved.
"""
import asyncio
import os

from _utils import stream_and_collect
from agentscope.formatter import OpenAIResponseMultiAgentFormatter
from agentscope.message import Msg
from agentscope.model import OpenAIResponseModel
from agentscope.credential import OpenAICredential


async def example_multiagent() -> None:
    """Simulate a multi-agent conversation and let gpt-4.1 (Responses API)
    summarize it.

    Alice and Bob discuss the weather, then a moderator (the model) is asked
    to summarize the conversation.
    """
    formatter = OpenAIResponseMultiAgentFormatter()

    model = OpenAIResponseModel(
        credential=OpenAICredential(
            api_key=os.environ["OPENAI_API_KEY"],
        ),
        model="gpt-4.1",
        stream=True,
        context_size=1_047_576,
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
