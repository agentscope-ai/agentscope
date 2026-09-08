# -*- coding: utf-8 -*-
"""Example of MiniMax chat model multimodal (vision) calls using
DataBlock.

The MiniMax M-series chat models accept image input on the
Anthropic-compatible endpoint, identical to Anthropic Claude.
"""

import asyncio
import base64
import os
from pathlib import Path

from _utils import stream_and_collect
from agentscope.credential import MiniMaxCredential
from agentscope.message import (
    Msg,
    TextBlock,
    DataBlock,
    URLSource,
    Base64Source,
)
from agentscope.model import MiniMaxChatModel

# A publicly accessible test image (dog and girl photo)
TEST_IMAGE_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage"
    "-files/zh-CN/20241022/emyrja/dog_and_girl.jpeg"
)


def _build_model() -> MiniMaxChatModel:
    """Build and return a MiniMaxChatModel instance."""
    return MiniMaxChatModel(
        credential=MiniMaxCredential(
            api_key=os.environ["MINIMAX_API_KEY"],
        ),
        model="MiniMax-M3",
        stream=True,
        parameters=MiniMaxChatModel.Parameters(
            thinking_enable=True,
            thinking_budget=1024,
        ),
    )


async def example_image_url() -> None:
    """Call MiniMax-M3 with an image URL and ask what is in the image."""
    model = _build_model()

    image_block = DataBlock(
        source=URLSource(
            url=TEST_IMAGE_URL,
            media_type="image/jpeg",
        ),
    )

    msgs = [
        Msg(
            name="user",
            content=[
                TextBlock(
                    text="What animal is in this image? Describe it briefly.",
                ),
                image_block,
            ],
            role="user",
        ),
    ]

    print("=== Multimodal Call (Image URL) ===")
    await stream_and_collect(await model(msgs))


async def example_image_local_path() -> None:
    """Call MiniMax-M3 with a local image using a ``file://`` URL.

    The formatter automatically reads the file and converts it to base64.
    """
    model = _build_model()

    abs_path = str(Path(__file__).parent / "test.jpeg")
    image_block = DataBlock(
        source=URLSource(
            url=f"file://{abs_path}",
            media_type="image/jpeg",
        ),
    )

    msgs = [
        Msg(
            name="user",
            content=[
                TextBlock(
                    text=(
                        "What is happening in this image? Describe it "
                        "briefly."
                    ),
                ),
                image_block,
            ],
            role="user",
        ),
    ]

    print("=== Local Path Call (file://) ===")
    await stream_and_collect(await model(msgs))


async def example_image_base64() -> None:
    """Call MiniMax-M3 with a local image using explicit base64 encoding.

    Use ``Base64Source`` when you already have the binary data in memory or
    want full control over the encoding step.
    """
    model = _build_model()

    with open(Path(__file__).parent / "test.jpeg", "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")

    image_block = DataBlock(
        source=Base64Source(
            data=data,
            media_type="image/jpeg",
        ),
    )

    msgs = [
        Msg(
            name="user",
            content=[
                TextBlock(
                    text=(
                        "What is happening in this image? Describe it "
                        "briefly."
                    ),
                ),
                image_block,
            ],
            role="user",
        ),
    ]

    print("=== Explicit Base64 Call ===")
    await stream_and_collect(await model(msgs))


if __name__ == "__main__":
    asyncio.run(example_image_url())
    asyncio.run(example_image_local_path())
    asyncio.run(example_image_base64())
