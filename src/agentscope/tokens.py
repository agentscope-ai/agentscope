# -*- coding: utf-8 -*-
"""The tokens interface for agentscope."""
import os
from http import HTTPStatus
from typing import Callable, Union, Optional, Any

from loguru import logger


__register_models = {}
# The dictionary to store the model names and token counting functions.
# TODO: a more elegant way to store the model names and functions.


def count(model_name: str, messages: list[dict[str, str]]) -> int:
    """Count the number of tokens for the given model and messages.

    Args:
        model_name (`str`):
            The name of the model.
        messages (`list[dict[str, str]]`):
            A list of dictionaries.
    """
    # Type checking
    if not isinstance(model_name, str):
        raise TypeError(
            f"Expected model_name to be a string, but got {type(model_name)}.",
        )
    if not isinstance(messages, list):
        raise TypeError(
            f"Expected messages to be a list, but got {type(messages)}.",
        )
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            raise TypeError(
                f"Expected messages[{i}] to be a dict, but got "
                f"{type(message)}.",
            )

    # Counting tokens according to the model name
    # Register model
    if model_name in __register_models:
        return __register_models[model_name](model_name, messages)

    # OpenAI
    elif model_name.startswith("gpt-"):
        return count_openai_tokens(model_name, messages)

    # Gemini
    elif model_name.startswith("gemini-"):
        return count_gemini_tokens(model_name, messages)

    # Dashscope
    elif model_name.startswith("qwen-"):
        return count_dashscope_tokens(model_name, messages)

    else:
        raise ValueError(
            f"Unsupported model {model_name} for token counting. "
            "Please register the model with the corresponding token counting "
            "function by "
            "`agentscope.tokens.register_model(model_name, token_count_func)`",
        )


