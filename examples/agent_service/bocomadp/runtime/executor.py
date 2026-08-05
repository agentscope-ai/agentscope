# -*- coding: utf-8 -*-
"""Agent execution driver — heartbeat-wrapped reply stream.

Drives ``agent.reply_stream(inputs=msgs)`` and delegates each event
to ``Envelope.translate_event()``. Wraps the raw event stream with
a heartbeat so long idle periods (e.g. tool approval waits) emit
keep-alive envelopes instead of letting the SSE connection drop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from .envelope import Envelope

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 15.0
_HEARTBEAT_TICK = object()  # sentinel


async def _iter_with_heartbeat(
    agen: Any,
    interval: float,
) -> AsyncGenerator[Any, None]:
    """Wrap an async generator with heartbeat ticks.

    If the wrapped generator doesn't produce an event within
    *interval* seconds, yield ``_HEARTBEAT_TICK`` so the caller
    can emit a keep-alive.
    """
    while True:
        try:
            event = await asyncio.wait_for(
                agen.__anext__(),
                timeout=interval,
            )
            yield event
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            yield _HEARTBEAT_TICK


class AgentExecutor:
    """Execute the agent's reply stream and translate events into
    SSE envelope objects.

    One instance per ``Runtime.run()`` invocation. The executor owns
    the heartbeat wrapper but not the agent itself.
    """

    def __init__(
        self,
        agent: Any,
        envelope: Envelope,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._agent = agent
        self._envelope = envelope
        self._heartbeat_interval = heartbeat_interval
        self._cancelled = False

    def cancel(self) -> None:
        """Request cooperative cancellation of the stream."""
        self._cancelled = True
        logger.info("executor: cancellation requested")

    async def run(
        self,
        msgs: list[Any],
    ) -> AsyncGenerator[dict, None]:
        """Drive ``agent.reply_stream`` and yield SSE envelope dicts.

        Wraps the raw event stream with ``_iter_with_heartbeat`` so
        long idle periods emit keep-alive envelopes instead of
        dropping the SSE connection.
        """
        agent_iter = self._agent.reply_stream(inputs=msgs).__aiter__()
        async for event in _iter_with_heartbeat(
            agent_iter,
            self._heartbeat_interval,
        ):
            if self._cancelled:
                async for obj in self._envelope.cancel_envelope():
                    yield obj
                return

            if event is _HEARTBEAT_TICK:
                async for obj in self._envelope.heartbeat():
                    yield obj
                continue

            # Msg objects (final assistant message) are handled by
            # finalize; only translate EventType events here.
            if hasattr(event, "type") or hasattr(event, "block_id"):
                async for obj in self._envelope.translate_event(event):
                    yield obj
            else:
                # Msg — skip, will be captured in finalize
                logger.debug("executor: skipping Msg event in stream")


__all__ = ["AgentExecutor", "HEARTBEAT_INTERVAL_SECONDS"]
