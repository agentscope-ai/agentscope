# -*- coding: utf-8 -*-
"""The OpenAI token counting class. The token calculation of vision models
follows
https://platform.openai.com/docs/guides/images-vision?api-mode=chat#calculating-costs
"""
import base64
import io
import json
import math
from http import HTTPStatus
from typing import Any

import requests

from ._token_base import TokenCounterBase


def _calculate_tokens_for_high_quality_image(
    base_tokens: int,
    tile_tokens: int,
    width: int,
    height: int,
) -> int:
    """Calculate the number of tokens for a high-quality image, which follows
    https://platform.openai.com/docs/guides/images-vision?api-mode=chat#calculating-costs
    """
    # Step1: scale to fit within a 2048x2048 box
    if width > 2048 or height > 2048:
        ratio = min(2048 / width, 2048 / height)
        width = int(width * ratio)
        height = int(height * ratio)

    # Step2: Scale to make the shortest side 768 pixels
    shortest_side = min(width, height)
    if shortest_side != 768:
        ratio = 768 / shortest_side
        width = int(width * ratio)
        height = int(height * ratio)

    # Step3: Calculate how many 512px tiles are needed
    tiles_width = (width + 511) // 512
    tiles_height = (height + 511) // 512
    total_tiles = tiles_width * tiles_height

    # Step4: Calculate the total tokens
    total_tokens = (total_tiles * tile_tokens) + base_tokens

    return total_tokens


def _get_size_of_image_url(url: str) -> tuple[int, int]:
    """Get the size of an image from the given URL.

    Args:
        url (`str`):
            A web URL or base64 encoded image URL.

    Returns:
        `tuple[int, int]`:
            A tuple containing the width and height of the image.
    """
    if url.startswith("data:image/"):
        base64_data = url.split("base64,")[1]
        image_data = base64.b64decode(base64_data)

    else:
        response = None
        for _ in range(3):
            response = requests.get(url)
            if response.status_code == HTTPStatus.OK:
                break
        response.raise_for_status()
        image_data = response.content

    from PIL import Image

    image = Image.open(io.BytesIO(image_data))
    width, height = image.size
    return width, height


def _get_base_and_tile_tokens(model_name: str) -> tuple[int, int]:
    """Get the base and tile tokens for the given OpenAI model.

    Args:
        model_name (`str`):
            The name of the model.

    Returns:
        `tuple[int, int]`:
            A tuple containing the base tokens and tile tokens.
    """
    if any(
        model_name.startswith(_)
        for _ in [
            "gpt-4o",
            "gpt-4.1",
            "gpt-4.5",
        ]
    ):
        return 85, 170

    if any(
        model_name.startswith(_)
        for _ in [
            "o1",
            "o1-pro",
            "o3",
        ]
    ):
        return 75, 150

    if model_name.startswith("4o-mini"):
        return 2833, 5667

    raise ValueError(
        f"Unsupported OpenAI model {model_name} for token counting. ",
    )


def _calculate_tokens_for_tools(
    model_name: str,
    tools: list[dict],
    encoding: Any,
) -> int:
    """Calculate the tokens for the given tools JSON schema, which follows the
    OpenAI cookbook
    https://github.com/openai/openai-cookbook/blob/6dfb7920b59a45291f7df4ea41338d1faf9ef1e8/examples/How_to_count_tokens_with_tiktoken.ipynb
    """
    if not tools:
        return 0

    func_init = 10
    prop_init = 3
    prop_key = 3
    enum_init = -3
    enum_item = 3
    func_end = 12

    if model_name.startswith("gpt-4o"):
        func_init = 7

    func_token_count = 0
    for f in tools:
        func_token_count += func_init
        function = f["function"]
        f_name = function["name"]
        f_desc = function.get("description", "").removesuffix(".")
        func_token_count += len(encoding.encode(f"{f_name}:{f_desc}"))

        properties = function["parameters"]["properties"]

        if len(properties) > 0:
            func_token_count += prop_init
            for key in properties.keys():
                func_token_count += prop_key
                p_name = key
                p_type = properties[key]["type"]
                p_desc = (
                    properties[key].get("description", "").removesuffix(".")
                )

                if "enum" in properties[key].keys():
                    func_token_count += enum_init
                    for item in properties[key]["enum"]:
                        func_token_count += enum_item
                        func_token_count += len(encoding.encode(item))

                func_token_count += len(
                    encoding.encode(f"{p_name}:{p_type}:{p_desc}"),
                )
    func_token_count += func_end

    return func_token_count


