# -*- coding: utf-8 -*-
"""Generic middleware that drains the message bus inbox before reasoning.

Producers push :class:`~agentscope.message.HintBlock` payloads into
the per-session inbox via :class:`~agentscope.app._message_bus.MessageBus`.
This middleware drains the inbox at the start of each reasoning step
and injects the blocks into ``agent.state.context`` — appended to
the last assistant message's content list (same pattern as
:class:`ToolOffloadMiddleware`).

A completed background tool is delivered as a
:class:`~agentscope.message.ToolResultBlock` instead, carrying the
original tool call id and name, so the agent can interpret it as the
completion of the offloaded call.

Each injected HintBlock also yields a one-shot ``HintBlockEvent``
so the front-end SSE stream can render it in real time; each
injected ToolResultBlock yields a ``ToolResultEndEvent`` with the
result's final state.
"""
from typing import Any, AsyncGenerator, Callable

from ..message_bus import MessageBus, MessageBusKeys
from ..._logging import logger
from ...agent import Agent
from ...event import HintBlockEvent, ToolResultEndEvent
from ...message import AssistantMsg, HintBlock, ToolResultBlock
from ...middleware import MiddlewareBase


class InboxMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Drain the session's inbox and inject blocks before each
    reasoning step.

    Each entry in the inbox is a serialised
    :class:`~agentscope.message.HintBlock` or
    :class:`~agentscope.message.ToolResultBlock` (the latter for
    completed background tools). The middleware deserializes them,
    appends to the last assistant message in ``agent.state.context``,
    and yields a one-shot event for each so the front-end sees them.

    Args:
        message_bus (`MessageBus`):
            The application message bus to read from.
        max_count (`int`, defaults to ``100``):
            Maximum number of entries drained per reasoning step.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        max_count: int = 100,
    ) -> None:
        """Initialise the middleware.

        Args:
            message_bus (`MessageBus`):
                The application-level message bus.
            max_count (`int`, defaults to ``100``):
                Maximum entries drained per reasoning step.
        """
        self._bus = message_bus
        self._max_count = max_count

    async def on_reasoning(  # type: ignore[override]
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Drain the inbox, inject blocks into context, yield events,
        then continue with downstream reasoning.

        Args:
            agent (`Agent`):
                The executing agent. ``agent.state.session_id`` selects
                the inbox to drain.
            input_kwargs (`dict`):
                Reasoning input kwargs (contains ``tool_choice``);
                forwarded unchanged to ``next_handler``.
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reasoning logic.

        Yields:
            `Any`:
                One event per drained inbox entry (``HintBlockEvent``
                for hints, ``ToolResultEndEvent`` for completed
                background tool results), followed by events from
                downstream.
        """
        entries = await self._bus.queue_drain(
            MessageBusKeys.inbox(agent.state.session_id),
            max_count=self._max_count,
        )

        if entries:
            blocks: list[HintBlock | ToolResultBlock] = []
            for _entry_id, payload in entries:
                if payload.get("type") == "tool_result":
                    blocks.append(
                        ToolResultBlock.model_validate(payload),
                    )
                else:
                    blocks.append(HintBlock.model_validate(payload))

            logger.info(
                "InboxMiddleware: injecting %d block(s) into context "
                "for session %s",
                len(blocks),
                agent.state.session_id,
            )

            # Inject into agent context (same pattern as
            # ToolOffloadMiddleware).
            if len(agent.state.context) > 0:
                last_msg = agent.state.context[-1]
                if (
                    last_msg.role == "assistant"
                    and last_msg.name == agent.name
                ):
                    last_msg.content.extend(blocks)
                else:
                    agent.state.context.append(
                        AssistantMsg(
                            id=agent.state.reply_id,
                            name=agent.name,
                            content=list(blocks),
                        ),
                    )
            else:
                agent.state.context.append(
                    AssistantMsg(
                        id=agent.state.reply_id,
                        name=agent.name,
                        content=list(blocks),
                    ),
                )

            # Yield one-shot events so the front-end SSE stream sees
            # each injected block.
            for block in blocks:
                if isinstance(block, ToolResultBlock):
                    yield ToolResultEndEvent(
                        reply_id=agent.state.reply_id,
                        tool_call_id=block.id,
                        state=block.state,
                        metadata=block.metadata,
                    )
                else:
                    yield HintBlockEvent(
                        reply_id=agent.state.reply_id,
                        block_id=block.id,
                        source=block.source,
                        hint=block.hint,
                    )

        async for evt in next_handler(**input_kwargs):
            yield evt
