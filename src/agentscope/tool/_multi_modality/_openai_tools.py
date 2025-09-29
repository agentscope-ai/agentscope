# -*- coding: utf-8 -*-
"""
Wrap OpenAI API calls as tools. Refer the official
`OpenAI API documentation <https://platform.openai.com/docs/overview>`_ for
more details.
"""
import base64
from io import BytesIO
import os
from typing import Literal, IO
import requests

from .. import ToolResponse
from ...formatter._openai_formatter import _to_openai_image_url
from ...message import (
    ImageBlock,
    TextBlock,
    Base64Source,
    URLSource,
    AudioBlock,
)


def _parse_url(url: str) -> BytesIO | IO[bytes]:
    """
    If url is a local file path, return a BytesIO of the file content.
    If url is a web URL, fetch the content and return as BytesIO.
    """
    if url.startswith(("http://", "https://")):
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return BytesIO(response.content)
    else:
        if not os.path.exists(url):
            raise FileNotFoundError(f"File not found: {url}")
        return open(os.path.abspath(url), "rb")


def openai_text_to_image(
    prompt: str,
    api_key: str,
    n: int = 1,
    model: Literal["dall-e-2", "dall-e-3", "gpt-image-1"] = "dall-e-2",
    size: Literal[
        "256x256",
        "512x512",
        "1024x1024",
        "1792x1024",
        "1024x1792",
    ] = "256x256",
    quality: Literal[
        "auto",
        "standard",
        "hd",
        "high",
        "medium",
        "low",
    ] = "auto",
    style: Literal["vivid", "natural"] = "vivid",
    response_format: Literal["url", "b64_json"] = "url",
) -> ToolResponse:
    """
    Generate image(s) based on the given prompt, and return image URL(s) or
    base64 data.

    Args:
        prompt (`str`):
            The text prompt to generate images.
        api_key (`str`):
            The API key for the OpenAI API.
        n (`int`, defaults to `1`):
            The number of images to generate.
        model (`Literal["dall-e-2", "dall-e-3"]`, defaults to `"dall-e-2"`):
            The model to use for image generation.
        size (`Literal["256x256", "512x512", "1024x1024", "1792x1024", \
        "1024x1792"]`, defaults to `"256x256"`):
            The size of the generated images.
            Must be one of 1024x1024, 1536x1024 (landscape), 1024x1536 (
            portrait), or auto (default value) for gpt-image-1,
            one of 256x256, 512x512, or 1024x1024 for dall-e-2,
            and one of 1024x1024, 1792x1024, or 1024x1792 for dall-e-3.
        quality (`Literal["auto", "standard", "hd", "high", "medium", \
        "low"]`,  defaults to `"auto"`):
            The quality of the image that will be generated.

            - `auto` (default value) will automatically select the best
              quality for the given model.
            - `high`, `medium` and `low` are supported for gpt-image-1.
            - `hd` and `standard` are supported for dall-e-3.
            - `standard` is the only option for dall-e-2.
        style (`Literal["vivid", "natural"]`, defaults to `"vivid"`):
            The style of the generated images.
            This parameter is only supported for dall-e-3.
            Must be one of `vivid` or `natural`.

            - `Vivid` causes the model to lean towards generating hyper-real
              and dramatic images.
            - `Natural` causes the model to produce more natural,
              less hyper-real looking images.
        response_format (`Literal["url", "b64_json"]`, defaults to `"url"`):
            The format in which generated images with dall-e-2 and dall-e-3
            are returned.

            - Must be one of "url" or "b64_json".
            - URLs are only valid for 60 minutes after the image has been
              generated.
            - This parameter isn't supported for gpt-image-1 which will always
              return base64-encoded images.
    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.
    """

    kwargs = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
    }
    if model == "dall-e-3":
        kwargs["style"] = style
    if model != "dall-e-2":
        kwargs["quality"] = quality
    if model != "gpt-image-1":
        kwargs["response_format"] = response_format
    if model == "gpt-image-1":
        response_format = "b64_json"

    try:
        import openai

        client = openai.OpenAI(
            api_key=api_key,
        )
        response = client.images.generate(
            **kwargs,
        )
        image_blocks: list = []
        if response_format == "url":
            image_urls = [_.url for _ in response.data]
            for image_url in image_urls:
                image_blocks.append(
                    ImageBlock(
                        type="image",
                        source=URLSource(
                            type="url",
                            url=image_url,
                        ),
                    ),
                )
        else:
            image_datas = [_.b64_json for _ in response.data]
            for image_data in image_datas:
                image_blocks.append(
                    ImageBlock(
                        type="image",
                        source=Base64Source(
                            type="base64",
                            media_type="image/png",
                            data=image_data,
                        ),
                    ),
                )
        return ToolResponse(
            content=image_blocks,
        )
    except Exception as e:
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=f"Failed to generate image: {str(e)}",
                ),
            ],
        )


