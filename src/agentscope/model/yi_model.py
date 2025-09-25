# -*- coding: utf-8 -*-
"""Model wrapper for Yi model"""
import json
from typing import (
    List,
    Union,
    Optional,
    Generator,
)

import requests

from ._model_usage import ChatUsage
from ._model_utils import (
    _verify_text_content_in_openai_message_response,
    _verify_text_content_in_openai_delta_response,
)
from .model import ModelWrapperBase, ModelResponse
from ..formatters import CommonFormatter
from ..message import Msg


class YiChatWrapper(ModelWrapperBase):
    """The model wrapper for Yi Chat API.

    Response:
        - From https://platform.lingyiwanwu.com/docs

        .. code-block:: json

            {
                "id": "cmpl-ea89ae83",
                "object": "chat.completion",
                "created": 5785971,
                "model": "yi-large-rag",
                "usage": {
                    "completion_tokens": 113,
                    "prompt_tokens": 896,
                    "total_tokens": 1009
                },
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Today in Los Angeles, the weather ...",
                        },
                        "finish_reason": "stop"
                    }
                ]
            }

    """

    model_type: str = "yi_chat"

    def __init__(
        self,
        config_name: str,
        model_name: str,
        api_key: str,
        max_tokens: Optional[int] = None,
        top_p: float = 0.9,
        temperature: float = 0.3,
        stream: bool = False,
    ) -> None:
        """Initialize the Yi chat model wrapper.

        Args:
            config_name (`str`):
                The name of the configuration to use.
            model_name (`str`):
                The name of the model to use, e.g. yi-large, yi-medium, etc.
            api_key (`str`):
                The API key for the Yi API.
            max_tokens (`Optional[int]`, defaults to `None`):
                The maximum number of tokens to generate, defaults to `None`.
            top_p (`float`, defaults to `0.9`):
                The randomness parameters in the range [0, 1].
            temperature (`float`, defaults to `0.3`):
                The temperature parameter in the range [0, 2].
            stream (`bool`, defaults to `False`):
                Whether to stream the response or not.
        """

        super().__init__(config_name, model_name)

        if top_p > 1 or top_p < 0:
            raise ValueError(
                f"The `top_p` parameter must be in the range [0, 1], but got "
                f"{top_p} instead.",
            )

        if temperature < 0 or temperature > 2:
            raise ValueError(
                f"The `temperature` parameter must be in the range [0, 2], "
                f"but got {temperature} instead.",
            )

        self.api_key = api_key
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.temperature = temperature
        self.stream = stream

    def __call__(
        self,
        messages: list[dict],
        stream: Optional[bool] = None,
    ) -> ModelResponse:
        """Invoke the Yi Chat API by sending a list of messages."""

        # Checking messages
        if not isinstance(messages, list):
            raise ValueError(
                f"Yi `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )

        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for Yi API.",
            )

        if stream is None:
            stream = self.stream

        # Forward to generate response
        kwargs = {
            "url": "https://api.lingyiwanwu.com/v1/chat/completions",
            "json": {
                "model": self.model_name,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "top_p": self.top_p,
                "stream": stream,
            },
            "headers": {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        }

        response = requests.post(**kwargs)
        response.raise_for_status()

        if stream:

            def generator() -> Generator[str, None, None]:
                text = ""
                last_chunk = {}
                for line in response.iter_lines():
                    if line:
                        line_str = line.decode("utf-8").strip()

                        # Remove prefix "data: " if exists
                        json_str = line_str.removeprefix("data: ")

                        # The last response is "data: [DONE]"
                        if json_str == "[DONE]":
                            continue

                        try:
                            chunk = json.loads(json_str)
                            if _verify_text_content_in_openai_delta_response(
                                chunk,
                            ):
                                text += chunk["choices"][0]["delta"]["content"]
                                yield text
                            last_chunk = chunk

                        except json.decoder.JSONDecodeError as e:
                            raise json.decoder.JSONDecodeError(
                                f"Invalid JSON: {json_str}",
                                e.doc,
                                e.pos,
                            ) from e

                # In Yi Chat API, the last valid chunk will save all the text
                # in this message
                self._save_model_invocation_and_update_monitor(
                    kwargs,
                    last_chunk,
                )

            return ModelResponse(
                stream=generator(),
            )
        else:
            response = response.json()
            self._save_model_invocation_and_update_monitor(
                kwargs,
                response,
            )

            # Re-use the openai response checking function
            if _verify_text_content_in_openai_message_response(response):
                return ModelResponse(
                    text=response["choices"][0]["message"]["content"],
                    raw=response,
                )
            else:
                raise RuntimeError(
                    f"Invalid response from Yi Chat API: {response}",
                )

    def format(
        self,
        *args: Union[Msg, list[Msg], None],
        multi_agent_mode: bool = True,
    ) -> List[dict]:
        """Format the messages into the required format of Yi Chat API.

        Note this strategy maybe not suitable for all scenarios,
        and developers are encouraged to implement their own prompt
        engineering strategies.

        The following is an example:

        .. code-block:: python

            prompt1 = model.format(
                Msg("system", "You're a helpful assistant", role="system"),
                Msg("Bob", "Hi, how can I help you?", role="assistant"),
                Msg("user", "What's the date today?", role="user")
            )

        The prompt will be as follows:

        .. code-block:: python

            # prompt1
            [
                {
                    "role": "system",
                    "content": "You're a helpful assistant"
                },
                {
                    "role": "user",
                    "content": (
                        "## Conversation History\\n"
                        "Bob: Hi, how can I help you?\\n"
                        "user: What's the date today?"
                    )
                }
            ]

        Args:
            args (`Union[Msg, list[Msg], None]`):
                The input arguments to be formatted, where each argument
                should be a `Msg` object, or a list of `Msg` objects. The
                `None` input will be ignored.
            multi_agent_mode (`bool`, defaults to `True`):
                Formatting the messages in multi-agent mode or not. If false,
                the messages will be formatted in chat mode, where only a user
                and an assistant roles are involved.

        Returns:
            `List[dict]`:
                The formatted messages.
        """

        # TODO: Support Vision model
        if self.model_name == "yi-vision":
            raise NotImplementedError(
                "Yi Vision model is not supported in the current version, "
                "please format the messages manually.",
            )

        if multi_agent_mode:
            return CommonFormatter.format_multi_agent(*args)
        return CommonFormatter.format_chat(*args)

    def _save_model_invocation_and_update_monitor(
        self,
        kwargs: dict,
        response: dict,
    ) -> None:
        """Save model invocation and update the monitor accordingly.

        Args:
            kwargs (`dict`):
                The keyword arguments used in model invocation
            response (`dict`):
                The response from model API
        """
        usage = response.get("usage", None)

        if usage:
            formatted_usage = ChatUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        else:
            formatted_usage = None

        self._save_model_invocation(
            arguments=kwargs,
            response=response,
            usage=formatted_usage,
        )

        if formatted_usage:
            self.monitor.update_text_and_embedding_tokens(
                model_name=self.model_name,
                **formatted_usage.usage.model_dump(),
            )
