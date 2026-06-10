# -*- coding: utf-8 -*-
"""Compaction middleware that triggers context compression before reasoning."""
from __future__ import annotations

from typing import AsyncGenerator, Callable, TYPE_CHECKING

from ._base import MiddlewareBase

if TYPE_CHECKING:
    from ..agent import Agent


class CompactionMiddleware(MiddlewareBase):
    """Middleware that invokes ``agent.compress_context()`` before reasoning.

    This mirrors the Java ``CompactionMiddleware`` by hooking into the
    reasoning phase and ensuring the conversation stays within token budget
    before the LLM call is made.

    Usage::

        agent = Agent(
            ...,
            middlewares=[CompactionMiddleware()],
        )
    """

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Trigger compression before reasoning, then delegate."""
        await agent.compress_context()
        async for item in next_handler():
            yield item
