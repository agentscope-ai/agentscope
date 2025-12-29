# -*- coding: utf-8 -*-
"""The chat model base class."""

import asyncio
from abc import abstractmethod
from typing import AsyncGenerator, Any

from ._model_response import ChatResponse
from .._logging import logger


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
        max_retries: int = 0,
        retry_interval: float = 1.0,
        fallback_model_name: str | None = None,
        fallback_max_retries: int = 0,
    ) -> None:
        """Initialize the chat model base class.

        Args:
            model_name (`str`):
                The name of the model
            stream (`bool`):
                Whether the model output is streaming or not
            max_retries (`int`, default `0`):
                Maximum number of retry attempts when API calls fail.
            retry_interval (`float`, default `1.0`):
                Initial retry interval in seconds. The interval will increase
                exponentially with each retry attempt.
            fallback_model_name (`str | None`, default `None`):
                The fallback model name to use when all retries with the
                primary model fail. If provided, a final attempt will be made
                using this model before raising the exception.
            fallback_max_retries (`int`, default `0`):
                Maximum number of retry attempts for fallback model.

        """
        self.model_name = model_name
        self.stream = stream
        self.max_retries = max_retries
        self.retry_interval = retry_interval
        self.fallback_model_name = fallback_model_name
        self.fallback_max_retries = fallback_max_retries

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the model with retry logic.

        Args:
            *args: Positional arguments to pass to the model.
            **kwargs: Keyword arguments to pass to the model.

        Returns:
            ChatResponse | AsyncGenerator[ChatResponse, None]:
                The response from the model.
        """

        async def _execute_with_retry(
            model_name_override: str | None = None,
            retry_number: int = 0,
        ) -> AsyncGenerator[ChatResponse, None]:
            """Unified retry generator that handles both streaming and
            non-streaming responses.

            Args:
                model_name_override (`str | None`, default `None`):
                    Override model name for fallback calls.
                retry_number (`int`, default `0`):
                    Number of retries to attempt.

            Yields:
                ChatResponse: Response chunks (single item for non-streaming).
            """
            last_exception: BaseException | None = None

            for attempt in range(retry_number + 1):
                try:
                    result = await self._call_api(
                        *args,
                        model_name_override=model_name_override,
                        **kwargs,
                    )

                    if self.stream:
                        # Streaming: iterate and yield each chunk
                        assert isinstance(result, AsyncGenerator)
                        async for chunk in result:
                            yield chunk
                    else:
                        # Non-streaming: yield the single result
                        assert isinstance(result, ChatResponse)
                        yield result
                    return

                except Exception as e:
                    last_exception = e

                    # Check if max retries reached
                    if attempt >= retry_number:
                        logger.error(
                            "Failed to call the API after %d attempts: %s",
                            retry_number + 1,
                            e,
                        )
                        break

                    # Log warning and wait before retry
                    wait_time = self.retry_interval * (2**attempt)
                    logger.warning(
                        "Failed to call the API (attempt %d/%d): %s. "
                        "Retrying in %.2f seconds...",
                        attempt + 1,
                        retry_number + 1,
                        e,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)

            # All retries failed, try fallback model if available
            if (
                self.fallback_model_name
                and model_name_override != self.fallback_model_name
            ):
                logger.warning(
                    "All retries with model '%s' failed. "
                    "Attempting with fallback model '%s'...",
                    model_name_override or self.model_name,
                    self.fallback_model_name,
                )
                # Recursively call with fallback model
                async for item in _execute_with_retry(
                    self.fallback_model_name,
                    self.fallback_max_retries,
                ):
                    yield item
                return

            assert last_exception is not None
            raise last_exception

        # Return based on streaming mode
        if self.stream:
            return _execute_with_retry(
                retry_number=self.max_retries,
            )

        # Non-streaming: consume the generator and return single result
        async for result in _execute_with_retry(
            retry_number=self.max_retries,
        ):
            return result

        # This line should never be reached since the generator either
        # yields a result or raises an exception
        raise RuntimeError("Unexpected: no result from API call")

    @abstractmethod
    async def _call_api(
        self,
        *args: Any,
        model_name_override: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the API with the given arguments.

        Subclasses must implement this method to perform the actual API call.

        Args:
            *args: Positional arguments for the API call.
            model_name_override (`str | None`, default `None`):
                If provided, use this model name instead of `self.model_name`.
                This is used for fallback model calls.
            **kwargs: Keyword arguments for the API call.

        Returns:
            ChatResponse | AsyncGenerator[ChatResponse, None]:
                The response from the API.
        """

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
