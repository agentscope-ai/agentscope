# -*- coding: utf-8 -*-
"""The chat model base class."""

from abc import abstractmethod
from typing import AsyncGenerator, Any

from ._model_response import ChatResponse


_TOOL_CHOICE_MODES = ["auto", "none", "required"]


class ChatModelBase:
    """Base class for chat models."""

    model_name: str
    """The model name"""

    stream: bool
    """Is the model output streaming or not"""

    def __init__(
        self,
        model_name: str,
        stream: bool,
    ) -> None:
        """Initialize the chat model base class.

        Args:
            model_name (`str`):
                The name of the model
            stream (`bool`):
                Whether the model output is streaming or not
        """
        self.model_name = model_name
        self.stream = stream

    @abstractmethod
    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        pass

    def _validate_tool_choice(
        self,
        tool_choice: str,
        tools: list[dict] | None,
    ) -> None:
        """
        Validate tool_choice parameter.

        Args:
            tool_choice (`str`):
                Tool choice mode or function name
            tools (`list[dict] | None`):
                Available tools list
        Raises:
            TypeError: If tool_choice is not string
            ValueError: If tool_choice is invalid
        """
        if not isinstance(tool_choice, str):
            raise TypeError(
                f"tool_choice must be str, got {type(tool_choice)}",
            )
        if tool_choice in _TOOL_CHOICE_MODES:
            return

        available_functions = [tool["function"]["name"] for tool in tools]

        if tool_choice not in available_functions:
            all_options = _TOOL_CHOICE_MODES + available_functions
            raise ValueError(
                f"Invalid tool_choice '{tool_choice}'. "
                f"Available options: {', '.join(sorted(all_options))}",
            )
