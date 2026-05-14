# -*- coding: utf-8 -*-
"""Example of OpenAI Chat model multimodal (vision) calls using DataBlock."""
import asyncio
import base64
import os
from pathlib import Path

from _utils import stream_and_collect
from agentscope.message import (
    Msg,
    TextBlock,
    DataBlock,
    URLSource,
    Base64Source,
)
from agentscope.model import OpenAIChatModel
from agentscope.credential import OpenAICredential

# A publicly accessible test image (a simple cat photo)
TEST_IMAGE_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage"
    "-files/zh-CN/20241022/emyrja/dog_and_girl.jpeg"
)


async def example_multimodal() -> None:
    """Call gpt-4.1 with an image URL and ask what is in the image."""
    model = OpenAIChatModel(
        credential=OpenAICredential(
            api_key=os.environ["OPENAI_API_KEY"],
        ),
        model="gpt-4.1",
        stream=True,
        context_size=1_047_576,
    )

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


def _build_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=os.environ["OPENAI_API_KEY"]),
        model="gpt-4.1",
        stream=True,
        context_size=1_047_576,
    )


async def example_local_path() -> None:
    """Call gpt-4.1 with a local image using a ``file://`` URL.

    The formatter reads the file from disk and converts it to a base64 data
     URI.
    """
    model = _build_model()

    abs_path = str(Path(__file__).parent / "test.jpeg")
    msgs = [
        Msg(
            name="user",
            content=[
                TextBlock(
                    text="What is happening in this image? Describe it "
                    "briefly.",
                ),
                DataBlock(
                    source=URLSource(
                        url=f"file://{abs_path}",
                        media_type="image/jpeg",
                    ),
                ),
            ],
            role="user",
        ),
    ]

    print("=== Local Path Call (file://) ===")
    await stream_and_collect(await model(msgs))


async def example_file_url() -> None:
    """Call gpt-4.1 with a local image using explicit base64 encoding.

    Use ``Base64Source`` when you already have the binary data in memory or
    want full control over the encoding step.
    """
    model = _build_model()

    with open(Path(__file__).parent / "test.jpeg", "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")

    msgs = [
        Msg(
            name="user",
            content=[
                TextBlock(
                    text="What is happening in this image? Describe it "
                    "briefly.",
                ),
                DataBlock(
                    source=Base64Source(data=data, media_type="image/jpeg"),
                ),
            ],
            role="user",
        ),
    ]

    print("=== Explicit Base64 Call ===")
    await stream_and_collect(await model(msgs))


if __name__ == "__main__":
    asyncio.run(example_multimodal())
    asyncio.run(example_local_path())
    asyncio.run(example_file_url())
