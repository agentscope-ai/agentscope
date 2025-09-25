# -*- coding: utf-8 -*-
"""
Wrap OpenAI API calls as services. Refer the official OpenAI API documentation
for more details.
https://platform.openai.com/docs/overview
"""

from io import BytesIO
import os
import re
from urllib.parse import urlparse, unquote
from typing import Literal, Optional, Union, Sequence
import requests


from ..service_response import (
    ServiceResponse,
    ServiceExecStatus,
)
from ...model.openai_model import (
    OpenAIDALLEWrapper,
    OpenAIChatWrapper,
)
from ...utils.common import _download_file
from ...message import Msg


def _url_to_filename(url: str) -> str:
    """Clean the URL to remove special characters.
    including /, \\, etc.
    remove spaces and replace with _.
    find the last part of the url
    make sure the name is not too long. length <= 15
    """
    parsed = urlparse(unquote(url))
    last_part = os.path.basename(parsed.path)
    # If there's no path, use the last part of the netloc (domain)
    if not last_part and parsed.netloc:
        last_part = parsed.netloc.split(".")[-2]
    last_part = os.path.splitext(last_part)[0]

    cleaned = re.sub(r"[^\w\s-]", "", last_part)
    cleaned = re.sub(r"\s+", "_", cleaned)
    if len(cleaned) > 15:
        return cleaned[:15]
    return cleaned[:15]


def _handle_openai_img_response(
    raw_response: dict,
    save_dir: Optional[str] = None,
) -> Union[str, Sequence[str]]:
    """Handle the response from OpenAI image generation API."""
    if "data" not in raw_response:
        if "error" in raw_response:
            error_msg = raw_response["error"]["message"]
        else:
            error_msg = raw_response
        raise ValueError(f"Error in OpenAI API call:\n{error_msg}")

    images = raw_response["data"]
    urls = [_["url"] for _ in images]
    if save_dir is not None:
        os.makedirs(save_dir, exist_ok=True)
        urls_local = []
        for url in urls:
            image_name = _url_to_filename(url)
            image_path = os.path.abspath(
                os.path.join(save_dir, image_name),
            )
            image_path = image_path + ".png"
            _download_file(url, image_path)
            urls_local.append(image_path)
        return urls_local
    else:
        return urls


def _parse_url(url: str) -> BytesIO:
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
        with open(os.path.abspath(url), "rb") as file:
            return BytesIO(file.read())


def _audio_filename(text: str) -> str:
    pattern = r"[^\w.,]+"
    cleaned = re.sub(pattern, " ", text)
    cleaned = re.sub(r"\s+", "_", cleaned)
    if len(cleaned) > 15:
        cleaned = cleaned[:15]
    return cleaned


