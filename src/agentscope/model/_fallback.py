# -*- coding: utf-8 -*-
"""Fallback model wrapper that switches to a backup model on failure."""
from __future__ import annotations

from typing import Any, AsyncGenerator

from ._base import ChatModelBase
from ._model_response import ChatResponse, StructuredResponse
from .._logging import logger
from ..message import Msg
from ..tool import ToolChoice


class FallbackChatModel(ChatModelBase):
    """Wraps a primary and a fallback model.

    When the primary model raises an exception (after its own retry budget is
    exhausted), the fallback model is invoked with the same arguments.

    Usage::

        primary = OpenAIChatModel(...)
        fallback = DashScopeChatModel(...)
        model = FallbackChatModel(primary=primary, fallback=fallback)

        agent = Agent(
            name="assistant",
            model=model,
            ...
        )
    """

    def __init__(
        self,
        primary: ChatModelBase,
        fallback: ChatModelBase,
    ) -> None:
        """Initialize the fallback model wrapper.

        Args:
            primary (`ChatModelBase`):
                The primary model to use.
            fallback (`ChatModelBase`):
                The fallback model invoked when the primary fails.
        """
        self._primary = primary
        self._fallback = fallback
        # Mirror primary's surface configuration for Agent introspection
        self.credential = primary.credential
        self.model = primary.model
        self.stream = primary.stream
        self.max_retries = primary.max_retries
        self.retry_delay = primary.retry_delay
        self.context_size = primary.context_size

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Delegate to primary, then fallback on failure."""
        try:
            return await self._primary._call_api(
                model_name,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs,
            )
        except Exception as e:
            logger.warning(
                "Primary model %s failed (%s), switching to fallback %s",
                self._primary.model,
                e,
                self._fallback.model,
            )
            return await self._fallback._call_api(
                self._fallback.model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs,
            )

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Any,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Delegate structured-output call to primary, then fallback."""
        try:
            return await self._primary._call_api_with_structured_output(
                model_name,
                messages=messages,
                structured_model=structured_model,
                tool_choice=tool_choice,
                **kwargs,
            )
        except Exception as e:
            logger.warning(
                "Primary model %s failed on structured output (%s), "
                "switching to fallback %s",
                self._primary.model,
                e,
                self._fallback.model,
            )
            return await self._fallback._call_api_with_structured_output(
                self._fallback.model,
                messages=messages,
                structured_model=structured_model,
                tool_choice=tool_choice,
                **kwargs,
            )

    async def count_tokens(
        self,
        messages: list[Msg],
        tools: list[dict] | None,
    ) -> int:
        """Count tokens using the primary model's heuristic."""
        return await self._primary.count_tokens(messages, tools)