def _count_content_tokens_for_openai_vision_model(
    content: list[dict],
    encoding: Any,
) -> int:
    """Yield the number of tokens for the content of an OpenAI vision model.
    Implemented according to https://platform.openai.com/docs/guides/vision.

    Args:
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
        if not isinstance(item, dict):
            raise TypeError(
                "If you're using a vision model for OpenAI model,"
                "The content field should be a list of "
                f"dictionaries, but got {type(item)}.",
            )

        typ = item.get("type", None)
        if typ == "text":
            num_tokens += len(encoding.encode(item["text"]))

        elif typ == "image_url":
            # By default, we use high here to avoid undercounting tokens
            detail = item.get("image_url").get("detail", "high")
            if detail == "low":
                num_tokens += 85
            elif detail in ["auto", "high"]:
                num_tokens += 170
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


def count_openai_tokens(  # pylint: disable=too-many-branches
    model_name: str,
    messages: list[dict[str, str]],
) -> int:
    """Count the number of tokens for the given OpenAI Chat model and
    messages.

    Refer to https://platform.openai.com/docs/advanced-usage/managing-tokens

    Args:
        model_name (`str`):
            The name of the OpenAI Chat model, e.g. "gpt-4o".
        messages (`list[dict[str, str]]`):
            A list of dictionaries. Each dictionary should have the keys
            of "role" and "content", and an optional key of "name". For vision
            LLMs, the value of "content" should be a list of dictionaries.
    """
    import tiktoken

    try:
        encoding = tiktoken.encoding_for_model(model_name)
    except KeyError:
        encoding = tiktoken.get_encoding("o200k_base")

    if model_name in {
        "gpt-3.5-turbo-0125",
        "gpt-4-0314",
        "gpt-4-32k-0314",
        "gpt-4-0613",
        "gpt-4-32k-0613",
        "gpt-4o-mini-2024-07-18",
        "gpt-4o-2024-08-06",
    }:
        tokens_per_message = 3
        tokens_per_name = 1
    elif "gpt-3.5-turbo" in model_name:
        return count_openai_tokens(
            model_name="gpt-3.5-turbo-0125",
            messages=messages,
        )
    elif "gpt-4o-mini" in model_name:
        return count_openai_tokens(
            model_name="gpt-4o-mini-2024-07-18",
            messages=messages,
        )
    elif "gpt-4o" in model_name:
        return count_openai_tokens(
            model_name="gpt-4o-2024-08-06",
            messages=messages,
        )
    elif "gpt-4" in model_name:
        return count_openai_tokens(model_name="gpt-4-0613", messages=messages)
    else:
        raise NotImplementedError(
            f"count_openai_tokens() is not implemented for "
            f"model {model_name}.",
        )

    num_tokens = 3  # every reply is primed with <|start|>assistant<|message|>
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            # Considering vision model
            if key == "content" and isinstance(value, list):
                num_tokens += _count_content_tokens_for_openai_vision_model(
                    value,
                    encoding,
                )

            elif isinstance(value, str):
                num_tokens += len(encoding.encode(value))

            else:
                raise TypeError(
                    f"Invalid type {type(value)} in the {key} field.",
                )

            if key == "name":
                num_tokens += tokens_per_name

    return num_tokens


def count_gemini_tokens(
    model_name: str,
    messages: list[dict[str, str]],
) -> int:
    """Count the number of tokens for the given Gemini model and messages.

    Args:
        model_name (`str`):
            The name of the Gemini model, e.g. "gemini-1.5-pro".
        messages (`list[dict[str, str]]`):
    """
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise ImportError(
            "The package `google.generativeai` is required for token counting "
            "for Gemini model. Install it with "
            "`pip install -q -U google-generativeai` and refer to "
            "https://ai.google.dev/gemini-api/docs/get-started/"
            "tutorial?lang=python for details.",
        ) from exc

    model = genai.GenerativeModel(model_name)
    tokens_count = model.count_tokens(messages).total_tokens
    return tokens_count


def count_dashscope_tokens(
    model_name: str,
    messages: list[dict[str, str]],
    api_key: Optional[str] = None,
) -> int:
    """Count the number of tokens for the given Dashscope model and messages.

    Note this function will call the Dashscope API to count the tokens.
    Refer to
    https://help.aliyun.com/zh/dashscope/developer-reference/token-api?spm=5176.28197632.console-base_help.dexternal.1c407e06Y2bQVB&disableWebsiteRedirect=true
    for more details.

    Args:
        model_name (`str`):
            The name of the Dashscope model, e.g. "qwen-max".
        messages (`list[dict[str, str]]`):
            The list of messages, each message is a dict with the key 'text'.
        api_key (`Optional[str]`, defaults to `None`):
            The API key for Dashscope. If `None`, the API key will be read
            from the environment variable `DASHSCOPE_API_KEY`.

    Returns:
        `int`: The number of tokens.
    """
    try:
        import dashscope
    except ImportError as exc:
        raise ImportError(
            "The package `dashscope` is required for token counting "
            "for Dashscope model.",
        ) from exc

    response = dashscope.Tokenization.call(
        model=model_name,
        messages=messages,
        api_key=api_key or os.environ.get("DASHSCOPE_API_KEY"),
    )

    if response.status_code != HTTPStatus.OK:
        raise RuntimeError({**response})

    return response.usage["input_tokens"]


def supported_models() -> list[str]:
    """Get the list of supported model for token counting."""
    infos = [
        "Supported model for token counting: ",
        " 1. OpenAI Chat model (starting with 'gpt-') ",
        " 2. Gemini model (starting with 'gemini-') ",
        " 3. Dashscope model (starting with 'qwen-') ",
        f" 4. Registered model: {', '.join(__register_models.keys())} ",
    ]
    for info in infos:
        logger.info(info)

    return ["gpt-.*", "gemini-.*", "qwen-.*"] + list(__register_models.keys())


def register_model(
    model_name: Union[str, list[str]],
    tokens_count_func: Callable[[str, list[dict[str, str]]], int],
) -> None:
    """Register a tokens counting function for the model(s) with the given
    name(s). If the model name is conflicting with the existing one, the
    new function will override the existing one.

    Args:
        model_name (`Union[str, list[str]]`):
            The name of the model or a list of model names.
        tokens_count_func (`Callable[[str, list[dict[str, str]]], int]`):
            The tokens counting function for the model, which takes the model
            name and a list of dictionary messages as input and returns the
            number of tokens.
    """
    if isinstance(model_name, str):
        model_name = [model_name]

    for name in model_name:
        if name in __register_models:
            logger.warning(
                f"Overriding the existing token counting function for model "
                f"named {name}.",
            )
        __register_models[name] = tokens_count_func

    logger.info(
        f"Successfully registered token counting function for model: "
        f"{', '.join(model_name)}.",
    )


def count_huggingface_tokens(
    pretrained_model_name_or_path: str,
    messages: list[dict[str, str]],
    use_fast: bool = False,
    trust_remote_code: bool = False,
    enable_mirror: bool = False,
) -> int:
    """Count the number of tokens for the given HuggingFace model and messages.

    Args:
        pretrained_model_name_or_path (`str`):
            The model name of path used in `AutoTokenizer.from_pretrained`.
        messages (`list[dict[str, str]]`):
            The list of messages, each message is a dictionary with keys "role"
            and "content".
        use_fast (`bool`, defaults to `False`):
            Whether to use the fast tokenizer when loading the tokenizer.
        trust_remote_code (`bool`, defaults to `False`):
            Whether to trust the remote code in transformers'
            `AutoTokenizer.from_pretrained` API.
        enable_mirror (`bool`, defaults to `False`):
            Whether to enable the HuggingFace mirror, which is useful for
            users in China.

    Returns:
        `int`: The number of tokens.
    """
    if enable_mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise ImportError(
            "The package `transformers` is required for downloading tokenizer",
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(
        pretrained_model_name_or_path,
        use_fast=use_fast,
        trust_remote_code=trust_remote_code,
    )

    if tokenizer.chat_template is None:
        raise ValueError(
            f"The tokenizer for model {pretrained_model_name_or_path} in "
            f"transformers does not have chat template.",
        )

    tokenized_msgs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=False,
        tokenize=True,
        return_tensors="np",
    )[0]

    return len(tokenized_msgs)
