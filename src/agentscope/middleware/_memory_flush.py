# -*- coding: utf-8 -*-
"""Middleware that triggers memory flush after each agent reply.

Mirrors the Java ``MemoryFlushMiddleware`` but uses Python's ``on_reply``
onion hook instead of Reactor's ``doOnComplete``.
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncGenerator, Callable

from ._base import MiddlewareBase
from .._logging import logger
from ..memory._flush_manager import MemoryFlushManager

if TYPE_CHECKING:
    from ..agent import Agent
    from ..model import ChatModelBase


class FlushMode(enum.Enum):
    """Trigger policy for memory flush."""

    ALWAYS = "always"
    NEVER = "never"
    THROTTLED = "throttled"


@dataclass(frozen=True)
class FlushTrigger:
    """Configuration for when the middleware should flush."""

    mode: FlushMode = FlushMode.ALWAYS
    min_gap_seconds: float = 60.0

    @classmethod
    def always(cls) -> "FlushTrigger":
        return cls(mode=FlushMode.ALWAYS)

    @classmethod
    def never(cls) -> "FlushTrigger":
        return cls(mode=FlushMode.NEVER)

    @classmethod
    def throttled(cls, min_gap_seconds: float = 60.0) -> "FlushTrigger":
        return cls(mode=FlushMode.THROTTLED, min_gap_seconds=min_gap_seconds)


class MemoryFlushMiddleware(MiddlewareBase):
    """Extract long-term memories into the daily ledger after each reply.

    Usage::

        agent = Agent(
            ...,
            middlewares=[
                MemoryFlushMiddleware(
                    model=model,
                    memory_dir="memory",
                    flush_trigger=FlushTrigger.throttled(60.0),
                ),
            ],
        )

    Args:
        model (`ChatModelBase`):
            The model used for memory extraction.
        memory_dir (`str`):
            Directory for memory ledgers (default ``"memory"``).
        flush_prompt (`str | None`):
            Optional custom prompt for extraction.
        flush_trigger (`FlushTrigger`):
            When to trigger flush (ALWAYS / NEVER / THROTTLED).
    """

    def __init__(
        self,
        model: "ChatModelBase",
        memory_dir: str = "memory",
        flush_prompt: str | None = None,
        flush_trigger: FlushTrigger | None = None,
    ) -> None:
        self._flush_mgr = MemoryFlushManager(
            model=model,
            memory_dir=memory_dir,
            flush_prompt=flush_prompt,
        )
        self._trigger = flush_trigger or FlushTrigger.always()
        self._last_flush_at: float = 0.0

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Run reply, then flush memories if trigger permits."""
        async for item in next_handler():
            yield item
        await self._maybe_flush(agent)

    async def _maybe_flush(self, agent: "Agent") -> None:
        if not self._should_flush_now():
            return
        messages = agent.state.context
        if not messages:
            return
        try:
            result = await self._flush_mgr.flush(messages)
            if result:
                logger.debug(
                    "[MemoryFlushMiddleware] Extracted %d chars for agent %s",
                    len(result),
                    agent.name,
                )
        except Exception as e:
            logger.warning(
                "[MemoryFlushMiddleware] Flush failed for agent %s: %s",
                agent.name,
                e,
            )

    def _should_flush_now(self) -> bool:
        """Apply the configured trigger policy."""
        match self._trigger.mode:
            case FlushMode.ALWAYS:
                return True
            case FlushMode.NEVER:
                return False
            case FlushMode.THROTTLED:
                now = time.time()
                if now - self._last_flush_at < self._trigger.min_gap_seconds:
                    return False
                self._last_flush_at = now
                return True
            case _:
                return True
