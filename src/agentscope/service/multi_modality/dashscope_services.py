# -*- coding: utf-8 -*-
"""Use DashScope API to generate images,
convert text to audio, and convert images to text.
Please refer to the official documentation for more details:
https://dashscope.aliyun.com/
"""

from typing import Union, Optional, Literal, Sequence

import os


from ..service_response import (
    ServiceResponse,
    ServiceExecStatus,
)
from ...utils.common import _download_file


def dashscope_text_to_image(
    prompt: str,
    api_key: str,
    n: int = 1,
    size: Literal["1024*1024", "720*1280", "1280*720"] = "1024*1024",
    model: str = "wanx-v1",
    save_dir: Optional[str] = None,
) -> ServiceResponse:
    """Generate image(s) based on the given prompt, and return image url(s).

    Args:
        prompt (`str`):
            The text prompt to generate image.
        api_key (`str`):
            The api key for the dashscope api.
        n (`int`, defaults to `1`):
            The number of images to generate.
        size (`Literal["1024*1024", "720*1280", "1280*720"]`, defaults to
         `"1024*1024"`):
            Size of the image.
        model (`str`, defaults to '"wanx-v1"'):
            The model to use.
        save_dir (`Optional[str]`, defaults to 'None'):
            The directory to save the generated images. If not specified,
            will return the web urls.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and`content`. If
            `status` is ServiceExecStatus.SUCCESS, the `content` is a dict
            with key 'fig_paths" and value is a list of the paths to the
            generated images.

    Example:

        .. code-block:: python

            prompt = "A beautiful sunset in the mountains"
            print(dashscope_text_to_image(prompt, "{api_key}"))

    > {
    >     'status': 'SUCCESS',
    >     'content': {'image_urls': ['IMAGE_URL1', 'IMAGE_URL2']}
    > }

    """
    from ...model import DashScopeImageSynthesisWrapper
    text2img = DashScopeImageSynthesisWrapper(
        config_name="dashscope-text-to-image-service",  # Just a placeholder
        model_name=model,
        api_key=api_key,
    )
    try:
        res = text2img(
            prompt=prompt,
            n=n,
            size=size,
        )
        urls = res.image_urls

        # save images to save_dir
        if urls is not None:
            if save_dir:
                os.makedirs(save_dir, exist_ok=True)
                urls_local = []
                # Obtain the image file names in the url
                for url in urls:
                    image_name = url.split("/")[-1]
                    image_path = os.path.abspath(
                        os.path.join(save_dir, image_name),
                    )
                    # Download the image
                    _download_file(url, image_path)
                    urls_local.append(image_path)

                return ServiceResponse(
                    ServiceExecStatus.SUCCESS,
                    {"image_urls": urls_local},
                )
            else:
                # Return the web urls
                return ServiceResponse(
                    ServiceExecStatus.SUCCESS,
                    {"image_urls": urls},
                )
        else:
            return ServiceResponse(
                ServiceExecStatus.ERROR,
                "Error: Failed to generate images",
            )
    except Exception as e:
        return ServiceResponse(
            ServiceExecStatus.ERROR,
            str(e),
        )


def dashscope_image_to_text(
    image_urls: Union[str, Sequence[str]],
    api_key: str,
    prompt: str = "Describe the image",
    model: str = "qwen-vl-plus",
) -> ServiceResponse:
    """Generate text based on the given images.

    Args:
        image_urls (`Union[str, Sequence[str]]`):
            The url of single or multiple images.
        api_key (`str`):
            The api key for the dashscope api.
        prompt (`str`, defaults to 'Describe the image' ):
            The text prompt.
        model (`str`, defaults to 'qwen-vl-plus'):
            The model to use in DashScope MultiModal API.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and`content`.
            If `status` is ServiceExecStatus.SUCCESS, the `content` is the
            generated text.

    Example:

        .. code-block:: python

            image_url = "image.jpg"
            prompt = "Describe the image"
            print(image_to_text(image_url, prompt))

    > {'status': 'SUCCESS', 'content': 'A beautiful sunset in the mountains'}

    """
    from ...model import DashScopeMultiModalWrapper
    img2text = DashScopeMultiModalWrapper(
        config_name="dashscope-image-to-text-service",  # Just a placeholder
        model_name=model,
        api_key=api_key,
    )

    if isinstance(image_urls, str):
        image_urls = [image_urls]

    # Check if the local url is valid
    img_abs_urls = []
    for url in image_urls:
        if os.path.exists(url):
            if os.path.isfile(url):
                img_abs_urls.append(os.path.abspath(url))
            else:
                return ServiceResponse(
                    ServiceExecStatus.ERROR,
                    f'Error: The input image url "{url}" is not a file.',
                )
        else:
            # Maybe a web url or an invalid url, we leave it to the API
            # to handle
            img_abs_urls.append(url)

    # Convert image paths according to the model requirements
    contents = img2text.convert_url(img_abs_urls)
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
        res = img2text(messages, stream=False)
        description = res.text
        if description is not None:
            return ServiceResponse(
                ServiceExecStatus.SUCCESS,
                description,
            )
        else:
            return ServiceResponse(
                ServiceExecStatus.ERROR,
                "Error: Failed to generate text",
            )
    except Exception as e:
        return ServiceResponse(
            ServiceExecStatus.ERROR,
            str(e),
        )


def dashscope_text_to_audio(
    text: str,
    api_key: str,
    save_dir: str,
    model: str = "sambert-zhichu-v1",
    sample_rate: int = 48000,
) -> ServiceResponse:
    """Convert the given text to audio.

    Args:
        text (`str`):
            The text to be converted into audio.
        api_key (`str`):
            The api key for the dashscope API.
        save_dir (`str`):
            The directory to save the generated audio.
        model (`str`, defaults to 'sambert-zhichu-v1'):
            The model to use. Full model list can be found in
            https://help.aliyun.com/zh/dashscope/model-list
        sample_rate (`int`, defaults to 48000):
            Sample rate of the audio.

    Returns:
        `ServiceResponse`:
            A dictionary with two variables: `status` and`content`. If
            `status` is ServiceExecStatus.SUCCESS, the `content` contains
            a dictionary with key "audio_path" and value is the path to
            the generated audio.

    Example:

        .. code-block:: python

            text = "How is the weather today?"
            print(text_to_audio(text)) gives:


    > {'status': 'SUCCESS', 'content': {"audio_path": "AUDIO_PATH"}}

    """
    try:
        import dashscope
    except ImportError as e:
        raise ImportError(
            "The package 'dashscope' is not installed. Please install it by "
            "running `pip install dashscope>=1.19.0`",
        ) from e

    dashscope.api_key = api_key

    res = dashscope.audio.tts.SpeechSynthesizer.call(
        model=model,
        text=text,
        sample_rate=sample_rate,
        format="wav",
    )

    audio_data = res.get_audio_data()

    if audio_data is not None:
        if save_dir is not None:
            os.makedirs(save_dir, exist_ok=True)

        # Save locally
        text = text[0:15] if len(text) > 15 else text
        audio_path = os.path.join(save_dir, f"{text.strip()}.wav")

        with open(audio_path, "wb") as f:
            f.write(audio_data)
        return ServiceResponse(
            ServiceExecStatus.SUCCESS,
            {"audio_path": audio_path},
        )
    else:
        return ServiceResponse(
            ServiceExecStatus.ERROR,
            "Error: Failed to generate audio",
        )
