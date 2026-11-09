# -*- coding: utf-8 -*-
"""Example of DashScope model multimodal (vision) calls using DataBlock."""
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
from agentscope.model import DashScopeChatModel
from agentscope.credential import DashScopeCredential

# A publicly accessible test image
TEST_IMAGE_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage"
    "-files/zh-CN/20241022/emyrja/dog_and_girl.jpeg"
)

# A publicly accessible test video
TEST_VIDEO_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage"
    "-files/zh-CN/20241115/cqqkru/1.mp4"
)

# A publicly accessible test audio
TEST_AUDIO_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage"
    "-files/zh-CN/20250211/tixcef/cherry.wav"
)


async def example_image_url() -> None:
    """Call qwen3.5-plus with an image URL and ask what is in the image."""
    model = DashScopeChatModel(
        credential=DashScopeCredential(
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        model="qwen3.5-plus",
        stream=True,
        context_size=1_000_000,
        parameters=DashScopeChatModel.Parameters(thinking_enable=True),
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


def _build_model() -> DashScopeChatModel:
    """Build and return a DashScopeChatModel instance."""
    return DashScopeChatModel(
        credential=DashScopeCredential(
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        model="qwen3.5-plus",
        stream=True,
        context_size=1_000_000,
        parameters=DashScopeChatModel.Parameters(thinking_enable=True),
    )


async def example_image_local_path() -> None:
    """Call qwen3.5-plus with a local image using a ``file://`` URL.

    The formatter reads the file from disk and converts it to a base64 data
     URI.
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
                    text="What is happening in this image? Describe it "
                    "briefly.",
                ),
                image_block,
            ],
            role="user",
        ),
    ]

    print("=== Local Path Call (file://) ===")
    await stream_and_collect(await model(msgs))


async def example_image_base64() -> None:
    """Call qwen3.5-plus with a local image using explicit base64 encoding.

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
                    text="What is happening in this image? Describe it "
                    "briefly.",
                ),
                image_block,
            ],
            role="user",
        ),
    ]

    print("=== Explicit Base64 Call ===")
    await stream_and_collect(await model(msgs))


async def example_video() -> None:
    """Call qwen3.5-plus with a video URL and ask what is in the video."""
    model = _build_model()

    video_block = DataBlock(
        source=URLSource(
            url=TEST_VIDEO_URL,
            media_type="video/mp4",
        ),
    )

    msgs = [
        Msg(
            name="user",
            content=[
                TextBlock(
                    text="What is happening in this video? "
                    "Describe it briefly.",
                ),
                video_block,
            ],
            role="user",
        ),
    ]

    print("=== Multimodal Call (Video URL) ===")
    await stream_and_collect(await model(msgs))


async def example_audio() -> None:
    """Call qwen3.5-omni-plus with an audio URL.

    Audio understanding requires an Omni model (qwen3.5-omni-plus or
    Qwen3-Omni-Flash). Omni models also require stream=True and the
    ``modalities`` parameter.
    """
    model = DashScopeChatModel(
        credential=DashScopeCredential(
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        model="qwen3.5-omni-plus",
        stream=True,
        context_size=1_000_000,
    )

    audio_block = DataBlock(
        source=URLSource(
            url=TEST_AUDIO_URL,
            media_type="audio/wav",
        ),
    )

    msgs = [
        Msg(
            name="user",
            content=[
                TextBlock(text="What is being said in this audio clip?"),
                audio_block,
            ],
            role="user",
        ),
    ]

    print("=== Multimodal Call (Audio URL - Omni) ===")
    await stream_and_collect(
        await model(msgs, modalities=["text", "audio"]),
    )


if __name__ == "__main__":
    asyncio.run(example_image_url())
    asyncio.run(example_image_local_path())
    asyncio.run(example_image_base64())
    asyncio.run(example_video())
    asyncio.run(example_audio())
