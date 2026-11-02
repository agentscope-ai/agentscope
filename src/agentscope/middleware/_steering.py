# -*- coding: utf-8 -*-
"""Inject steering inputs at normal Agent reasoning boundaries."""
import asyncio
from contextlib import aclosing
from typing import Any, AsyncGenerator, Callable, TYPE_CHECKING

from ._base import MiddlewareBase
from ._steering_queue import SteeringInput, SteeringSource
from ..event import (
    ExternalExecutionResultEvent,
    HintBlockEvent,
    ReplyStartEvent,
    UserConfirmResultEvent,
)
from ..message import AssistantMsg, DataBlock, HintBlock, Msg, TextBlock

if TYPE_CHECKING:
    from ..agent import Agent


class SteeringMiddleware(MiddlewareBase):
    """Guide active replies without interrupting model or tool calls.

    Args:
        source: Reply-scoped source of pending steering inputs.
        hint_source: Source label attached to injected hint blocks.

    The source is checked before reasoning and after a text-only response.
    A new input can suppress that response's final candidate; the Agent then
    performs another normal iteration, including compression and budgeting.
    Inputs received during tools wait until the next reasoning call.
    """

    def __init__(
        self,
        source: SteeringSource,
        *,
        hint_source: str = "steering",
    ) -> None:
        """Store the input source and the hint event label."""
        self._source = source
        self._hint_source = hint_source

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Open and close input windows across reply and resume lifecycles."""
        session_id = agent.state.session_id
        active_reply_id: str | None = None
        try:
            if isinstance(
                input_kwargs.get("inputs"),
                (UserConfirmResultEvent, ExternalExecutionResultEvent),
            ):
                await self._source.start_reply(
                    session_id,
                    agent.state.reply_id,
                )
                active_reply_id = agent.state.reply_id
            async with aclosing(next_handler(**input_kwargs)) as stream:
                async for event in stream:
                    if isinstance(event, ReplyStartEvent):
                        if active_reply_id is not None:
                            await self._source.finish_reply(
                                session_id,
                                active_reply_id,
                            )
                        await self._source.start_reply(
                            session_id,
                            event.reply_id,
                        )
                        active_reply_id = event.reply_id
                    yield event
        finally:
            # Another middleware may swallow ReplyEndEvent to continue.
            # Close when the invocation actually exits, not at that event.
            if active_reply_id is not None:
                await self._source.finish_reply(session_id, active_reply_id)

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Run one model call, yielding continuation control to the Agent."""
        consumed = False
        async for event in self._consume(agent):
            consumed = True
            yield event
        if consumed:
            # The Agent checked context before entering reasoning. Include
            # newly injected text and attachments before calling the model.
            await agent.compress_context()

        terminal_messages: list[Msg] = []
        async for event in next_handler(**input_kwargs):
            if isinstance(event, Msg):
                terminal_messages.append(event)
            else:
                yield event

        # The forced finalization call must still be allowed to terminate.
        # Its late inputs stay pending and become unconsumed at reply end.
        if (
            terminal_messages
            and agent.state.cur_iter < agent.react_config.max_iters
        ):
            consumed = False
            async for event in self._consume(agent):
                consumed = True
                yield event
            if consumed:
                return

        for message in terminal_messages:
            yield message

    async def _consume(
        self,
        agent: "Agent",
    ) -> AsyncGenerator[HintBlockEvent, None]:
        """Validate inputs and acknowledge only a successful context append."""
        session_id = agent.state.session_id
        reply_id = agent.state.reply_id
        items = await self._source.read(session_id, reply_id)
        prepared: list[SteeringInput] = []
        hints: list[HintBlock] = []
        for item in items:
            try:
                item_hints = self._to_hints(item.id, item.messages)
            except ValueError as exc:
                await self._source.reject(
                    session_id,
                    reply_id,
                    item,
                    f"The steering input could not be injected: {exc}",
                )
            else:
                prepared.append(item)
                hints.extend(item_hints)
        if not prepared:
            return

        rollback = self._append_hints(agent, hints)
        acknowledgement = asyncio.create_task(
            self._source.acknowledge(session_id, reply_id, prepared),
        )
        cancellation: asyncio.CancelledError | None = None
        # Cancellation is not evidence that acknowledgement failed: the
        # source may already have removed the inputs. Resolve its outcome
        # before either rolling back or allowing reply persistence to run.
        while not acknowledgement.done():
            try:
                await asyncio.shield(acknowledgement)
            except asyncio.CancelledError as exc:
                if cancellation is None:
                    cancellation = exc
            except Exception:
                break
        try:
            acknowledgement.result()
        except BaseException as exc:
            rollback()
            if cancellation is not None:
                raise cancellation from exc
            raise
        # Project every committed hint before propagating cancellation. The
        # service persists replies built from events, not the agent context.
        for hint in hints:
            yield HintBlockEvent(
                reply_id=reply_id,
                block_id=hint.id,
                source=hint.source,
                hint=hint.hint,
            )
        # Receive any further cancellation redirected by the event consumer
        # before the Agent leaves reasoning and starts interruption cleanup.
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
        if cancellation is not None:
            raise cancellation

    def _to_hints(self, item_id: str, messages: list[Msg]) -> list[HintBlock]:
        """Convert one steering input into correlated multimodal hints.

        Args:
            item_id (`str`):
                Source input identifier used to derive stable block ids.
            messages (`list[Msg]`):
                Parsed messages carried by the steering input.

        Returns:
            `list[HintBlock]`:
                One hint per message, preserving text and data blocks.

        Raises:
            `ValueError`:
                A message is empty or contains a block unsupported by hints.
        """
        if not messages:
            raise ValueError("Steering requires at least one message.")
        hints: list[HintBlock] = []
        for index, message in enumerate(messages):
            content = [
                block
                for block in message.content
                if isinstance(block, (TextBlock, DataBlock))
            ]
            if not content or len(content) != len(message.content):
                raise ValueError(
                    f"Message '{message.id}' contains unsupported content.",
                )
            hints.append(
                HintBlock(
                    id=f"{item_id}:{index}",
                    hint=content,
                    source=self._hint_source,
                ),
            )
        return hints

    @staticmethod
    def _append_hints(
        agent: "Agent",
        hints: list[HintBlock],
    ) -> Callable[[], None]:
        """Append hints synchronously and return an exact rollback callback."""
        if agent.state.context:
            last_message = agent.state.context[-1]
            if (
                last_message.role == "assistant"
                and last_message.name == agent.name
                and last_message.id == agent.state.reply_id
            ):
                original_length = len(last_message.content)
                last_message.content.extend(hints)

                def rollback_extension() -> None:
                    del last_message.content[original_length:]

                return rollback_extension

        injected_message = AssistantMsg(
            id=agent.state.reply_id,
            name=agent.name,
            content=list(hints),
        )
        agent.state.context.append(injected_message)

        def rollback_message() -> None:
            if (
                agent.state.context
                and agent.state.context[-1] is injected_message
            ):
                agent.state.context.pop()

        return rollback_message