def openai_edit_image(
    image_url: str,
    prompt: str,
    api_key: str,
    model: Literal["dall-e-2", "gpt-image-1"] = "dall-e-2",
    mask_url: str | None = None,
    n: int = 1,
    size: Literal[
        "256x256",
        "512x512",
        "1024x1024",
    ] = "256x256",
    response_format: Literal["url", "b64_json"] = "url",
) -> ToolResponse:
    """
    Edit an image based on the provided mask and prompt, and return the edited
    image URL(s) or base64 data.

    Args:
        image_url (`str`):
            The file path or URL to the image that needs editing.
        prompt (`str`):
            The text prompt describing the edits to be made to the image.
        api_key (`str`):
            The API key for the OpenAI API.
        model (`Literal["dall-e-2", "gpt-image-1"]`, defaults to `"dall-e-2"`):
            The model to use for image generation.
        mask_url (`str | None`, defaults to `None`):
            The file path or URL to the mask image that specifies the regions
            to be edited.
        n (`int`, defaults to `1`):
            The number of edited images to generate.
        size (`Literal["256x256", "512x512", "1024x1024"]`, defaults to \
        `"256x256"`):
            The size of the edited images.
        response_format (`Literal["url", "b64_json"]`, defaults to `"url"`):
            The format in which generated images are returned.

            - Must be one of "url" or "b64_json".
            - URLs are only valid for 60 minutes after generation.
            - This parameter isn't supported for gpt-image-1 which will
              always return base64-encoded images.

    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.

    """

    try:
        import openai

        client = openai.OpenAI(
            api_key=api_key,
        )

        def prepare_image(url_or_path: str) -> BytesIO:
            from PIL import Image

            if url_or_path.startswith(("http://", "https://")):
                response = requests.get(url_or_path)
                response.raise_for_status()
                img = Image.open(BytesIO(response.content))
            else:
                img = Image.open(url_or_path)

            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img_buffer = BytesIO()
            img.save(img_buffer, format="PNG")
            img_buffer.seek(0)
            img_buffer.name = "image.png"
            return img_buffer

        image_file = prepare_image(image_url)

        kwargs = {
            "model": model,
            "image": image_file,
            "prompt": prompt,
            "n": n,
            "size": size,
        }

        if mask_url:
            kwargs["mask"] = prepare_image(mask_url)

        if model == "dall-e-2":
            kwargs["response_format"] = response_format
        else:
            response_format = "b64_json"

        response = client.images.edit(**kwargs)

        if response_format == "url":
            urls = [_.url for _ in response.data]
            image_blocks: list = []
            for url in urls:
                image_blocks.append(
                    ImageBlock(
                        type="image",
                        source=URLSource(
                            type="url",
                            url=url,
                        ),
                    ),
                )
            return ToolResponse(
                content=image_blocks,
            )
        elif response_format == "b64_json":
            image_datas = [_.b64_json for _ in response.data]
            image_blocks = []
            for image_data in image_datas:
                image_blocks.append(
                    ImageBlock(
                        type="image",
                        source=Base64Source(
                            type="base64",
                            media_type="image/png",
                            data=image_data,
                        ),
                    ),
                )
            return ToolResponse(
                content=image_blocks,
            )
    except Exception as e:
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=f"Failed to generate image: {str(e)}",
                ),
            ],
        )


def openai_create_image_variation(
    image_url: str,
    api_key: str,
    n: int = 1,
    model: Literal["dall-e-2"] = "dall-e-2",
    size: Literal[
        "256x256",
        "512x512",
        "1024x1024",
    ] = "256x256",
    response_format: Literal["url", "b64_json"] = "url",
) -> ToolResponse:
    """
    Create variations of an image and return the image URL(s) or base64 data.

    Args:
        image_url (`str`):
            The file path or URL to the image from which variations will be
            generated.
        api_key (`str`):
            The API key for the OpenAI API.
        n (`int`, defaults to `1`):
            The number of image variations to generate.
        model (` Literal["dall-e-2"]`, default to `dall-e-2`):
            The model to use for image variation.
        size (`Literal["256x256", "512x512", "1024x1024"]`, defaults to \
        `"256x256"`):
            The size of the generated image variations.
        response_format (`Literal["url", "b64_json"]`, defaults to `"url"`):
            The format in which generated images are returned.

            - Must be one of url or b64_json.
            - URLs are only valid for 60 minutes after the image has been
              generated.

    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.
    """

    # _parse_url handles both local and web URLs and returns BytesIO
    image = _parse_url(image_url)
    try:
        import openai

        client = openai.OpenAI(
            api_key=api_key,
        )
        response = client.images.create_variation(
            model=model,
            image=image,
            n=n,
            size=size,
        )
        image_blocks: list = []
        if response_format == "url":
            urls = [_.url for _ in response.data]
            for url in urls:
                image_blocks.append(
                    ImageBlock(
                        type="image",
                        source=URLSource(
                            type="url",
                            url=url,
                        ),
                    ),
                )
        else:
            image_datas = [_.b64_json for _ in response.data]
            for image_data in image_datas:
                image_blocks.append(
                    ImageBlock(
                        type="image",
                        source=Base64Source(
                            type="base64",
                            media_type="image/png",
                            data=image_data,
                        ),
                    ),
                )
        return ToolResponse(
            content=image_blocks,
        )
    except Exception as e:
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=f"Failed to generate image: {str(e)}",
                ),
            ],
        )


