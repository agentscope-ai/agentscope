# -*- coding: utf-8 -*-
"""Standalone Textual application for chatting with an Agent or pipeline."""

# Textual lifecycle and message handlers inherit their intent from the App.
# pylint: disable=missing-function-docstring

from __future__ import annotations

import asyncio
from typing import Sequence, TypeAlias

from textual import on
from textual.app import App, ComposeResult

from ..agent import Agent
from ..event import (
    ReplyStartEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import Msg
from ..pipeline import PipelineProtocol
from ._chat import ChatUI

_TUIInput: TypeAlias = Msg | UserConfirmResultEvent | UserInterruptEvent


class _AgentScopeTUI(App[None]):
    """The private application used by :func:`launch_tui`."""

    TITLE = "AgentScope"
    SUB_TITLE = "Interactive agent chat"
    BINDINGS = [("ctrl+q", "quit", "Quit")]
    CSS = """
    Screen {
        background: $background;
    }

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
        super().__init__()
        self.target = target
        self.initial_messages = messages
        self.user_name = user_name
        self._tasks: set[asyncio.Task[None]] = set()
        self._reply_tasks: dict[str, asyncio.Task[None]] = {}

    def compose(self) -> ComposeResult:
        yield ChatUI(
            self.initial_messages,
            user_name=self.user_name,
            id="agentscope-chat",
        )

    def _start_stream(self, inputs: _TUIInput) -> None:
        # Deliberately do not serialize submissions. Agent/Pipeline owns the
        # policy for inputs arriving while another reply is active.
        task = asyncio.create_task(self._consume(inputs))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume(self, inputs: _TUIInput) -> None:
        chat = self.query_one(ChatUI)
        task = asyncio.current_task()
        owned_reply_ids: set[str] = set()
        try:
            async for item in self.target.reply_stream(inputs):
                if isinstance(item, ReplyStartEvent) and task is not None:
                    self._reply_tasks[item.reply_id] = task
                    owned_reply_ids.add(item.reply_id)
                chat.feed(item)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.notify(str(error), title="Agent error", severity="error")
        finally:
            for reply_id in owned_reply_ids:
                if self._reply_tasks.get(reply_id) is task:
                    self._reply_tasks.pop(reply_id, None)

    @on(ChatUI.Submitted)
    def _on_submitted(self, event: ChatUI.Submitted) -> None:
        self._start_stream(event.msg)

    @on(ChatUI.Confirmed)
    def _on_confirmed(self, event: ChatUI.Confirmed) -> None:
        self._start_stream(event.value)

    @on(ChatUI.InterruptRequested)
    def _on_interrupt(self, event: ChatUI.InterruptRequested) -> None:
        chat = self.query_one(ChatUI)
        message = next(
            (msg for msg in chat.messages if msg.id == event.reply_id),
            None,
        )
        parked = bool(
            message
            and any(
                getattr(block, "state", None) in ("asking", "submitted")
                for block in message.content
            ),
        )
        if parked:
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


__all__ = ["launch_tui"]
