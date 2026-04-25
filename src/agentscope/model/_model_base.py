# -*- coding: utf-8 -*-
"""The chat model base class."""

from abc import abstractmethod
from typing import AsyncGenerator, Any, TYPE_CHECKING

from ._model_response import ChatResponse
from .._logging import logger
from ..formatter import FormatterBase
from ..message import Msg

if TYPE_CHECKING:
    from ..tool import ToolChoice
else:
    ToolChoice = Any

_TOOL_CHOICE_MODES = ["auto", "none", "required"]


class ChatModelBase:
    """Base class for chat models."""

    model_name: str
    """The model name"""

    stream: bool
    """Is the model output streaming or not"""

    max_retries: int
    """Maximum number of retries on failure"""

    fallback_model_name: str | None
    """Fallback model name to use after all retries fail"""

    formatter: Any | None
    """The API formatter that format the messages into the required format for
    the underlying API."""

    def __init__(
        self,
        model_name: str,
        stream: bool,
        max_retries: int = 0,
        fallback_model_name: str | None = None,
        formatter: FormatterBase | None = None,
    ) -> None:
        """Initialize the chat model base class.

        Args:
            model_name (`str`):
                The name of the model
            stream (`bool`):
                Whether the model output is streaming or not
            max_retries (`int`, optional):
                Maximum number of retries on failure. Defaults to 0.
            fallback_model_name (`str | None`, optional):
                Fallback model name to use after all retries fail.
            formatter (`FormatterBase | None`, optional):
                Formatter for message preprocessing.
        """
        self.model_name = model_name
        self.stream = stream
        self.max_retries = max_retries
        self.fallback_model_name = fallback_model_name
        self.formatter = formatter

    async def __call__(
        self,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the model with retry and fallback logic.

        Formats messages using formatter if available, then attempts
        to call the model up to max_retries + 1 times. If all attempts
        fail and a fallback model is configured, retries with that model.

        Args:
            messages (`list[Msg]`):
                The messages to send to the model.
            tools (`list[dict] | None`, optional):
                The tools available to the model.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice mode or function name.
            **kwargs:
                Additional keyword arguments passed to the underlying API.
        """
        if self.formatter is not None:
            messages = await self.formatter.format(messages)

        last_error: Exception | None = None

        for model_name in self._models_to_try():
            for attempt in range(self.max_retries + 1):
                try:
                    return await self._call_api(
                        model_name,
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        **kwargs,
                    )
                except Exception as e:
                    last_error = e
                    if attempt < self.max_retries:
                        logger.warning(
                            "Attempt %d failed for model %s: %s. Retrying...",
                            attempt + 1,
                            model_name,
                            str(e),
                        )
                    else:
                        logger.warning(
                            "All %d attempt(s) failed for model %s.",
                            self.max_retries + 1,
                            model_name,
                        )

        if last_error is not None:
            raise last_error
        raise RuntimeError("No models to try")

    def _models_to_try(self) -> list[str]:
        """Return the ordered list of model names to try."""
        models = [self.model_name]
        if self.fallback_model_name:
            models.append(self.fallback_model_name)
        return models

    @abstractmethod
    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the underlying API. Subclasses must implement this method.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[Msg]`):
                The messages to send to the model.
            tools (`list[dict] | None`, optional):
                The tools available to the model.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice mode or function name.
            **kwargs:
                Additional keyword arguments for the underlying API.
        """

    def _validate_tool_choice(
        self,
        tool_choice: ToolChoice | None,
        tools: list[dict] | None,
    ) -> None:
        """Validate tool_choice parameter.

        Args:
            tool_choice (`ToolChoice | None`):
                Tool choice mode or function name
            tools (`list[dict] | None`):
                Available tools list

        Raises:
            `ValueError`:
                If tool_choice is not a valid mode or function name.
        """
        if tool_choice is None:
            return

        if not isinstance(tool_choice, str):
            raise ValueError(
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
