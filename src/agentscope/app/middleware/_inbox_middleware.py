# -*- coding: utf-8 -*-
"""Generic middleware that drains the message bus inbox before reasoning.

Producers push :class:`~agentscope.message.HintBlock` payloads into
the per-session inbox via :class:`~agentscope.app._message_bus.MessageBus`.
This middleware drains the inbox at the start of each reasoning step
and injects the HintBlocks into ``agent.state.context`` — appended to
the last assistant message's content list (same pattern as
:class:`ToolOffloadMiddleware`).

Each injected HintBlock also yields a one-shot ``HintBlockEvent``
so the front-end SSE stream can render it in real time.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, AsyncGenerator, Callable

from .._bus_ops import CHANNEL_USER_INBOX_SOURCE
from ..message_bus import MessageBus, MessageBusKeys
from ..._logging import logger
from ...agent import Agent
from ...event import HintBlockEvent
from ...message import AssistantMsg, HintBlock
from ...middleware import MiddlewareBase
from ...permission import (
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
)


class InboxMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Drain the session's inbox and inject HintBlocks before each
    reasoning step.

    Each entry in the inbox is a serialised
    :class:`~agentscope.message.HintBlock`. The middleware
    deserializes them, appends to the last assistant message in
    ``agent.state.context``, and yields a one-shot ``HintBlockEvent``
    for each so the front-end sees them.

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

    async def _pending_reason(self, session_id: str) -> str | None:
        limit = max(1, self._max_count)
        entries = await self._bus.queue_read(
            MessageBusKeys.inbox(session_id),
            max_count=limit + 1,
        )
        if any(
            payload.get(CHANNEL_USER_INBOX_SOURCE) == session_id
            for _, payload in entries
        ):
            return (
                "Pending channel input requires user approval before execution"
            )
        if len(entries) > limit:
            return "Inbox inspection is incomplete; user approval is required"
        return None

    @asynccontextmanager
    async def tool_dispatch_guard(
        self,
        agent: Agent,
    ) -> AsyncGenerator[Callable[[], bool], None]:
        """Serialize the dispatch decision with trusted inbox delivery."""
        sid = agent.state.session_id
        async with self._bus.acquire_lock(
            MessageBusKeys.inbox_lock(sid),
            ttl_secs=MessageBusKeys.INBOX_LOCK_TTL_SECS,
        ):
            reason = await self._pending_reason(sid)
            yield lambda: reason is not None

    async def on_check_permission(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable,
    ) -> PermissionDecision:
        """Invalidate AUTO approval while trusted channel input is pending."""
        decision = await next_handler(**input_kwargs)
        if (
            decision.behavior != PermissionBehavior.ALLOW
            or agent.state.permission_context.mode != PermissionMode.AUTO
            or agent.state.reply_context.has_user_confirmation(
                input_kwargs["tool_call"],
            )
        ):
            return decision
        try:
            # pylint: disable-next=protected-access
            async with asyncio.timeout(agent._engine.review_timeout):
                reason = await self._pending_reason(agent.state.session_id)
            if reason is None:
                return decision
        except Exception as error:
            reason = f"Inbox approval check failed: {type(error).__name__}"
        return replace(
            decision,
            behavior=PermissionBehavior.ASK
            if agent.state.effective_permission_context.interactive
            else PermissionBehavior.DENY,
            message=reason,
            decision_reason=reason,
            review=None,
        )

    async def on_reasoning(  # type: ignore[override]
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Drain the inbox, inject HintBlocks into context, yield
        events, then continue with downstream reasoning.

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
                One ``HintBlockEvent`` per drained inbox entry,
                followed by events from downstream.
        """
        entries = await self._bus.queue_drain(
            MessageBusKeys.inbox(agent.state.session_id),
            max_count=self._max_count,
        )

        if entries:
            # Only the gateway producer can attach this session-bound marker.
            hint_blocks = [
                HintBlock.model_validate(
                    {
                        **payload,
                        "source_role": "user"
                        if payload.get(CHANNEL_USER_INBOX_SOURCE)
                        == agent.state.session_id
                        else None,
                    },
                )
                for _entry_id, payload in entries
            ]

            logger.info(
                "InboxMiddleware: injecting %d HintBlock(s) into context "
                "for session %s",
                len(hint_blocks),
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
                    last_msg.content.extend(hint_blocks)
                else:
                    agent.state.context.append(
                        AssistantMsg(
                            id=agent.state.reply_id,
                            name=agent.name,
                            content=list(hint_blocks),
                        ),
                    )
            else:
                agent.state.context.append(
                    AssistantMsg(
                        id=agent.state.reply_id,
                        name=agent.name,
                        content=list(hint_blocks),
                    ),
                )

            # Yield one-shot events so the front-end SSE stream sees
            # each HintBlock.
            for hint in hint_blocks:
                yield HintBlockEvent(
                    reply_id=agent.state.reply_id,
                    block_id=hint.id,
                    source=hint.source,
                    source_role=hint.source_role,
                    hint=hint.hint,
                )

        async for evt in next_handler(**input_kwargs):
            yield evt
