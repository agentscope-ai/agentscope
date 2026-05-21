# -*- coding: utf-8 -*-
"""Example of Ollama model calls with OllamaMultiAgentFormatter.

The multi-agent formatter wraps prior conversation history in
<history></history> tags, enabling the model to handle multi-agent
conversations where more than one non-user agent is involved.
"""
import asyncio

from _utils import stream_and_collect
from agentscope.formatter import OllamaMultiAgentFormatter
from agentscope.message import Msg
from agentscope.model import OllamaChatModel


async def example_multiagent() -> None:
    """Simulate a multi-agent conversation and let qwen3:14b summarize it.

    Alice and Bob discuss the weather, then a moderator (the model) is asked
    to summarize the conversation.
    """
    formatter = OllamaMultiAgentFormatter()

    model = OllamaChatModel(
        model="qwen3:14b",
        stream=True,
        context_size=40_960,
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