def openai_text_to_image(
    prompt: str,
    api_key: str,
    n: int = 1,
    model: Literal["dall-e-2", "dall-e-3"] = "dall-e-2",
    size: Literal[
        "256x256",
        "512x512",
        "1024x1024",
        "1792x1024",
        "1024x1792",
    ] = "256x256",
    quality: Literal["standard", "hd"] = "standard",
    style: Literal["vivid", "natural"] = "vivid",
    save_dir: Optional[str] = None,
) -> ServiceResponse:
    """
    Generate image(s) based on the given prompt, and return image URL(s) or
    save them locally.

    Args:
        prompt (`str`):
            The text prompt to generate images.
        api_key (`str`):
            The API key for the OpenAI API.
        n (`int`, defaults to `1`):
            The number of images to generate.
        model (`Literal["dall-e-2", "dall-e-3"]`, defaults to `"dall-e-2"`):
            The model to use for image generation.
        size (`Literal["256x256", "512x512", "1024x1024", "1792x1024",
        "1024x1792"]`, defaults to `"256x256"`):
            The size of the generated image(s).
        quality (`Literal["standard", "hdr"]`, defaults to `"standard`):
            The quality of the generated images.
        style (`Literal["vivid", "natural"]]`, defaults to `"vivid`):
            The style of the generated images.
        save_dir (`Optional[str]`, defaults to `None`):
            The directory to save the generated images. If not specified, will
            return the web URLs.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and `content`.
            If `status` is `ServiceExecStatus.SUCCESS`,
            the `content` is a dict with key 'image_urls' and
            value is a list of the paths to the generated images or URLs.

    Example:

        .. code-block:: python

            prompt = "A futuristic city skyline at sunset"
            print(openai_text_to_image(prompt, "{api_key}"))

        > {
        >     'status': 'SUCCESS',
        >     'content': {'image_urls': ['IMAGE_URL1', 'IMAGE_URL2']}
        > }
    """
    dalle_wrapper = OpenAIDALLEWrapper(
        config_name="text_to_image_service_call",
        model_name=model,
        api_key=api_key,
    )
    try:
        response = dalle_wrapper(
            prompt=prompt,
            n=n,
            size=size,
            quality=quality,
            style=style,
        )
        urls = response.image_urls
        if urls is None:
            return ServiceResponse(
                ServiceExecStatus.ERROR,
                "Error: Failed to generate images",
            )
        if save_dir is not None:
            if not os.path.isabs(save_dir):
                cwd = os.getcwd()
                save_dir = os.path.join(cwd, save_dir)
            os.makedirs(save_dir, exist_ok=True)
            urls_local = []
            for url in urls:
                image_name = _url_to_filename(url)
                image_path = os.path.abspath(
                    os.path.join(save_dir, image_name),
                )
                image_path = (
                    image_path
                    if image_path.endswith(".png")
                    else image_path + ".png"
                )
                _download_file(url, image_path)
                urls_local.append(image_path)
            return ServiceResponse(
                ServiceExecStatus.SUCCESS,
                {"image_urls": urls_local},
            )
        else:
            return ServiceResponse(
                ServiceExecStatus.SUCCESS,
                {"image_urls": urls},
            )
    except Exception as e:
        return ServiceResponse(
            ServiceExecStatus.ERROR,
            str(e),
        )


def openai_edit_image(
    image_url: str,
    prompt: str,
    api_key: str,
    mask_url: Optional[str] = None,
    n: int = 1,
    size: Literal[
        "256x256",
        "512x512",
        "1024x1024",
    ] = "256x256",
    save_dir: Optional[str] = None,
) -> ServiceResponse:
    """
    Edit an image based on the provided mask and prompt, and return the edited
    image URL(s) or save them locally.

    Args:
        image_url (`str`):
            The file path or URL to the image that needs editing.
        prompt (`str`):
            The text prompt describing the edits to be made to the image.
        api_key (`str`):
            The API key for the OpenAI API.
        mask_url (`Optional[str]`, defaults to `None`):
            The file path or URL to the mask image that specifies the regions
            to be edited.
        n (`int`, defaults to `1`):
            The number of edited images to generate.
        size (`Literal["256x256", "512x512", "1024x1024"]`, defaults to
        `"256x256"`):
            The size of the edited images.
        save_dir (`Optional[str]`, defaults to `None`):
            The directory to save the edited images. If not specified, will
            return the web URLs.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and `content`.
            If `status` is `ServiceExecStatus.SUCCESS`,
            the `content` is a dict with key 'image_urls' and
            value is a list of the paths to the edited images or URLs.

    Example:

        .. code-block:: python

            image_url = "/path/to/original_image.png"
            mask_url = "/path/to/mask_image.png"
            prompt = "Add a sun to the sky"
            api_key = "YOUR_API_KEY"
            print(openai_edit_image(image_url, prompt, api_key, mask_url))

        > {
        >     'status': 'SUCCESS',
        >     'content': {'image_urls': ['EDITED_IMAGE_URL1',
        'EDITED_IMAGE_URL2']}
        > }
    """
    try:
        import openai
    except ImportError as e:
        raise ImportError(
            "The `openai` library is not installed. Please install it by "
            "running `pip install openai`.",
        ) from e

    client = openai.OpenAI(api_key=api_key)
    # _parse_url handles both local and web URLs and returns BytesIO
    image = _parse_url(image_url)
    try:
        kwargs = {
            "model": "dall-e-2",
            "image": image,
            "prompt": prompt,
            "n": n,
            "size": size,
        }

        if mask_url:
            kwargs["mask"] = _parse_url(mask_url)

        response = client.images.edit(**kwargs)

        urls = _handle_openai_img_response(response.model_dump(), save_dir)
        return ServiceResponse(
            ServiceExecStatus.SUCCESS,
            {"image_urls": urls},
        )
    except Exception as e:
        return ServiceResponse(
            ServiceExecStatus.ERROR,
            str(e),
        )


