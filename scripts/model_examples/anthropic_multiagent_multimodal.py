# -*- coding: utf-8 -*-
"""Example of Anthropic Claude model calls with MultiAgentFormatter and
image input.

Demonstrates combining AnthropicMultiAgentFormatter with multimodal (vision)
content: Alice shares an image in a multi-agent conversation, and the model
is asked to summarize what everyone is looking at.
"""
import asyncio
import os

from _utils import stream_and_collect
from agentscope.formatter import AnthropicMultiAgentFormatter
from agentscope.message import Msg, TextBlock, DataBlock, URLSource
from agentscope.model import AnthropicChatModel
from agentscope.credential import AnthropicCredential

TEST_IMAGE_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage"
    "-files/zh-CN/20241022/emyrja/dog_and_girl.jpeg"
)


async def example_multiagent_multimodal() -> None:
    """Multi-agent conversation where Alice shares an image for the group."""
    formatter = AnthropicMultiAgentFormatter()

    model = AnthropicChatModel(
        credential=AnthropicCredential(
            api_key=os.environ["ANTHROPIC_API_KEY"],
        ),
        model="claude-opus-4-5",
        stream=True,
        context_size=1_000_000,
        formatter=formatter,
    )

    image_block = DataBlock(
        source=URLSource(url=TEST_IMAGE_URL, media_type="image/jpeg"),
    )

    msgs = [
        Msg(
            name="system",
            content=(
                "You are a helpful moderator in a group chat. "
                "Summarize what the image shows and what the participants "
                "said."
            ),
            role="system",
        ),
        Msg(
            name="alice",
            content=[
                TextBlock(
                    text="Hey everyone, look at this cute photo I took!",
                ),
                image_block,
            ],
            role="user",
        ),
        Msg(
            name="bob",
            content="Aww, that's adorable! Where was this taken?",
            role="assistant",
        ),
        Msg(
            name="alice",
            content="At the local park yesterday.",
            role="user",
        ),
        Msg(
            name="moderator",
            content="Please summarize the image content and the "
            "conversation in one paragraph.",
            role="user",
        ),
    ]

    print("=== Multi-Agent + Multimodal Call ===")
    await stream_and_collect(await model(msgs))


if __name__ == "__main__":
    asyncio.run(example_multiagent_multimodal())