def _count_content_tokens_for_openai_vision_model(
    model_name: str,
    content: list[dict],
    encoding: Any,
) -> int:
    """Yield the number of tokens for the content of an OpenAI vision model.
    Implemented according to https://platform.openai.com/docs/guides/vision.

    Args:
        model_name (`str`):
            The name of the model.
        content (`list[dict]`):
            A list of dictionaries.
        encoding (`Any`):
            The encoding object.

    Example:
        .. code-block:: python

            _yield_tokens_for_openai_vision_model(
                [
                    {
                        "type": "text",
                        "text": "xxx",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "xxx",
                            "detail": "auto",
                        }
                    },
                    # ...
                ]
            )

    Returns:
        `Generator[int, None, None]`: Generate the number of tokens in a
        generator.
    """

    num_tokens = 0
    for item in content:
        assert isinstance(item, dict), (
            "The content field should be a list of dictionaries, but got "
            f"{type(item)}."
        )

        typ = item.get("type", None)
        if typ == "text":
            num_tokens += len(
                encoding.encode(item["text"]),
            )

        elif typ == "image_url":
            width, height = _get_size_of_image_url(item["image_url"]["url"])

            # Different counting logic for different models
            if any(
                model_name.startswith(_)
                for _ in [
                    "gpt-4.1-mini",
                    "gpt-4.1-nano",
                    "o4-mini",
                ]
            ):
                patches = min(
                    math.ceil(width / 32) * math.ceil(height / 32),
                    1536,
                )
                if model_name.startswith("gpt-4.1-mini"):
                    num_tokens += math.ceil(patches * 1.62)

                elif model_name.startswith("gpt-4.1-nano"):
                    num_tokens += math.ceil(patches * 2.46)

                else:
                    num_tokens += math.ceil(patches * 1.72)

            elif any(
                model_name.startswith(_)
                for _ in [
                    "gpt-4o",
                    "gpt-4.1",
                    "gpt-4o-mini",
                    "o",
                ]
            ):
                base_tokens, tile_tokens = _get_base_and_tile_tokens(
                    model_name,
                )

                # By default, we use high here to avoid undercounting tokens
                detail = item.get("image_url").get("detail", "high")
                if detail == "low":
                    num_tokens += base_tokens

                elif detail in ["auto", "high"]:
                    num_tokens += _calculate_tokens_for_high_quality_image(
                        base_tokens,
                        tile_tokens,
                        width,
                        height,
                    )

                else:
                    raise ValueError(
                        f"Unsupported image detail {detail}, expected "
                        f"one of ['low', 'auto', 'high'].",
                    )

        else:
            raise ValueError(
                "The type field currently only supports 'text' "
                f"and 'image_url', but got {typ}.",
            )

    return num_tokens


class OpenAITokenCounter(TokenCounterBase):
    """The OpenAI token counting class."""

    def __init__(self, model_name: str) -> None:
        """Initialize the OpenAI token counter.

        Args:
            model_name (`str`):
                The name of the OpenAI model to use for token counting.
        """
        self.model_name = model_name

    async def count(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] = None,
        **kwargs: Any,
    ) -> int:
        """Count the token numbers of the given messages.

        .. note:: OpenAI hasn't provided an official guide for counting tokens
         with tools. If you have any ideas, please open an issue on
         our GitHub repository.

        Args:
            messages (`list[dict[str, Any]]`):
                A list of dictionaries, where `role` and `content` fields are
                required.
            tools (`list[dict]`, defaults to `None`):
        """
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(self.model_name)
        except KeyError:
            encoding = tiktoken.get_encoding("o200k_base")

        tokens_per_message = 3
        tokens_per_name = 1

        # every reply is primed with <|start|>assistant<|message|>
        num_tokens = 3
        for message in messages:
            num_tokens += tokens_per_message
            for key, value in message.items():
                # Considering vision models
                if key == "content" and isinstance(value, list):
                    num_tokens += (
                        _count_content_tokens_for_openai_vision_model(
                            self.model_name,
                            value,
                            encoding,
                        )
                    )

                elif isinstance(value, str):
                    num_tokens += len(encoding.encode(value))

                elif value is None:
                    continue

                elif key == "tool_calls":
                    # TODO: This is only a temporary solution, since OpenAI
                    # hasn't provided an official guide for counting tokens
                    # with tool results.
                    num_tokens += len(
                        encoding.encode(
                            json.dumps(value, ensure_ascii=False),
                        ),
                    )

                else:
                    raise TypeError(
                        f"Invalid type {type(value)} in the {key} field: "
                        f"{value}",
                    )

                if key == "name":
                    num_tokens += tokens_per_name

        if tools:
            num_tokens += _calculate_tokens_for_tools(
                self.model_name,
                tools,
                encoding,
            )

        return num_tokens
