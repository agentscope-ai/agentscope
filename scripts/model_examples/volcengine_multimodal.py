# -*- coding: utf-8 -*-
"""Examples of Volcengine image, video, and audio input using DataBlock.

Set VOLCENGINE_API_KEY and run this script without arguments, or use the
example runner with --providers volcengine --tests multimodal.
The examples use doubao-seed-2-1-lite-260915. For a local audio file, call
``await example_audio("./sample.wav", "audio/wav")``.
"""

import asyncio
import base64
import os
from pathlib import Path

from _utils import stream_and_collect
from agentscope.credential import VolcengineCredential
from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    URLSource,
    UserMsg,
)
from agentscope.model import VolcengineChatModel


TEST_IMAGE_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage"
    "-files/zh-CN/20241022/emyrja/dog_and_girl.jpeg"
)
TEST_VIDEO_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/file-manage"
    "-files/zh-CN/20241115/cqqkru/1.mp4"
)
TEST_AUDIO_URL = (
    "https://ark-project.tos-cn-beijing.volces.com/doc_audio/"
    "ark_demo_audio.mp3"
)


def _build_model() -> VolcengineChatModel:
    """Build a Doubao model supporting image, video, and audio input."""
    return VolcengineChatModel(
        credential=VolcengineCredential(
            api_key=os.environ["VOLCENGINE_API_KEY"],
        ),
        model="doubao-seed-2-1-lite-260915",
        stream=True,
        context_size=1_024_000,
    )


async def example_image_url() -> None:
    """Describe an image provided as a public URL."""
    model = _build_model()
    messages = [
        UserMsg(
            name="user",
            content=[
                TextBlock(text="What is in this image? Describe it briefly."),
                DataBlock(
                    source=URLSource(
                        url=TEST_IMAGE_URL,
                        media_type="image/jpeg",
                    ),
                ),
            ],
        ),
    ]
    print("=== Multimodal Call (Image URL) ===")
    await stream_and_collect(await model(messages))


async def example_image_local_path() -> None:
    """Describe a local image using a portable file URI."""
    model = _build_model()
    image_path = Path(__file__).resolve().parent / "test.jpeg"
    messages = [
        UserMsg(
            name="user",
            content=[
                TextBlock(text="What is in this image? Describe it briefly."),
                DataBlock(
                    source=URLSource(
                        url=image_path.as_uri(),
                        media_type="image/jpeg",
                    ),
                ),
            ],
        ),
    ]
    print("=== Local Path Call (file://) ===")
    await stream_and_collect(await model(messages))


async def example_image_base64() -> None:
    """Describe a local image using explicit Base64 encoding."""
    model = _build_model()
    image_path = Path(__file__).resolve().parent / "test.jpeg"
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    messages = [
        UserMsg(
            name="user",
            content=[
                TextBlock(text="What is in this image? Describe it briefly."),
                DataBlock(
                    source=Base64Source(data=data, media_type="image/jpeg"),
                ),
            ],
        ),
    ]
    print("=== Explicit Base64 Call ===")
    await stream_and_collect(await model(messages))


async def example_video() -> None:
    """Describe a video provided as a public URL."""
    model = _build_model()
    messages = [
        UserMsg(
            name="user",
            content=[
                TextBlock(text="What is happening in this video?"),
                DataBlock(
                    source=URLSource(
                        url=TEST_VIDEO_URL,
                        media_type="video/mp4",
                    ),
                ),
            ],
        ),
    ]
    print("=== Multimodal Call (Video URL) ===")
    await stream_and_collect(await model(messages))


async def example_audio(
    audio: str = TEST_AUDIO_URL,
    media_type: str = "audio/mpeg",
) -> None:
    """Transcribe a public audio URL or a local MP3, WAV, AAC, or M4A file."""
    model = _build_model()
    url = (
        audio
        if audio.startswith(("http://", "https://", "file://"))
        else Path(audio).expanduser().resolve().as_uri()
    )
    messages = [
        UserMsg(
            name="user",
            content=[
                TextBlock(text="Transcribe this audio."),
                DataBlock(source=URLSource(url=url, media_type=media_type)),
            ],
        ),
    ]
    print("=== Multimodal Call (Audio) ===")
    await stream_and_collect(await model(messages))


if __name__ == "__main__":
    asyncio.run(example_image_url())
    asyncio.run(example_image_local_path())
    asyncio.run(example_image_base64())
    asyncio.run(example_video())
    asyncio.run(example_audio())
