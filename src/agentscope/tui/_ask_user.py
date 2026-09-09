# -*- coding: utf-8 -*-
"""Keyboard-first AskUser form for externally executed questions."""

# Textual callbacks inherit their documentation from the owning widget.
# pylint: disable=missing-function-docstring,protected-access
# pylint: disable=attribute-defined-outside-init

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ..event import ExternalExecutionResultEvent
from ..message import ToolCallBlock, ToolResultBlock, ToolResultState
from ..tool import AskUserAnswer, AskUserMetadata, AskUserParams


@dataclass
class _Answer:
    """Mutable answer state while the form is being completed."""

    selected: list[str] = field(default_factory=list)
    other: str | None = None


class _QuestionOptions(OptionList):
    """Option list whose mouse hover follows its keyboard highlight."""

    def _on_mouse_move(self, event: events.MouseMove) -> None:
        super()._on_mouse_move(event)
        hovered = event.style.meta.get("option")
        if isinstance(hovered, int):
            self.highlighted = hovered


class AskUserUI(Vertical):
    """Render an AskUser call and return a schema-valid external result."""

    DEFAULT_CSS = """
    AskUserUI {
        width: 100%;
        height: auto;
        max-height: 80%;
        overflow-y: auto;
        scrollbar-visibility: hidden;
        padding: 0;
        background: transparent;
    }

    .as-ask-user-rule {
        width: 100%;
        height: 1;
        color: #766b5b;
        background: transparent;
    }

    .as-ask-user-steps,
    .as-ask-user-question,
    .as-ask-user-context,
    .as-ask-user-preview,
    .as-ask-user-hint {
        width: 100%;
        height: auto;
        padding: 0 1;
        background: transparent;
    }

    .as-ask-user-steps {
        height: 1;
        color: $text-muted;
    }

    .as-ask-user-question {
        margin-top: 1;
        color: $foreground;
        text-style: bold;
    }

    .as-ask-user-context,
    .as-ask-user-preview,
    .as-ask-user-hint {
        color: $text-muted;
    }

    .as-ask-user-context,
    .as-ask-user-preview {
        margin-top: 1;
    }

    .as-ask-user-options {
        width: 100%;
        height: auto;
        max-height: 14;
        margin-top: 1;
        padding: 0 1;
        border: none;
        scrollbar-visibility: hidden;
        background: transparent;
    }

    .as-ask-user-options:focus {
        border: none;
        background: transparent;
        background-tint: transparent;
    }

    .as-ask-user-options > .option-list--option-highlighted,
    .as-ask-user-options:focus > .option-list--option-highlighted,
    .as-ask-user-options > .option-list--option-hover {
        color: #d8b66f;
        background: transparent;
    }

    .as-ask-user-other {
        width: 100%;
        height: 1;
        min-height: 1;
        max-height: 5;
        padding: 0 1;
        border: none;
        scrollbar-visibility: hidden;
        background: transparent;
    }

    .as-ask-user-other:focus {
        border: none;
        background: transparent;
    }

    .as-ask-user-hint {
        height: 1;
    }
    """

    class Submitted(Message):
        """A completed AskUser external execution result."""

        def __init__(
            self,
            value: ExternalExecutionResultEvent,
            tool_call_id: str,
        ) -> None:
            super().__init__()
            self.value = value
            self.tool_call_id = tool_call_id

    class InterruptRequested(Message):
        """Request interruption of the parked reply."""

        def __init__(self, reply_id: str) -> None:
            super().__init__()
            self.reply_id = reply_id

    def __init__(self) -> None:
        super().__init__(classes="as-ask-user")
        self._pending: list[tuple[str, str, ToolCallBlock]] = []
        self._params: AskUserParams | None = None
        self._answers: list[_Answer] = []
        self._question_index = 0
        self._editing_other = False
        self._submitting = False
        self._error: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("─" * 4096, classes="as-ask-user-rule")
        yield Static(classes="as-ask-user-steps")
        yield Static(classes="as-ask-user-question")
        context = Static(classes="as-ask-user-context")
        context.display = False
        yield context
        yield _QuestionOptions(classes="as-ask-user-options")
        preview = Static(classes="as-ask-user-preview")
        preview.display = False
        yield preview
        other = Input(
            placeholder="Type your answer…",
            classes="as-ask-user-other",
        )
        other.display = False
        yield other
        yield Static(classes="as-ask-user-hint")

    def set_pending(
        self,
        pending: list[tuple[str, str, ToolCallBlock]],
    ) -> None:
        previous_id = self._pending[0][2].id if self._pending else None
        current_id = pending[0][2].id if pending else None
        self._pending = pending
        self.display = bool(pending)
        if previous_id != current_id:
            self._load_current()
        if self.is_mounted and pending:
            self._render_form()

    def _load_current(self) -> None:
        self._params = None
        self._answers = []
        self._question_index = 0
        self._editing_other = False
        self._submitting = False
        self._error = None
        if not self._pending:
            return
        try:
            self._params = AskUserParams.model_validate_json(
                self._pending[0][2].input,
            )
        except ValidationError as error:
            self._error = f"Invalid AskUser input: {error.errors()[0]['msg']}"
        if self._params is not None:
            self._answers = [_Answer() for _ in self._params.questions]

    @property
    def _question(self) -> Any:
        assert self._params is not None
        return self._params.questions[self._question_index]

    def focus_action(self) -> None:
        if not self.is_mounted or not self._pending:
            return
        if self._editing_other:
            self.query_one(Input).focus()
        elif self._params is not None:
            self.query_one(OptionList).focus()

    def _render_form(self) -> None:
        steps = self.query_one(".as-ask-user-steps", Static)
        question_widget = self.query_one(".as-ask-user-question", Static)
        context_widget = self.query_one(".as-ask-user-context", Static)
        preview_widget = self.query_one(".as-ask-user-preview", Static)
        options = self.query_one(OptionList)
        other = self.query_one(Input)
        hint = self.query_one(".as-ask-user-hint", Static)

        if self._error is not None:
            steps.update(Text("AskUser", style="bold #d8b66f"))
            question_widget.update(Text(self._error, style="bold red"))
            context_widget.display = False
            preview_widget.display = False
            options.display = False
            other.display = False
            hint.update("Ctrl+C interrupt")
            return

        assert self._params is not None
        question = self._question
        steps.update(self._steps_text())
        question_widget.update(question.question)
        context_widget.display = bool(question.context)
        if question.context:
            context_widget.update(question.context)

        choices = [
            Option("", id=f"option:{index}")
            for index, _ in enumerate(question.options)
        ]
        choices.append(Option("", id="other"))
        if question.multi_select:
            choices.append(Option("", id="continue"))
        options.clear_options().add_options(choices)
        options.display = not self._editing_other
        options.highlighted = 0
        options.disabled = self._submitting
        other.display = self._editing_other
        other.disabled = self._submitting
        self._refresh_options()
        self._update_preview()
        hint.update(
            "Submitting…"
            if self._submitting
            else (
                "Enter submit answer · Esc choices · Ctrl+C interrupt"
                if self._editing_other
                else (
                    "↑/↓ select · Enter choose · ← previous "
                    "· Ctrl+C interrupt"
                )
            ),
        )

    def _steps_text(self) -> Text:
        assert self._params is not None
        text = Text("←  ", style="dim")
        for index, question in enumerate(self._params.questions):
            answered = bool(
                self._answers[index].selected
                or self._answers[index].other is not None,
            )
            marker = "■" if answered else "□"
            style = (
                "bold #d8b66f underline"
                if index == self._question_index
                else "dim"
            )
            text.append(f"{marker} {question.header}", style=style)
            text.append("  ")
        submit_style = (
            "bold #d8b66f"
            if self._question_index == len(self._params.questions) - 1
            else "dim"
        )
        text.append("✓ Submit  →", style=submit_style)
        return text

    def _option_prompt(self, index: int) -> Text:
        question = self._question
        highlighted = self.query_one(OptionList).highlighted == index
        marker = "→" if highlighted else " "
        if index < len(question.options):
            option = question.options[index]
            selected = (
                option.label in self._answers[self._question_index].selected
            )
            checkbox = (
                ("■ " if selected else "□ ") if question.multi_select else ""
            )
            prompt = Text(f"{marker} {index + 1}. {checkbox}")
            prompt.append(
                option.label,
                style="bold #d8b66f" if highlighted else "",
            )
            prompt.append(f"\n     {option.description}", style="dim")
            return prompt
        if index == len(question.options):
            prompt = Text(f"{marker} {index + 1}. ")
            prompt.append(
                "Type something.",
                style="bold #d8b66f" if highlighted else "",
            )
            return prompt
        prompt = Text(f"{marker} ✓ Continue")
        if highlighted:
            prompt.stylize("bold #d8b66f")
        return prompt

    def _refresh_options(self) -> None:
        if self._params is None:
            return
        options = self.query_one(OptionList)
        for index in range(options.option_count):
            options.replace_option_prompt_at_index(
                index,
                self._option_prompt(index),
            )

    def _update_preview(self) -> None:
        preview = self.query_one(".as-ask-user-preview", Static)
        if self._editing_other or self._params is None:
            preview.display = False
            return
        highlighted = self.query_one(OptionList).highlighted
        question = self._question
        value = (
            question.options[highlighted].preview
            if highlighted is not None
            and highlighted < len(question.options)
            and not question.multi_select
            else None
        )
        preview.display = bool(value)
        if value:
            content = Text("Preview\n", style="bold #d8b66f")
            content.append(value, style="dim")
            preview.update(content)

    def _complete_question(self) -> None:
        if self._question_index + 1 < len(self._answers):
            self._question_index += 1
            self._editing_other = False
            self._render_form()
            self.call_later(self.focus_action)
            return
        self._submit()

    def _submit(self) -> None:
        if self._submitting or not self._pending or self._params is None:
            return
        answers = [
            AskUserAnswer(
                question=question.question,
                selected=answer.selected,
                other=answer.other,
            )
            for question, answer in zip(
                self._params.questions,
                self._answers,
                strict=True,
            )
        ]
        metadata = AskUserMetadata(answers=answers).model_dump(mode="json")
        lines = []
        for answer in answers:
            value = answer.other or ", ".join(answer.selected)
            lines.append(f"{answer.question}\n{value}")
        reply_id, _, tool_call = self._pending[0]
        result = ToolResultBlock(
            id=tool_call.id,
            name=tool_call.name,
            output="\n\n".join(lines),
            state=ToolResultState.SUCCESS,
            metadata=metadata,
        )
        self._submitting = True
        self._render_form()
        self.post_message(
            self.Submitted(
                ExternalExecutionResultEvent(
                    reply_id=reply_id,
                    execution_results=[result],
                ),
                tool_call.id,
            ),
        )

    @on(OptionList.OptionSelected, ".as-ask-user-options")
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._submitting or self._params is None:
            return
        option_id = str(event.option_id)
        question = self._question
        answer = self._answers[self._question_index]
        if option_id.startswith("option:"):
            index = int(option_id.split(":", maxsplit=1)[1])
            label = question.options[index].label
            answer.other = None
            if question.multi_select:
                if label in answer.selected:
                    answer.selected.remove(label)
                else:
                    answer.selected.append(label)
                self._refresh_options()
            else:
                answer.selected = [label]
                self._complete_question()
        elif option_id == "other":
            answer.selected = []
            self._editing_other = True
            self._render_form()
            self.call_later(self.focus_action)
        elif option_id == "continue" and answer.selected:
            self._complete_question()

    @on(OptionList.OptionHighlighted, ".as-ask-user-options")
    def _on_option_highlighted(self) -> None:
        if self._params is None or not self.display:
            return
        self._refresh_options()
        self._update_preview()

    @on(Input.Submitted, ".as-ask-user-other")
    def _on_other_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        answer = self._answers[self._question_index]
        answer.selected = []
        answer.other = value
        self._complete_question()

    def on_key(self, event: events.Key) -> None:
        if not self._pending or self._submitting:
            return
        if event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            self._submitting = True
            self.post_message(self.InterruptRequested(self._pending[0][0]))
        elif event.key == "escape" and self._editing_other:
            event.stop()
            event.prevent_default()
            self._editing_other = False
            self._render_form()
            self.call_later(self.focus_action)
        elif event.key == "left" and not self._editing_other:
            if self._question_index > 0:
                event.stop()
                event.prevent_default()
                self._question_index -= 1
                self._render_form()
                self.call_later(self.focus_action)
