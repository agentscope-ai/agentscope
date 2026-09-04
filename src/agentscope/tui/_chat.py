# -*- coding: utf-8 -*-
"""Composable chat widget with input and human-in-the-loop controls."""

# Textual handlers and nested message payloads are intentionally tiny and
# inherit their behavioral documentation from their owning widgets.
# pylint: disable=missing-function-docstring,missing-class-docstring
# pylint: disable=attribute-defined-outside-init,protected-access

from __future__ import annotations

from typing import Sequence

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Static, TextArea

from ..event import AgentEvent, ConfirmResult, UserConfirmResultEvent
from ..message import Msg, ToolCallBlock, UserMsg
from ._messages import MessagesUI


class _ComposerTextArea(TextArea):
    """A TextArea where Enter submits and Shift+Enter inserts a newline."""

    class SubmitRequested(Message):
        """Request submission of the current editor contents."""

    async def _on_key(self, event: events.Key) -> None:
        # Textual reports modified keys in ``event.key`` (e.g.
        # ``shift+enter``). Intercept both variants before TextArea's default
        # handler turns Enter into a newline.
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.SubmitRequested())
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


class ComposerUI(Vertical):
    """Multi-line chat composer with send and targeted stop actions."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class InterruptRequested(Message):
        def __init__(self, reply_id: str) -> None:
            super().__init__()
            self.reply_id = reply_id

    def __init__(self) -> None:
        super().__init__(classes="as-composer")
        self._enabled = True
        self._running_reply_id: str | None = None

    def compose(self) -> ComposeResult:
        yield _ComposerTextArea(
            placeholder="Message the agent…",
            id="as-composer-input",
            soft_wrap=True,
            compact=True,
        )
        with Horizontal(classes="as-composer-actions"):
            yield Static(
                "Enter send · Shift+Enter newline",
                classes="as-composer-hint",
            )
            yield Button("Stop", id="as-stop", variant="warning")
            yield Button("Send", id="as-send", variant="primary")

    @property
    def draft(self) -> str:
        return self.query_one(_ComposerTextArea).text

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not self.is_mounted:
            return
        editor = self.query_one(_ComposerTextArea)
        editor.disabled = not enabled
        self.query_one("#as-send", Button).disabled = not enabled

    def set_running_reply(self, reply_id: str | None) -> None:
        self._running_reply_id = reply_id
        if self.is_mounted:
            self.query_one("#as-stop", Button).display = reply_id is not None

    def focus_editor(self) -> None:
        if self.is_mounted and self._enabled:
            self.query_one(_ComposerTextArea).focus()

    def on_mount(self) -> None:
        self.set_enabled(self._enabled)
        self.set_running_reply(self._running_reply_id)

    def _submit(self) -> None:
        if not self._enabled:
            return
        editor = self.query_one(_ComposerTextArea)
        value = editor.text.strip()
        if not value:
            return
        editor.load_text("")
        self.post_message(self.Submitted(value))

    @on(_ComposerTextArea.SubmitRequested)
    def _on_editor_submit(self) -> None:
        self._submit()

    @on(Button.Pressed, "#as-send")
    def _on_send(self) -> None:
        self._submit()

    @on(Button.Pressed, "#as-stop")
    def _on_stop(self) -> None:
        if self._running_reply_id is not None:
            self.post_message(
                self.InterruptRequested(self._running_reply_id),
            )


class HitlUI(Vertical):
    """Bottom-docked modal controls for pending tool interactions."""

    class Confirmed(Message):
        def __init__(self, value: UserConfirmResultEvent) -> None:
            super().__init__()
            self.value = value

    class InterruptRequested(Message):
        def __init__(self, reply_id: str) -> None:
            super().__init__()
            self.reply_id = reply_id

    def __init__(self) -> None:
        super().__init__(classes="as-hitl")
        self._pending: list[tuple[str, str, ToolCallBlock]] = []
        self._submitting = False

    def compose(self) -> ComposeResult:
        yield Static(id="as-hitl-title", classes="as-hitl-title")
        yield Static(id="as-hitl-body", classes="as-hitl-body")
        with Horizontal(classes="as-hitl-actions"):
            yield Button("Allow", id="as-allow", variant="success")
            yield Button("Always allow", id="as-always", variant="primary")
            yield Button("Deny", id="as-deny", variant="error")
            yield Button("Abort reply", id="as-hitl-abort", variant="warning")

    def set_pending(
        self,
        pending: list[tuple[str, str, ToolCallBlock]],
    ) -> None:
        previous = self._pending[0][2].id if self._pending else None
        current = pending[0][2].id if pending else None
        self._pending = pending
        if previous != current:
            self._submitting = False
        self.display = bool(pending)
        if self.is_mounted and pending:
            self._render_current()

    def focus_action(self) -> None:
        if not self.is_mounted or not self._pending:
            return
        selector = (
            "#as-hitl-abort"
            if self._pending[0][2].state == "submitted"
            else "#as-allow"
        )
        self.query_one(selector, Button).focus()

    def _render_current(self) -> None:
        _, agent_name, tool_call = self._pending[0]
        waiting_external = tool_call.state == "submitted"
        index = 1
        total = len(self._pending)
        state = (
            "Waiting for external execution"
            if waiting_external
            else "Approval required"
        )
        self.query_one("#as-hitl-title", Static).update(
            f"{state} · {agent_name} · {index}/{total}",
        )
        body = Text(f"{tool_call.name}\n", style="bold")
        body.append(tool_call.input or "{}", style="dim")
        if tool_call.suggested_rules:
            body.append("\n\nSuggested permission rules:", style="yellow")
            for rule in tool_call.suggested_rules:
                suffix = f" ({rule.rule_content})" if rule.rule_content else ""
                body.append(
                    f"\n  {rule.behavior.value} {rule.tool_name}{suffix}",
                    style="dim",
                )
        self.query_one("#as-hitl-body", Static).update(body)

        confirmable = not waiting_external and not self._submitting
        self.query_one("#as-allow", Button).display = not waiting_external
        always = self.query_one("#as-always", Button)
        always.display = (
            bool(tool_call.suggested_rules) and not waiting_external
        )
        self.query_one("#as-deny", Button).display = not waiting_external
        for selector in ("#as-allow", "#as-always", "#as-deny"):
            self.query_one(selector, Button).disabled = not confirmable
        self.query_one("#as-hitl-abort", Button).disabled = self._submitting

    def _confirm(self, confirmed: bool, always: bool = False) -> None:
        if not self._pending or self._submitting:
            return
        reply_id, _, tool_call = self._pending[0]
        if tool_call.state == "submitted":
            return
        self._submitting = True
        self._render_current()
        self.post_message(
            self.Confirmed(
                UserConfirmResultEvent(
                    reply_id=reply_id,
                    confirm_results=[
                        ConfirmResult(
                            confirmed=confirmed,
                            tool_call=tool_call,
                            rules=(
                                tool_call.suggested_rules
                                if confirmed and always
                                else None
                            ),
                        ),
                    ],
                ),
            ),
        )

    @on(Button.Pressed, "#as-allow")
    def _on_allow(self) -> None:
        self._confirm(True)

    @on(Button.Pressed, "#as-always")
    def _on_always(self) -> None:
        self._confirm(True, always=True)

    @on(Button.Pressed, "#as-deny")
    def _on_deny(self) -> None:
        self._confirm(False)

    @on(Button.Pressed, "#as-hitl-abort")
    def _on_abort(self) -> None:
        if self._pending and not self._submitting:
            self._submitting = True
            self._render_current()
            self.post_message(self.InterruptRequested(self._pending[0][0]))

    def on_key(self, event: events.Key) -> None:
        if not self._pending or self._submitting:
            return
        if event.key in ("y", "1"):
            event.stop()
            self._confirm(True)
        elif event.key in ("a", "2") and self._pending[0][2].suggested_rules:
            event.stop()
            self._confirm(True, always=True)
        elif event.key in ("n", "3"):
            event.stop()
            self._confirm(False)


class ChatUI(Widget):
    """Messages, composer and HITL controls without an Agent dependency."""

    input_enabled = reactive(True)

    DEFAULT_CSS = """
    ChatUI {
        width: 100%;
        height: 100%;
        layout: vertical;
    }

    ChatUI > MessagesUI {
        height: 1fr;
    }

    ComposerUI, HitlUI {
        width: 100%;
        height: auto;
        min-height: 4;
        padding: 0 1;
        border-top: solid $border;
        background: $surface;
    }

    #as-composer-input {
        width: 100%;
        height: auto;
        min-height: 3;
        max-height: 10;
        border: none;
    }

    .as-composer-actions, .as-hitl-actions {
        width: 100%;
        height: 3;
        align-vertical: middle;
    }

    .as-composer-hint {
        width: 1fr;
        height: 1;
        color: $text-muted;
    }

    .as-composer-actions Button, .as-hitl-actions Button {
        min-width: 8;
        width: auto;
        height: 3;
        margin-left: 1;
    }

    .as-hitl-title {
        height: 1;
        text-style: bold;
        color: $warning;
    }

    .as-hitl-body {
        height: auto;
        max-height: 10;
        overflow-y: auto;
        padding: 1;
        background: $boost;
    }
    """

    class Submitted(Message):
        def __init__(self, msg: Msg) -> None:
            super().__init__()
            self.msg = msg

    class Confirmed(Message):
        def __init__(self, value: UserConfirmResultEvent) -> None:
            super().__init__()
            self.value = value

    class InterruptRequested(Message):
        def __init__(self, reply_id: str) -> None:
            super().__init__()
            self.reply_id = reply_id

    def __init__(
        self,
        messages: Sequence[Msg] = (),
        *,
        user_name: str = "user",
        input_enabled: bool = True,
        show_thinking: bool = True,
        show_usage: bool = True,
        id: str | None = None,  # pylint: disable=redefined-builtin
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self._initial_messages = messages
        self.user_name = user_name
        self.show_thinking = show_thinking
        self.show_usage = show_usage
        self.input_enabled = input_enabled
        self._hitl_active = False

    def compose(self) -> ComposeResult:
        yield MessagesUI(
            self._initial_messages,
            show_thinking=self.show_thinking,
            show_usage=self.show_usage,
            id="as-messages",
        )
        yield ComposerUI()
        hitl = HitlUI()
        hitl.display = False
        yield hitl

    def on_mount(self) -> None:
        self._sync_interaction_area()

    @property
    def messages(self) -> tuple[Msg, ...]:
        return self.query_one(MessagesUI).messages

    def _current_messages(self) -> tuple[Msg, ...]:
        return self.query_one(MessagesUI)._current_messages()

    async def set_messages(self, messages: Sequence[Msg]) -> None:
        await self.query_one(MessagesUI).set_messages(messages)
        self._sync_interaction_area()

    def feed(self, item: AgentEvent | Msg) -> None:
        self.query_one(MessagesUI).feed(item)
        self.call_later(self._sync_interaction_area)

    def watch_input_enabled(self, enabled: bool) -> None:
        if self.is_mounted:
            self.query_one(ComposerUI).set_enabled(enabled)

    def _pending_tools(self) -> list[tuple[str, str, ToolCallBlock]]:
        pending: list[tuple[str, str, ToolCallBlock]] = []
        for message in self._current_messages():
            if message.role != "assistant" or message.finished_at is not None:
                continue
            for block in message.content:
                if isinstance(block, ToolCallBlock) and block.state in (
                    "asking",
                    "submitted",
                ):
                    pending.append((message.id, message.name, block))
        return pending

    def _latest_running_reply_id(self) -> str | None:
        for message in reversed(self._current_messages()):
            if message.role == "assistant" and message.finished_at is None:
                return message.id
        return None

    def _sync_interaction_area(self) -> None:
        composer = self.query_one(ComposerUI)
        hitl = self.query_one(HitlUI)
        pending = self._pending_tools()
        was_hitl_active = self._hitl_active
        self._hitl_active = bool(pending)
        hitl.set_pending(pending)
        composer.display = not pending
        composer.set_enabled(self.input_enabled and not self.disabled)
        composer.set_running_reply(self._latest_running_reply_id())
        if self._hitl_active and not was_hitl_active:
            self.call_later(hitl.focus_action)
        elif was_hitl_active and not self._hitl_active:
            self.call_later(composer.focus_editor)

    @on(ComposerUI.Submitted)
    def _on_composer_submitted(self, event: ComposerUI.Submitted) -> None:
        msg = UserMsg(name=self.user_name, content=event.text)
        self.query_one(MessagesUI).feed(msg)
        self.post_message(self.Submitted(msg))

    @on(ComposerUI.InterruptRequested)
    def _on_composer_interrupt(
        self,
        event: ComposerUI.InterruptRequested,
    ) -> None:
        self.post_message(self.InterruptRequested(event.reply_id))

    @on(HitlUI.Confirmed)
    def _on_hitl_confirmed(self, event: HitlUI.Confirmed) -> None:
        self.post_message(self.Confirmed(event.value))

    @on(HitlUI.InterruptRequested)
    def _on_hitl_interrupt(
        self,
        event: HitlUI.InterruptRequested,
    ) -> None:
        self.post_message(self.InterruptRequested(event.reply_id))


__all__ = ["ChatUI"]
