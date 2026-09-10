# -*- coding: utf-8 -*-
"""Standalone Textual application for chatting with an Agent or pipeline."""

# Textual lifecycle and message handlers inherit their intent from the App.
# pylint: disable=missing-function-docstring

from __future__ import annotations

import asyncio
from typing import Sequence, TypeAlias

from textual import on
from textual.app import App, ComposeResult

from .._logging import logger
from ..agent import Agent
from ..event import (
    AgentEvent,
    ExternalExecutionResultEvent,
    ReplyStartEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import AssistantMsg, Msg
from ..pipeline import PipelineProtocol
from ._chat import ChatUI

_TUIInput: TypeAlias = (
    Msg
    | UserConfirmResultEvent
    | ExternalExecutionResultEvent
    | UserInterruptEvent
)


class _Conversation:
    """Accumulate backend events into the messages displayed by the UI."""

    def __init__(self, messages: Sequence[Msg] = ()) -> None:
        self.messages = [message.model_copy(deep=True) for message in messages]
        self._by_id = {message.id: message for message in self.messages}

    def feed(self, item: AgentEvent | Msg) -> None:
        if isinstance(item, Msg):
            message = item.model_copy(deep=True)
            previous = self._by_id.get(message.id)
            if previous is None:
                self.messages.append(message)
            else:
                self.messages[self.messages.index(previous)] = message
            self._by_id[message.id] = message
            return
        reply_id = getattr(item, "reply_id", None)
        if reply_id is None:
            return
        message = self._by_id.get(reply_id)
        if message is None:
            name = item.name if isinstance(item, ReplyStartEvent) else "agent"
            message = AssistantMsg(name=name, content=[], id=reply_id)
            self.messages.append(message)
            self._by_id[reply_id] = message
        if isinstance(item, ReplyStartEvent):
            message.name = item.name
        else:
            message.append_event(item)


class _AgentScopeTUI(App[None]):
    """The private application used by :func:`launch_tui`."""

    TITLE = "AgentScope"
    SUB_TITLE = "Interactive agent chat"
    BINDINGS = []
    CSS = """
    #agentscope-chat {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(
        self,
        target: Agent | PipelineProtocol,
        messages: Sequence[Msg],
        user_name: str,
    ) -> None:
        # Render ANSI default colors so transparent widgets inherit the
        # user's terminal background rather than Textual's dark theme.
        super().__init__(ansi_color=True)
        self.target = target
        self._conversation = _Conversation(messages)
        self.user_name = user_name
        self._tasks: set[asyncio.Task[None]] = set()
        self._reply_tasks: dict[str, asyncio.Task[None]] = {}
        self._reply_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield ChatUI(
            self._conversation.messages,
            user_name=self.user_name,
            id="agentscope-chat",
        )

    def _start_stream(self, inputs: _TUIInput) -> None:
        # The standalone application accepts input while a reply is running,
        # but queues each reply_stream call so one target context is never
        # mutated by concurrent replies.
        task = asyncio.create_task(self._consume(inputs))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume(self, inputs: _TUIInput) -> None:
        async with self._reply_lock:
            chat = self.query_one(ChatUI)
            task = asyncio.current_task()
            owned_reply_ids: set[str] = set()
            if not isinstance(inputs, Msg) and task is not None:
                self._reply_tasks[inputs.reply_id] = task
                owned_reply_ids.add(inputs.reply_id)
            try:
                if isinstance(inputs, (Msg, UserConfirmResultEvent)):
                    self._conversation.feed(inputs)
                    await chat.set_messages(self._conversation.messages)
                async for item in self.target.reply_stream(inputs):
                    if isinstance(item, ReplyStartEvent) and task is not None:
                        self._reply_tasks[item.reply_id] = task
                        owned_reply_ids.add(item.reply_id)
                    self._conversation.feed(item)
                    await chat.set_messages(self._conversation.messages)
            # The standalone UI must keep running if its target fails.
            # pylint: disable-next=broad-exception-caught
            except Exception as error:
                logger.exception("TUI reply stream failed")
                self.notify(str(error), title="Agent error", severity="error")
            finally:
                for reply_id in owned_reply_ids:
                    if self._reply_tasks.get(reply_id) is task:
                        self._reply_tasks.pop(reply_id, None)

    @on(ChatUI.Submitted)
    def _on_submitted(self, event: ChatUI.Submitted) -> None:
        if event.msg.get_text_content().strip().casefold() == "/exit":
            self.exit()
            return
        self._start_stream(event.msg)

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

    async def on_unmount(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


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