def openai_image_to_text(
    image_urls: str | list[str],
    api_key: str,
    prompt: str = "Describe the image",
    model: str = "gpt-4o",
) -> ToolResponse:
    """
    Generate descriptive text for given image(s) using a specified model, and
    return the generated text.

    Args:
        image_urls (`str | list[str]`):
            The URL or list of URLs pointing to the images that need to be
            described.
        api_key (`str`):
            The API key for the OpenAI API.
        prompt (`str`, defaults to `"Describe the image"`):
            The prompt that instructs the model on how to describe
            the image(s).
        model (`str`, defaults to `"gpt-4o"`):
            The model to use for generating the text descriptions.

    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.

    """
    if isinstance(image_urls, str):
        image_urls = [image_urls]

    content = []
    for url in image_urls:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": _to_openai_image_url(url),
                },
            },
        )
    content.append(
        {
            "type": "text",
            "text": prompt,
        },
    )
    messages = [
        {
            "role": "user",
            "content": content,
        },
    ]

    try:
        import openai

        client = openai.OpenAI(
            api_key=api_key,
        )
        response = client.chat.completions.create(
            messages=messages,
            model=model,
        )
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=response.choices[0].message.content,
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


def openai_text_to_audio(
    text: str,
    api_key: str,
    model: Literal["tts-1", "tts-1-hd", "gpt-4o-mini-tts"] = "tts-1",
    voice: Literal[
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
    ] = "alloy",
    speed: float = 1.0,
    res_format: Literal[
        "mp3",
        "opus",
        "aac",
        "flac",
        "wav",
        "pcm",
    ] = "mp3",
) -> ToolResponse:
    """
    Convert text to an audio file using a specified model and voice.

    Args:
        text (`str`):
            The text to convert to audio.
        api_key (`str`):
            The API key for the OpenAI API.
        model (`Literal["tts-1", "tts-1-hd"]`, defaults to `"tts-1"`):
            The model to use for text-to-speech conversion.
        voice (`Literal["alloy", "echo", "fable", "onyx", "nova", \
        "shimmer"]`, defaults to `"alloy"`):
            The voice to use for the audio output.
        speed (`float`, defaults to `1.0`):
            The speed of the audio playback. A value of 1.0 is normal speed.
        res_format (`Literal["mp3", "wav", "opus", "aac", "flac", \
        "wav", "pcm"]`, defaults to `"mp3"`):
            The format of the audio file.

    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.
    """

    try:
        import openai

        client = openai.OpenAI(
            api_key=api_key,
        )
        response = client.audio.speech.create(
            model=model,
            voice=voice,
            speed=speed,
            input=text,
            response_format=res_format,
        )
        audio_bytes = response.content
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        return ToolResponse(
            [
                AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        media_type=f"audio/{res_format}",
                        data=audio_base64,
                    ),
                ),
            ],
        )
    except Exception as e:
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=f"Error: Failed to generate audio. {str(e)}",
                ),
            ],
        )


def openai_audio_to_text(
    audio_file_url: str,
    api_key: str,
    language: str = "en",
    temperature: float = 0.2,
) -> ToolResponse:
    """
    Convert an audio file to text using OpenAI's transcription service.

    Args:
        audio_file_url (`str`):
            The file path or URL to the audio file that needs to be
            transcribed.
        api_key (`str`):
            The API key for the OpenAI API.
        language (`str`, defaults to `"en"`):
            The language of the input audio in
            `ISO-639-1 format \
            <https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes>`_
            (e.g., "en", "zh", "fr"). Improves accuracy and latency.
        temperature (`float`, defaults to `0.2`):
            The temperature for the transcription, which affects the
            randomness of the output.

    Returns:
        `ToolResponse`:
            A ToolResponse containing the generated content
            (ImageBlock/TextBlock/AudioBlock) or error information if the
            operation failed.
    """

    try:
        import openai

        client = openai.OpenAI(
            api_key=api_key,
        )

        if audio_file_url.startswith(("http://", "https://")):
            response = requests.get(audio_file_url)
            response.raise_for_status()
            audio_buffer = BytesIO(response.content)
            import urllib.parse
            from pathlib import Path

            parsed_url = urllib.parse.urlparse(audio_file_url)
            filename = Path(parsed_url.path).name or "audio.mp3"
            audio_buffer.name = filename
            audio_file = audio_buffer
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=language,
                temperature=temperature,
            )
        else:
            if not os.path.exists(audio_file_url):
                raise FileNotFoundError(f"File not found: {audio_file_url}")
            with open(audio_file_url, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=language,
                    temperature=temperature,
                )
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=transcription.text,
                ),
            ],
        )
    except Exception as e:
        return ToolResponse(
            [
                TextBlock(
                    type="text",
                    text=f"Error: Failed to transcribe audio: {str(e)}",
                ),
            ],
        )
