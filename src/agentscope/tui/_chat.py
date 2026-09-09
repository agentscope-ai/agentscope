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
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..event import AgentEvent, ConfirmResult, UserConfirmResultEvent
from ..message import Msg, ToolCallBlock, UserMsg
from ._messages import MessagesUI


class _ComposerTextArea(TextArea):
    """A TextArea where Enter submits and Shift+Enter inserts a newline."""

    class SubmitRequested(Message):
        """Request submission of the current editor contents."""

    class InterruptRequested(Message):
        """Request interruption of the currently running reply."""

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
        if event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            self.post_message(self.InterruptRequested())
            return
        await super()._on_key(event)


class ComposerUI(Vertical):
    """Keyboard-driven multiline composer with targeted interruption."""

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
        yield Static(id="as-composer-hint", classes="as-composer-hint")

    @property
    def draft(self) -> str:
        return self.query_one(_ComposerTextArea).text

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not self.is_mounted:
            return
        editor = self.query_one(_ComposerTextArea)
        editor.disabled = not enabled
        self._update_hint()

    def set_running_reply(self, reply_id: str | None) -> None:
        self._running_reply_id = reply_id
        if self.is_mounted:
            self._update_hint()

    def _update_hint(self) -> None:
        if not self._enabled:
            hint = "Input disabled"
        else:
            hint = "Enter send · Shift+Enter newline"
            if self._running_reply_id is not None:
                hint += " · Ctrl+C interrupt"
        self.query_one("#as-composer-hint", Static).update(hint)

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

    @on(_ComposerTextArea.InterruptRequested)
    def _on_interrupt(self) -> None:
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
        yield OptionList(id="as-hitl-options", classes="as-hitl-options")
        yield Static(id="as-hitl-hint", classes="as-hitl-hint")

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
        self.query_one(OptionList).focus()

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
        self.query_one("#as-hitl-body", Static).update(body)

        options = self.query_one(OptionList)
        choices: list[Option] = []
        if not waiting_external:
            choices.append(Option("Allow once", id="allow"))
            if tool_call.suggested_rules:
                rules = "; ".join(
                    f"{rule.behavior.value} {rule.tool_name}"
                    + (f" ({rule.rule_content})" if rule.rule_content else "")
                    for rule in tool_call.suggested_rules
                )
                choices.append(
                    Option(f"Always allow with {rules}", id="always"),
                )
            choices.append(Option("Deny", id="deny"))
        choices.append(Option("Interrupt reply", id="interrupt"))
        options.clear_options().add_options(choices)
        options.highlighted = 0
        options.disabled = self._submitting
        hint = (
            "Submitting…"
            if self._submitting
            else "↑/↓ select · Enter confirm · Ctrl+C interrupt"
        )
        self.query_one("#as-hitl-hint", Static).update(hint)

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

    def _interrupt(self) -> None:
        if self._pending and not self._submitting:
            self._submitting = True
            self._render_current()
            self.post_message(self.InterruptRequested(self._pending[0][0]))

    @on(OptionList.OptionSelected, "#as-hitl-options")
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "allow":
            self._confirm(True)
        elif event.option_id == "always":
            self._confirm(True, always=True)
        elif event.option_id == "deny":
            self._confirm(False)
        elif event.option_id == "interrupt":
            self._interrupt()

    def on_key(self, event: events.Key) -> None:
        if not self._pending or self._submitting:
            return
        if event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            self._interrupt()


class ChatUI(Widget):
    """Messages, composer and HITL controls without an Agent dependency."""

    input_enabled = reactive(True)

    DEFAULT_CSS = """
    ChatUI {
        width: 100%;
        height: 100%;
        layout: vertical;
        padding: 0 1;
        background: transparent;
    }

    ChatUI > MessagesUI {
        height: 1fr;
    }

    ComposerUI, HitlUI {
        width: 100%;
        height: auto;
        min-height: 3;
        padding: 0;
        background: transparent;
    }

    ComposerUI {
        border-top: solid $foreground 20%;
        border-bottom: solid $foreground 20%;
    }

    #as-composer-input {
        width: 100%;
        height: auto;
        min-height: 3;
        max-height: 10;
        border: none;
        padding: 0;
        background: transparent;
    }

    #as-composer-input .text-area--cursor-line {
        background: transparent;
    }

    .as-composer-hint, .as-hitl-hint {
        width: 100%;
        height: 1;
        color: $text-muted;
    }

    .as-hitl-options {
        width: 100%;
        height: auto;
        max-height: 6;
        border: none;
        padding: 0;
        background: transparent;
    }

    .as-hitl-options > .option-list--option-highlighted,
    .as-hitl-options:focus > .option-list--option-highlighted {
        color: $foreground;
        background: $foreground 10%;
        text-style: bold;
    }

    .as-hitl-title {
        height: 1;
        text-style: bold;
        color: $foreground;
    }

    .as-hitl-body {
        height: auto;
        max-height: 10;
        overflow-y: auto;
        padding: 0;
        background: transparent;
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
        show_usage: bool = False,
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
