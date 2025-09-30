# -*- coding: utf-8 -*-
"""Token utils."""
from typing import Union
from loguru import logger

try:
    import tiktoken
except ImportError:
    tiktoken = None

# TODO: obtain from web API and store it in `~/.cache`
OPENAI_MAX_LENGTH = {
    "update": 20231212,
    # gpt-4
    "gpt-4-1106-preview": 128000,
    "gpt-4-vision-preview": 128000,
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-0613": 8192,
    "gpt-4-32k-0613": 32768,
    "gpt-4-0314": 8192,  # legacy
    "gpt-4-32k-0314": 32768,  # legacy
    # gpt-3.5
    "gpt-3.5-turbo-1106": 16385,
    "gpt-3.5-turbo": 4096,
    "gpt-3.5-turbo-16k": 16385,
    "gpt-3.5-turbo-instruct": 4096,
    "gpt-3.5-turbo-0613": 4096,  # legacy
    "gpt-3.5-turbo-16k-0613": 16385,  # deprecated on June 13th 2024
    "gpt-3.5-turbo-0301": 4096,  # deprecated on June 13th 2024
    "text-davinci-003": 4096,  # deprecated on Jan 4th 2024
    "text-davinci-002": 4096,  # deprecated on Jan 4th 2024
    "code-davinci-002": 4096,  # deprecated on Jan 4th 2024
    # gpt-3 legacy
    "text-curie-001": 2049,
    "text-babbage-001": 2049,
    "text-ada-001": 2049,
    "davinci": 2049,
    "curie": 2049,
    "babbage": 2049,
    "ada": 2049,
}


def get_openai_max_length(model_name: str) -> int:
    """Get the max length of the OpenAi model."""
    try:
        return OPENAI_MAX_LENGTH[model_name]
    except KeyError as exc:
        raise KeyError(
            f"Model [{model_name}] not found in OPENAI_MAX_LENGTH. "
            f"The last updated date is {OPENAI_MAX_LENGTH['update']}",
        ) from exc


def count_openai_token(content: Union[str, list], model: str) -> int:
    """Count token in format of OpenAI API"""
    if isinstance(content, str):
        content = [content]
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        logger.warning(
            "Warning: model not found. Using cl100k_base encoding.",
        )
        encoding = tiktoken.get_encoding("cl100k_base")

    if model in [
        "text-davinci-003",  # deprecated on Jan 4th 2024,
        "text-davinci-002",  # deprecated on Jan 4th 2024
        "code-davinci-002",  # deprecated on Jan 4th 2024
        # gpt-3 legacy
        "text-curie-001",
        "text-babbage-001",
        "text-ada-001",
        "davinci",
        "curie",
        "babbage",
        "ada",
    ]:
        num_tokens = 0
        for message in content:
            if isinstance(message, dict):
                raise NotImplementedError(
                    f"""count_openai_token() is not implemented for
                    model {model}. See
                    https://github.com/openai/openai-python for
                    information on how messages are converted to tokens.""",
                )
            num_tokens += len(encoding.encode(message))
        return num_tokens
    return num_tokens_from_content(content, model)


def num_tokens_from_content(content: list, model: str) -> int:
    """Count token in format of OpenAI Chat API"""
    # modified from https://github.com/openai/openai-cookbook/blob/main
    # /examples/How_to_count_tokens_with_tiktoken.ipynb
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        logger.warning(
            "Warning: model not found. Using cl100k_base encoding.",
        )
        encoding = tiktoken.get_encoding("cl100k_base")

    if model in {
        "gpt-3.5-turbo-0613",
        "gpt-3.5-turbo-16k-0613",
        "gpt-4-0314",
        "gpt-4-32k-0314",
        "gpt-4-0613",
        "gpt-4-32k-0613",
    }:
        tokens_per_message = 3
        tokens_per_name = 1
    elif model == "gpt-3.5-turbo-0301":
        # every message follows <|im_start|>{role/name}\n{
        # content}<|im_end|>\n
        tokens_per_message = 4
        # if there's a name, the role is omitted
        tokens_per_name = -1
    elif "gpt-3.5-turbo" in model:
        logger.warning(
            "Warning: gpt-3.5-turbo may update over time. "
            "Returning num tokens assuming "
            "gpt-3.5-turbo-0613.",
        )
        return num_tokens_from_content(content, "gpt-3.5-turbo-0613")
    elif "gpt-4" in model:
        logger.warning(
            "Warning: gpt-4 may update over time. Returning "
            "num tokens assuming gpt-4-0613.",
        )
        return num_tokens_from_content(content, "gpt-4-0613")
    else:
        raise NotImplementedError(
            f"""num_tokens_from_content() is not implemented for model
             {model}. See
             https://github.com/openai/openai-python
             for information on how messages are converted to tokens.""",
        )
    num_tokens = 0
    for message in content:
        if isinstance(message, str):
            num_tokens += len(encoding.encode(message))
        else:
            num_tokens += tokens_per_message
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":
                    num_tokens += tokens_per_name
    # every reply is primed with <|start|>assistant<|message|>
    num_tokens += 3
    return num_tokens