def openai_create_image_variation(
    image_url: str,
    api_key: str,
    n: int = 1,
    size: Literal[
        "256x256",
        "512x512",
        "1024x1024",
    ] = "256x256",
    save_dir: Optional[str] = None,
) -> ServiceResponse:
    """
    Create variations of an image and return the image URL(s) or save them
    locally.

    Args:
        image_url (`str`):
            The file path or URL to the image from which variations will be
            generated.
        api_key (`str`):
            The API key for the OpenAI API.
        n (`int`, defaults to `1`):
            The number of image variations to generate.
        size (`Literal["256x256", "512x512", "1024x1024"]`, defaults to `
        "256x256"`):
            The size of the generated image variations.
        save_dir (`Optional[str]`, defaults to `None`):
            The directory to save the generated image variations. If not
            specified, will return the web URLs.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and `content`.
            If `status` is `ServiceExecStatus.SUCCESS`,
            the `content` is a dict with key 'image_urls' and
            value is a list of the paths to the generated images or URLs.

    Example:

        .. code-block:: python

            image_url = "/path/to/image.png"
            api_key = "YOUR_API_KEY"
            print(openai_create_image_variation(image_url, api_key))

        > {
        >     'status': 'SUCCESS',
        >     'content': {'image_urls': ['VARIATION_URL1', 'VARIATION_URL2']}
        > }
    """
    try:
        import openai
    except ImportError as e:
        raise ImportError(
            "The `openai` library is not installed. Please install it by "
            "running `pip install openai`.",
        ) from e

    client = openai.OpenAI(api_key=api_key)
    # _parse_url handles both local and web URLs and returns BytesIO
    image = _parse_url(image_url)
    try:
        response = client.images.create_variation(
            model="dall-e-2",
            image=image,
            n=n,
            size=size,
        )
        urls = _handle_openai_img_response(response.model_dump(), save_dir)
        return ServiceResponse(
            ServiceExecStatus.SUCCESS,
            {"image_urls": urls},
        )
    except Exception as e:
        return ServiceResponse(
            ServiceExecStatus.ERROR,
            str(e),
        )


def openai_image_to_text(
    image_urls: Union[str, list[str]],
    api_key: str,
    prompt: str = "Describe the image",
    model: Literal["gpt-4o", "gpt-4-turbo"] = "gpt-4o",
) -> ServiceResponse:
    """
    Generate descriptive text for given image(s) using a specified model, and
    return the generated text.

    Args:
        image_urls (`Union[str, list[str]]`):
            The URL or list of URLs pointing to the images that need to be
            described.
        api_key (`str`):
            The API key for the OpenAI API.
        prompt (`str`, defaults to `"Describe the image"`):
            The prompt that instructs the model on how to describe
            the image(s).
        model (`Literal["gpt-4o", "gpt-4-turbo"]`, defaults to `"gpt-4o"`):
            The model to use for generating the text descriptions.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and `content`.
            If `status` is `ServiceExecStatus.SUCCESS`,
            the `content` contains the generated text description(s).

    Example:

        .. code-block:: python

            image_url = "https://example.com/image.jpg"
            api_key = "YOUR_API_KEY"
            print(openai_image_to_text(image_url, api_key))

        > {
        >     'status': 'SUCCESS',
        >     'content': "A detailed description of the image..."
        > }
    """
    openai_chat_wrapper = OpenAIChatWrapper(
        config_name="image_to_text_service_call",
        model_name=model,
        api_key=api_key,
    )
    messages = Msg(
        name="service_call",
        role="user",
        content=prompt,
        url=image_urls,
    )
    openai_messages = openai_chat_wrapper.format(messages)
    try:
        response = openai_chat_wrapper(openai_messages)
        return ServiceResponse(ServiceExecStatus.SUCCESS, response.text)
    except Exception as e:
        return ServiceResponse(ServiceExecStatus.ERROR, str(e))


