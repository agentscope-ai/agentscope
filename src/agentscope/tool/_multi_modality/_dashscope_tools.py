# -*- coding: utf-8 -*-
"""Use DashScope API to generate images,
convert text to audio, and convert images to text.
Please refer to the `official documentation <https://dashscope.aliyun.com/>`_
 for more details.
"""
import base64
from typing import Literal, Sequence

import os


from ..._utils._common import _get_bytes_from_web_url
from ...message import ImageBlock, TextBlock, AudioBlock
from ...tool import ToolResponse


def dashscope_text_to_image(
    prompt: str,
    api_key: str,
    n: int = 1,
    size: Literal["1024*1024", "720*1280", "1280*720"] = "1024*1024",
    model: str = "wanx-v1",
    use_base64: bool = False,
) -> ToolResponse:
    """Generate image(s) based on the given prompt, and return image url(s)
    or base64 data.

    Args:
        prompt (`str`):
            The text prompt to generate image.
        api_key (`str`):
            The api key for the dashscope api.
        n (`int`, defaults to `1`):
            The number of images to generate.
        size (`Literal["1024*1024", "720*1280", "1280*720"]`, defaults to \
         `"1024*1024"`):
            Size of the image.
        model (`str`, defaults to '"wanx-v1"'):
            The model to use, such as "wanx-v1", "qwen-image",
            "wan2.2-t2i-flash", etc.
        use_base64 (`bool`, defaults to 'False'):
            Whether to use base64 data for images.

    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.
    """
    try:
        import dashscope

        response = dashscope.ImageSynthesis.call(
            model=model,
            prompt=prompt,
            api_key=api_key,
            n=n,
            size=size,
        )
        images = response.output["results"]
        urls = [_["url"] for _ in images]

        image_blocks: list = []

        if urls is not None:
            for url in urls:
                if use_base64:
                    extension = url.split(".")[-1].lower()

                    image_data = _get_bytes_from_web_url(url)
                    image_blocks.append(
                        ImageBlock(
                            type="image",
                            source={
                                "type": "base64",
                                "media_type": f"image/{extension}",
                                "data": image_data,
                            },
                        ),
                    )
                else:
                    image_blocks.append(
                        ImageBlock(
                            type="image",
                            source={
                                "type": "url",
                                "url": url,
                            },
                        ),
                    )

            return ToolResponse(
                content=image_blocks,
            )

        else:
            return ToolResponse(
                [
                    TextBlock(
                        type="text",
                        text="Error: Failed to generate images",
                    ),
                ],
            )
    except Exception as e:
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=f"Failed to generate images: {str(e)}",
                ),
            ],
        )


def dashscope_image_to_text(
    image_urls: str | Sequence[str],
    api_key: str,
    prompt: str = "Describe the image",
    model: str = "qwen-vl-plus",
) -> ToolResponse:
    """Generate text based on the given images.

    Args:
        image_urls (`str | Sequence[str]`):
            The url of single or multiple images.
        api_key (`str`):
            The api key for the dashscope api.
        prompt (`str`, defaults to 'Describe the image' ):
            The text prompt.
        model (`str`, defaults to 'qwen-vl-plus'):
            The model to use in DashScope MultiModal API.

    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.
    """

    if isinstance(image_urls, str):
        image_urls = [image_urls]

    # Check if the local url is valid
    img_abs_urls = []
    for url in image_urls:
        if os.path.exists(url):
            if os.path.isfile(url):
                img_abs_urls.append(os.path.abspath(url))
            else:
                return ToolResponse(
                    [
                        TextBlock(
                            type="text",
                            text=f'Error: The input image url "{url}" is '
                            f"not a file.",
                        ),
                    ],
                )
        else:
            # Maybe a web url or an invalid url, we leave it to the API
            # to handle
            img_abs_urls.append(url)

    # Convert image paths according to the model requirements
    contents = []
    for url in img_abs_urls:
        contents.append(
            {
                "image": url,
            },
        )

    contents.append({"text": prompt})

    # currently only support one round of conversation
    # if multiple rounds of conversation are needed,
    # it would be better to implement an Agent class
    sys_message = {
        "role": "system",
        "content": [{"text": "You are a helpful assistant."}],
    }
    user_message = {
        "role": "user",
        "content": contents,
    }
    messages = [sys_message, user_message]
    try:
        import dashscope

        response = dashscope.MultiModalConversation.call(
            model=model,
            messages=messages,
            api_key=api_key,
        )
        content = response.output["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = content[0]["text"]
        if content is not None:
            return ToolResponse(
                [
                    TextBlock(
                        type="text",
                        text=content,
                    ),
                ],
            )
        else:
            return ToolResponse(
                [
                    TextBlock(
                        type="text",
                        text="Error: Failed to generate text",
                    ),
                ],
            )
    except Exception as e:
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=f"Failed to generate text: {str(e)}",
                ),
            ],
        )


def dashscope_text_to_audio(
    text: str,
    api_key: str,
    model: str = "sambert-zhichu-v1",
    sample_rate: int = 48000,
) -> ToolResponse:
    """Convert the given text to audio.

    Args:
        text (`str`):
            The text to be converted into audio.
        api_key (`str`):
            The api key for the dashscope API.
        model (`str`, defaults to 'sambert-zhichu-v1'):
            The model to use. Full model list can be found in the
            `official document
            <https://help.aliyun.com/zh/model-studio/sambert-python-sdk>`_.
        sample_rate (`int`, defaults to 48000):
            Sample rate of the audio.

    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.

    """
    try:
        import dashscope

        dashscope.api_key = api_key

        res = dashscope.audio.tts.SpeechSynthesizer.call(
            model=model,
            text=text,
            sample_rate=sample_rate,
            format="wav",
        )

        audio_data = res.get_audio_data()

        if audio_data is not None:
            audio_base64 = base64.b64encode(audio_data).decode("utf-8")

            return ToolResponse(
                [
                    AudioBlock(
                        type="audio",
                        source={
                            "type": "base64",
                            "media_type": "audio/wav",
                            "data": audio_base64,
                        },
                    ),
                ],
            )
        else:
            return ToolResponse(
                [
                    TextBlock(
                        type="text",
                        text="Error: Failed to generate audio",
                    ),
                ],
            )
    except Exception as e:
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=f"Failed to generate audio: {str(e)}",
                ),
            ],
        )
