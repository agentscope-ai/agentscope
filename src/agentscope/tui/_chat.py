# -*- coding: utf-8 -*-
"""Composable chat widget with input and human-in-the-loop controls."""

# Textual handlers and nested message payloads are intentionally tiny and
# inherit their behavioral documentation from their owning widgets.
# pylint: disable=missing-function-docstring,missing-class-docstring
# pylint: disable=attribute-defined-outside-init

from __future__ import annotations

from typing import Sequence

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Collapsible, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..event import (
    AgentEvent,
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from ..message import Msg, ToolCallBlock, ToolCallState, UserMsg
from ._ask_user import AskUserUI
from ._messages import MessagesUI

_ASK_USER_TOOL_NAME = "AskUser"


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


class _HitlOptionList(OptionList):
    """Option list whose mouse hover follows the keyboard highlight."""

    def _on_mouse_move(self, event: events.MouseMove) -> None:
        super()._on_mouse_move(event)
        hovered = event.style.meta.get("option")
        if isinstance(hovered, int):
            self.highlighted = hovered


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
        yield Static("─" * 4096, classes="as-section-rule")
        yield _ComposerTextArea(
            placeholder="Message the agent…",
            id="as-composer-input",
            soft_wrap=True,
            compact=True,
        )
        yield Static("─" * 4096, classes="as-section-rule")
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
            hint = "Enter send · Shift+Enter newline · /exit quit"
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
        self._choice_labels: list[str] = []
        self._submitting = False

    def compose(self) -> ComposeResult:
        yield Static("─" * 4096, classes="as-section-rule")
        yield Static(id="as-hitl-title", classes="as-hitl-title")
        yield Static(id="as-hitl-body", classes="as-hitl-body")
        yield _HitlOptionList(
            id="as-hitl-options",
            classes="as-hitl-options",
        )
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
        waiting_external = tool_call.state == ToolCallState.SUBMITTED
        total = len(self._pending)
        state = (
            "Waiting for external execution"
            if waiting_external
            else "Approval required"
        )
        self.query_one("#as-hitl-title", Static).update(
            f"{state} · {agent_name} · 1/{total}",
        )
        body = Text(f"{tool_call.name}\n\n", style="bold")
        arguments = tool_call.input or "{}"
        body.append(
            "\n".join(f"  {line}" for line in arguments.splitlines()),
            style="dim",
        )
        self.query_one("#as-hitl-body", Static).update(body)

        options = self.query_one(OptionList)
        choice_specs: list[tuple[str, str]] = []
        if not waiting_external:
            choice_specs.append(("Allow once", "allow"))
            if tool_call.suggested_rules:
                rules = "; ".join(
                    f"{rule.behavior.value} {rule.tool_name}"
                    + (f" ({rule.rule_content})" if rule.rule_content else "")
                    for rule in tool_call.suggested_rules
                )
                choice_specs.append(
                    (f"Always allow with {rules}", "always"),
                )
            choice_specs.append(("Deny", "deny"))
        choice_specs.append(("Interrupt reply", "interrupt"))
        self._choice_labels = [label for label, _ in choice_specs]
        choices = [
            Option(
                self._choice_prompt(choice_index, label, choice_index == 0),
                id=key,
            )
            for choice_index, (label, key) in enumerate(choice_specs)
        ]
        options.clear_options().add_options(choices)
        options.highlighted = 0
        self._refresh_choice_prompts()
        options.disabled = self._submitting
        hint = (
            "Submitting…"
            if self._submitting
            else "↑/↓ select · Enter confirm · Ctrl+C interrupt"
        )
        self.query_one("#as-hitl-hint", Static).update(hint)

    @staticmethod
    def _choice_prompt(index: int, label: str, selected: bool) -> str:
        marker = "→" if selected else " "
        return f"{marker} {index + 1}. {label}"

    def _refresh_choice_prompts(self) -> None:
        options = self.query_one(OptionList)
        for index, label in enumerate(self._choice_labels):
            options.replace_option_prompt_at_index(
                index,
                self._choice_prompt(
                    index,
                    label,
                    index == options.highlighted,
                ),
            )

    def _confirm(self, confirmed: bool, always: bool = False) -> None:
        if not self._pending or self._submitting:
            return
        reply_id, _, tool_call = self._pending[0]
        if tool_call.state == ToolCallState.SUBMITTED:
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

    @on(OptionList.OptionHighlighted, "#as-hitl-options")
    def _on_option_highlighted(self) -> None:
        self._refresh_choice_prompts()

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
        padding: 0;
        background: transparent;
    }

    ChatUI > MessagesUI {
        height: 1fr;
        padding: 0 1;
    }

    ComposerUI, HitlUI {
        width: 100%;
        height: auto;
        min-height: 3;
        padding: 0;
        background: transparent;
    }

    .as-section-rule {
        width: 100%;
        height: 1;
        color: #766b5b;
        background: transparent;
    }

    #as-composer-input {
        width: 100%;
        height: auto;
        min-height: 1;
        max-height: 10;
        scrollbar-visibility: hidden;
        border: none;
        padding: 0 1;
        background: transparent;
    }

    #as-composer-input .text-area--cursor-line {
        background: transparent;
    }

    .as-composer-hint, .as-hitl-hint {
        width: 100%;
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    .as-hitl-options {
        width: 100%;
        height: auto;
        max-height: 10;
        margin-top: 1;
        scrollbar-visibility: hidden;
        border: none;
        padding: 0 1;
        background: transparent;
    }

    .as-hitl-options:focus {
        border: none;
        background: transparent;
        background-tint: transparent;
    }

    .as-hitl-options > .option-list--option-highlighted,
    .as-hitl-options:focus > .option-list--option-highlighted {
        color: #d8b66f;
        background: transparent;
        text-style: bold;
    }

    .as-hitl-options > .option-list--option-hover {
        color: #d8b66f;
        background: transparent;
    }

    .as-hitl-title {
        height: 1;
        padding: 0 1;
        text-style: bold;
        color: $foreground;
    }

    .as-hitl-body {
        height: auto;
        max-height: 10;
        overflow-y: auto;
        scrollbar-visibility: hidden;
        padding: 0 1;
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

    class ExternalExecutionSubmitted(Message):
        def __init__(self, value: ExternalExecutionResultEvent) -> None:
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
        self._dismissed_external_call_ids: set[str] = set()

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
        ask_user = AskUserUI()
        ask_user.display = False
        yield ask_user

    def on_mount(self) -> None:
        self._sync_interaction_area()

    @property
    def messages(self) -> tuple[Msg, ...]:
        return self.query_one(MessagesUI).messages

    def _current_messages(self) -> tuple[Msg, ...]:
        return self.query_one(MessagesUI).current_messages()

    def is_reply_parked(self, reply_id: str) -> bool:
        """Whether a reply is waiting for confirmation or external input."""
        return any(
            message.id == reply_id
            and any(
                isinstance(block, ToolCallBlock)
                and block.state
                in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
                for block in message.content
            )
            for message in self._current_messages()
        )

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
        submitted_ids: set[str] = set()
        for message in self._current_messages():
            if message.role != "assistant" or message.finished_at is not None:
                continue
            for block in message.content:
                if (
                    isinstance(block, ToolCallBlock)
                    and block.state == ToolCallState.SUBMITTED
                ):
                    submitted_ids.add(block.id)
                if (
                    isinstance(block, ToolCallBlock)
                    and block.state
                    in (
                        ToolCallState.ASKING,
                        ToolCallState.SUBMITTED,
                    )
                    and block.id not in self._dismissed_external_call_ids
                ):
                    pending.append((message.id, message.name, block))
        self._dismissed_external_call_ids.intersection_update(submitted_ids)
        return pending

    def _latest_running_reply_id(self) -> str | None:
        for message in reversed(self._current_messages()):
            if message.role == "assistant" and message.finished_at is None:
                return message.id
        return None

    def _sync_interaction_area(self) -> None:
        composer = self.query_one(ComposerUI)
        hitl = self.query_one(HitlUI)
        ask_user = self.query_one(AskUserUI)
        pending = self._pending_tools()
        ask_pending: list[tuple[str, str, ToolCallBlock]] = []
        hitl_pending: list[tuple[str, str, ToolCallBlock]] = []
        ask_user_first = bool(pending and self._is_ask_user(pending[0][2]))
        active_pending = ask_pending if ask_user_first else hitl_pending
        for item in pending:
            if self._is_ask_user(item[2]) != ask_user_first:
                break
            active_pending.append(item)
        hitl.set_pending(hitl_pending)
        ask_user.set_pending(ask_pending)
        composer.display = not pending
        composer.set_enabled(self.input_enabled and not self.disabled)
        composer.set_running_reply(self._latest_running_reply_id())
        if ask_pending:
            self.call_later(ask_user.focus_action)
        elif hitl_pending:
            self.call_later(hitl.focus_action)
        else:
            self.call_later(composer.focus_editor)

    @staticmethod
    def _is_ask_user(tool_call: ToolCallBlock) -> bool:
        return (
            tool_call.name == _ASK_USER_TOOL_NAME
            and tool_call.state == ToolCallState.SUBMITTED
        )

    @on(Collapsible.Expanded)
    @on(Collapsible.Collapsed)
    def _on_collapsible_toggled(self) -> None:
        ask_user = self.query_one(AskUserUI)
        hitl = self.query_one(HitlUI)
        if ask_user.display:
            self.call_later(ask_user.focus_action)
        elif hitl.display:
            self.call_later(self.query_one(HitlUI).focus_action)
        else:
            self.call_later(self.query_one(ComposerUI).focus_editor)

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
        # Reconcile the outgoing decision immediately. The runtime may not
        # produce another event until a long-running allowed tool completes,
        # but the approval prompt has already been answered and must not stay
        # visible throughout that execution.
        self.feed(event.value)
        self.post_message(self.Confirmed(event.value))

    @on(AskUserUI.Submitted)
    def _on_ask_user_submitted(self, event: AskUserUI.Submitted) -> None:
        # Hide the completed form immediately. Feeding the outgoing external
        # result here would duplicate the ToolResultBlock when the runtime
        # streams its validated result back, so dismissal is tracked locally.
        self._dismissed_external_call_ids.add(event.tool_call_id)
        self._sync_interaction_area()
        self.post_message(self.ExternalExecutionSubmitted(event.value))

    @on(HitlUI.InterruptRequested)
    def _on_hitl_interrupt(
        self,
        event: HitlUI.InterruptRequested,
    ) -> None:
        self.post_message(self.InterruptRequested(event.reply_id))

    @on(AskUserUI.InterruptRequested)
    def _on_ask_user_interrupt(
        self,
        event: AskUserUI.InterruptRequested,
    ) -> None:
        self.post_message(self.InterruptRequested(event.reply_id))
