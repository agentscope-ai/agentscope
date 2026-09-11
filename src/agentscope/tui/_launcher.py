# -*- coding: utf-8 -*-
"""Standalone Textual applications for chatting with an agent."""

# Textual lifecycle and message handlers inherit their intent from the App.
# pylint: disable=missing-function-docstring

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, Sequence, TypeAlias

from textual import on
from textual.app import App, ComposeResult

from .._logging import logger
from ..agent import Agent, RealtimeAgent
from ..event import (
    AgentEvent,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ExternalExecutionResultEvent,
    ReplyStartEvent,
    ReplyEndEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import AssistantMsg, Msg, UserMsg
from ..pipeline import PipelineProtocol
from ..realtime import TransportBase
from ._chat import ChatUI

_TUIInput: TypeAlias = (
    Msg
    | UserConfirmResultEvent
    | ExternalExecutionResultEvent
    | UserInterruptEvent
)

_RealtimeInput: TypeAlias = Msg | UserConfirmResultEvent | UserInterruptEvent


class _ChatAppBase(App[None]):
    """The half both applications share: one :class:`ChatUI`, the replies
    still open, and the folding of a stream into them. What drives the
    target — a reply per submission, or one continuous session — is left
    to the subclass.
    """

    TITLE = "AgentScope"
    BINDINGS = []
    CSS = """
    #agentscope-chat {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(
        self,
        messages: Sequence[Msg],
        user_name: str,
        input_enabled: bool = True,
    ) -> None:
        # Render ANSI default colors so transparent widgets inherit the
        # user's terminal background rather than Textual's dark theme.
        super().__init__(ansi_color=True)
        self._initial_messages = messages
        self._replies = {
            msg.id: msg.model_copy(deep=True)
            for msg in messages
            if msg.role == "assistant" and msg.finished_at is None
        }
        self.user_name = user_name
        self.input_enabled = input_enabled
        self._tasks: set[asyncio.Task[None]] = set()

    def compose(self) -> ComposeResult:
        yield ChatUI(
            self._initial_messages,
            user_name=self.user_name,
            input_enabled=self.input_enabled,
            id="agentscope-chat",
        )

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run *coro* until it finishes or the application unmounts."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _publish(self, item: Msg | AgentEvent) -> None:
        """Fold one item of a stream into the open replies and render it."""
        if isinstance(item, Msg):
            message = item.model_copy(deep=True)
            if message.role == "assistant" and message.finished_at is None:
                self._replies[message.id] = message
        else:
            reply_id = getattr(item, "reply_id", None)
            if reply_id is None:
                return
            message = self._replies.get(reply_id)
            if message is None:
                # A reply resumed after a confirmation gets no
                # ReplyStartEvent, so open one here as well.
                start = item if isinstance(item, ReplyStartEvent) else None
                if start is not None and start.role == "user":
                    message = UserMsg(
                        name=start.name,
                        content=[],
                        id=reply_id,
                    )
                else:
                    message = AssistantMsg(
                        name=start.name if start else "agent",
                        content=[],
                        id=reply_id,
                    )
                # Every reply stays open until it ends, including a spoken
                # user turn, which UserMsg would stamp as finished.
                message.finished_at = None
                self._replies[reply_id] = message
            if isinstance(item, ReplyStartEvent):
                message.name = item.name
            else:
                message.append_event(item)
        await self.query_one(ChatUI).update_message(message)
        if isinstance(item, ReplyEndEvent) or message.finished_at is not None:
            self._replies.pop(message.id, None)

    def _submit(self, msg: Msg) -> None:
        """Hand a message composed by the user to the target."""
        raise NotImplementedError

    @on(ChatUI.Submitted)
    def _on_submitted(self, event: ChatUI.Submitted) -> None:
        if event.msg.get_text_content().strip().casefold() == "/exit":
            self.exit()
            return
        self._submit(event.msg)

    async def on_unmount(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class _AgentScopeTUI(_ChatAppBase):
    """The private application used by :func:`launch_tui`."""

    SUB_TITLE = "Interactive agent chat"

    def __init__(
        self,
        target: Agent | PipelineProtocol,
        messages: Sequence[Msg],
        user_name: str,
    ) -> None:
        super().__init__(messages, user_name)
        self.target = target
        self._reply_tasks: dict[str, asyncio.Task[None]] = {}
        self._reply_lock = asyncio.Lock()

    def _start_stream(self, inputs: _TUIInput) -> None:
        # The standalone application accepts input while a reply is running,
        # but queues each reply_stream call so one target context is never
        # mutated by concurrent replies.
        self._spawn(self._consume(inputs))

    async def _consume(self, inputs: _TUIInput) -> None:
        if isinstance(inputs, Msg):
            await self._publish(inputs)
        async with self._reply_lock:
            task = asyncio.current_task()
            owned_reply_ids: set[str] = set()
            if not isinstance(inputs, Msg) and task is not None:
                self._reply_tasks[inputs.reply_id] = task
                owned_reply_ids.add(inputs.reply_id)
            try:
                if isinstance(inputs, UserConfirmResultEvent):
                    await self._publish(inputs)
                async for item in self.target.reply_stream(inputs):
                    if isinstance(item, ReplyStartEvent) and task is not None:
                        self._reply_tasks[item.reply_id] = task
                        owned_reply_ids.add(item.reply_id)
                    await self._publish(item)
            # The standalone UI must keep running if its target fails.
            # pylint: disable-next=broad-exception-caught
            except Exception as error:
                logger.exception("TUI reply stream failed")
                self.notify(str(error), title="Agent error", severity="error")
            finally:
                for reply_id in owned_reply_ids:
                    if self._reply_tasks.get(reply_id) is task:
                        self._reply_tasks.pop(reply_id, None)

    def _submit(self, msg: Msg) -> None:
        self._start_stream(msg)

    @on(ChatUI.Confirmed)
    def _on_confirmed(self, event: ChatUI.Confirmed) -> None:
        self._start_stream(event.value)

    @on(ChatUI.ExternalExecutionSubmitted)
    def _on_external_execution_submitted(
        self,
        event: ChatUI.ExternalExecutionSubmitted,
    ) -> None:
        self._start_stream(event.value)

    @on(ChatUI.InterruptRequested)
    def _on_interrupt(self, event: ChatUI.InterruptRequested) -> None:
        chat = self.query_one(ChatUI)
        if chat.is_reply_parked(event.reply_id):
            self._start_stream(UserInterruptEvent(reply_id=event.reply_id))
            return
        task = self._reply_tasks.get(event.reply_id)
        if task is not None:
            task.cancel()


class _RealtimeTUI(_ChatAppBase):
    """The private application used by :func:`launch_realtime_ui`."""

    SUB_TITLE = "Realtime voice chat"

    def __init__(
        self,
        agent: RealtimeAgent,
        transport: TransportBase,
        messages: Sequence[Msg],
        user_name: str,
    ) -> None:
        # Speaking is the way in; the composer is only good for a model
        # that also takes a text turn mid-session.
        super().__init__(
            messages,
            user_name,
            input_enabled=agent.model.supports_text_input,
        )
        self.agent = agent
        self.transport = transport

    def on_mount(self) -> None:
        # Audio flows for as long as the transport lives, so the session is
        # one stream, started here, rather than one per submission.
        self._spawn(self._consume())

    async def _consume(self) -> None:
        try:
            async for event in self.agent.reply_stream(self.transport):
                # The transport plays the reply; the view shows what was
                # said, so the audio blocks are of no use here.
                if isinstance(
                    event,
                    (
                        DataBlockStartEvent,
                        DataBlockDeltaEvent,
                        DataBlockEndEvent,
                    ),
                ):
                    continue
                await self._publish(event)
            # The stream ends with the transport, and there is no second
            # way in: nothing is left to show.
            self.exit()
        # The standalone UI must keep running if its agent fails.
        # pylint: disable-next=broad-exception-caught
        except Exception as error:
            logger.exception("Realtime TUI session failed")
            self.notify(str(error), title="Agent error", severity="error")

    async def _send(self, inputs: _RealtimeInput) -> None:
        """Hand the agent everything that is not audio."""
        # An interrupt leaves its mark on the reply it cuts off; the rest
        # is shown as it is submitted, since the agent does not echo it.
        if not isinstance(inputs, UserInterruptEvent):
            await self._publish(inputs)
        try:
            await self.agent.send(inputs)
        # pylint: disable-next=broad-exception-caught
        except Exception as error:
            logger.exception("Realtime TUI input failed")
            self.notify(str(error), title="Agent error", severity="error")

    def _submit(self, msg: Msg) -> None:
        self._spawn(self._send(msg))

    @on(ChatUI.Confirmed)
    def _on_confirmed(self, event: ChatUI.Confirmed) -> None:
        self._spawn(self._send(event.value))

    @on(ChatUI.InterruptRequested)
    def _on_interrupt(self, event: ChatUI.InterruptRequested) -> None:
        # Interrupting means barging in on the reply being spoken; there is
        # no per-reply task to cancel.
        self._spawn(self._send(UserInterruptEvent(reply_id=event.reply_id)))


async def launch_tui(
    target: Agent | PipelineProtocol,
    *,
    messages: Sequence[Msg] = (),
    user_name: str = "user",
) -> None:
    """Launch a full-screen interactive terminal chat.

    Args:
        target (`Agent | PipelineProtocol`):
            Agent or pipeline whose ``reply_stream`` consumes user messages
            and HITL continuation events.
        messages (`Sequence[Msg]`, optional):
            Historical messages displayed before live interaction starts.
        user_name (`str`, defaults to ``"user"``):
            Name assigned to messages submitted from the composer.
    """
    await _AgentScopeTUI(target, messages, user_name).run_async()


async def launch_realtime_ui(
    agent: RealtimeAgent,
    transport: TransportBase,
    *,
    messages: Sequence[Msg] = (),
    user_name: str = "user",
) -> None:
    """Launch a full-screen terminal view of a voice session.

    Both the agent and the transport are borrowed: they must already be
    started, and neither is closed here.

    Args:
        agent (`RealtimeAgent`):
            A connected realtime agent. Its ``reply_stream`` runs for the
            whole session, while a confirmation, an interrupt or a typed
            turn goes back in through ``send``.
        transport (`TransportBase`):
            The started transport carrying the audio. It plays the reply
            itself, so the view shows the transcripts only.
        messages (`Sequence[Msg]`, optional):
            Historical messages displayed before live interaction starts.
        user_name (`str`, defaults to ``"user"``):
            Name assigned to messages submitted from the composer, which is
            disabled for a model that takes no text input.
    """
    await _RealtimeTUI(agent, transport, messages, user_name).run_async()
