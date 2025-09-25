# -*- coding: utf-8 -*-
"""
Service for text processing
"""
from loguru import logger

from agentscope.model import ModelWrapperBase
from agentscope.service.service_status import ServiceExecStatus
from agentscope.service.service_response import ServiceResponse
from agentscope.message import Msg
from agentscope.constants import _DEFAULT_SYSTEM_PROMPT
from agentscope.constants import _DEFAULT_TOKEN_LIMIT_PROMPT


def summarization(
    model: ModelWrapperBase,
    text: str,
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    max_return_token: int = -1,
    token_limit_prompt: str = _DEFAULT_TOKEN_LIMIT_PROMPT,
) -> ServiceResponse:
    """Summarize the input text.

    Summarization function (Notice: current version of token limitation is
    built with Open AI API)

    Args:
        model (`ModelWrapperBase`):
            Model used to summarize provided text.
        text (`str`):
            Text to be summarized by the model.
        system_prompt (`str`, defaults to `_DEFAULT_SYSTEM_PROMPT`):
            Prompts as instruction for the system, will be as an instruction
            for the model.
        max_return_token (`int`, defaults to `-1`):
            Whether provide additional prompting instruction to limit the
            number of tokens in summarization returned by the model.
        token_limit_prompt (`str`, defaults to `_DEFAULT_TOKEN_LIMIT_PROMPT`):
            Prompt to instruct the model follow token limitation.

    Returns:
        `ServiceResponse`: If the model successfully summarized the text, and
        the summarization satisfies the provided token limitation, return
        `ServiceResponse` with `ServiceExecStatus.SUCCESS`; otherwise return
        `ServiceResponse` with `ServiceExecStatus.ERROR` (if the summary is
        return successfully but exceed the token limits, the content
        contains the summary as well).

    Example:

    The default message with `text` to be summarized:

    .. code-block:: python

        [
            {
                "role": "system",
                "name": "system",
                "content": "You are a helpful agent to summarize the text.\\
                You need to keep all the key information of the text in the\\
                summary."
            },
            {
                "role": "user",
                "name": "user",
                "content": text
            },
        ]

    Messages will be processed by model.format() before feeding to model.
    """
    if max_return_token > 0:
        system_prompt += token_limit_prompt.format(max_return_token)
    try:
        msgs = [
            Msg(name="system", role="system", content=system_prompt),
            Msg(name="user", role="user", content=text),
        ]
        msgs = model.format(msgs)
        model_output = model(msgs)
        summary = model_output.text
        return ServiceResponse(
            ServiceExecStatus.SUCCESS,
            content=summary,
        )
    except ValueError as e:
        logger.exception(e)
        return ServiceResponse(
            ServiceExecStatus.ERROR,
            content=f"Summarization by model {model.model} fail",
        )