def openai_text_to_audio(
    text: str,
    api_key: str,
    save_dir: str = "",
    model: Literal["tts-1", "tts-1-hd"] = "tts-1",
    voice: Literal[
        "alloy",
        "echo",
        "fable",
        "onyx",
        "nova",
        "shimmer",
    ] = "alloy",
    speed: float = 1.0,
    res_format: Literal[
        "mp3",
        "wav",
        "opus",
        "aac",
        "flac",
        "wav",
        "pcm",
    ] = "mp3",
) -> ServiceResponse:
    """
    Convert text to an audio file using a specified model and voice, and save
    the audio file locally.

    Args:
        text (`str`):
            The text to convert to audio.
        api_key (`str`):
            The API key for the OpenAI API.
        save_dir (`str` defaults to `''`):
            The directory where the generated audio file will be saved.
        model (`Literal["tts-1", "tts-1-hd"]`, defaults to `"tts-1"`):
            The model to use for text-to-speech conversion.
        voice (`Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"]`,
        defaults to `"alloy"`):
            The voice to use for the audio output.
        speed (`float`, defaults to `1.0`):
            The speed of the audio playback. A value of 1.0 is normal speed.
        res_format (`Literal["mp3", "wav", "opus", "aac", "flac",
        "wav", "pcm"]`,
        defaults to `"mp3"`):
            The format of the audio file.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and `content`.
            If `status` is `ServiceExecStatus.SUCCESS`,
            the `content` is a dict with key 'audio_path' and
            value is the path to the generated audio file.

    Example:

        .. code-block:: python

            text = "Hello, welcome to the text-to-speech service!"
            api_key = "YOUR_API_KEY"
            save_dir = "./audio_files"
            print(openai_text_to_audio(text, api_key, save_dir))

        > {
        >     'status': 'SUCCESS',
        >     'content': {'audio_path': './audio_files/Hello,_welco.mp3'}
        > }
    """
    try:
        import openai
    except ImportError as e:
        raise ImportError(
            "The `openai` library is not installed. Please install it by "
            "running `pip install openai`.",
        ) from e

    client = openai.OpenAI(api_key=api_key)
    save_name = _audio_filename(text)
    if os.path.isabs(save_dir):
        save_path = os.path.join(save_dir, f"{save_name}.{res_format}")
    else:
        cwd = os.getcwd()
        save_dir = os.path.join(cwd, save_dir)
        save_path = os.path.join(save_dir, f"{save_name}.{res_format}")
    try:
        response = client.audio.speech.create(
            model=model,
            voice=voice,
            speed=speed,
            input=text,
            response_format=res_format,
        )
        response.stream_to_file(save_path)
        return ServiceResponse(
            ServiceExecStatus.SUCCESS,
            {"audio_path": save_path},
        )
        # stream_to_file method is deprecated.
        # But you still get the right output.
    except Exception as e:
        return ServiceResponse(
            ServiceExecStatus.ERROR,
            f"Error: Failed to generate audio. {str(e)}",
        )


def openai_audio_to_text(
    audio_file_url: str,
    api_key: str,
    language: str = "en",
    temperature: float = 0.2,
) -> ServiceResponse:
    """
    Convert an audio file to text using OpenAI's transcription service.

    Args:
        audio_file_url (`str`):
            The file path or URL to the audio file that needs to be
            transcribed.
        api_key (`str`):
            The API key for the OpenAI API.
        language (`str`, defaults to `"en"`):
            The language of the input audio. Supplying the input language in
            [ISO-639-1](https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes)
            format will improve accuracy and latency.
        temperature (`float`, defaults to `0.2`):
            The temperature for the transcription, which affects the
            randomness of the output.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and `content`.
            If `status` is `ServiceExecStatus.SUCCESS`,
            the `content` contains a dictionary with key 'transcription' and
            value as the transcribed text.

    Example:

        .. code-block:: python

            audio_file_url = "/path/to/audio.mp3"
            api_key = "YOUR_API_KEY"
            print(openai_audio_to_text(audio_file_url, api_key))

        > {
        >     'status': 'SUCCESS',
        >     'content': {'transcription': 'This is the transcribed text from
        the audio file.'}
        > }
    """
    try:
        import openai
    except ImportError as e:
        raise ImportError(
            "The `openai` library is not installed. Please install it by "
            "running `pip install openai`.",
        ) from e

    client = openai.OpenAI(api_key=api_key)
    audio_file_url = os.path.abspath(audio_file_url)
    with open(audio_file_url, "rb") as audio_file:
        try:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=language,
                temperature=temperature,
            )
            return ServiceResponse(
                ServiceExecStatus.SUCCESS,
                {"transcription": transcription.text},
            )
        except Exception as e:
            return ServiceResponse(
                ServiceExecStatus.ERROR,
                f"Error: Failed to transcribe audio {str(e)}",
            )
