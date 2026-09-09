# -*- coding: utf-8 -*-
"""The asking user tool class."""
from typing import Any

from pydantic import BaseModel, Field

from .._base import ToolBase
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)

_HEADER_MAX_CHARS = 12


class _Option(BaseModel):
    """A single selectable option within a question."""

    label: str = Field(
        description=(
            "The display text for this option that the user will see and "
            "select. Should be concise (1-5 words) and clearly describe the "
            "choice."
        ),
    )

    description: str = Field(
        description=(
            "Explanation of what this option means or what will happen if "
            "chosen. Useful for providing context about trade-offs or "
            "implications."
        ),
    )

    preview: str | None = Field(
        default=None,
        description=(
            "Optional preview content rendered when this option is focused. "
            "Use for mockups, code snippets, or visual comparisons that help "
            "users compare options. Only supported in single-select questions."
        ),
    )


class _Question(BaseModel):
    """A single question presented to the user."""

    question: str = Field(
        description=(
            "The complete question to ask the user. Should be clear, "
            "specific, and end with a question mark. "
            'Example: "Which library should we use for date formatting?" '
            "If multi_select is true, phrase it accordingly, "
            'e.g. "Which features do you want to enable?"'
        ),
    )

    header: str = Field(
        max_length=_HEADER_MAX_CHARS,
        description=(
            f"Very short label displayed as a chip/tag "
            f"(max {_HEADER_MAX_CHARS} chars). "
            'Examples: "Auth method", "Library", "Approach".'
        ),
    )

    context: str | None = Field(
        default=None,
        description=(
            "What the user should look at while answering — the draft "
            "being reviewed, the two designs being compared, the error "
            "that prompted the question. Shown above the options."
        ),
    )

    options: list[_Option] = Field(
        min_length=2,
        max_length=4,
        description=(
            "The available choices for this question. Must have 2-4 options. "
            "Each option should be a distinct, mutually exclusive choice "
            "(unless multi_select is enabled). There should be no 'Other' "
            "option — that will be provided automatically."
        ),
    )

    multi_select: bool = Field(
        default=False,
        description=(
            "Set to true to allow the user to select multiple options instead "
            "of just one. Use when choices are not mutually exclusive. "
            "Note: preview is only supported when multi_select is false."
        ),
    )


class AskUserAnswer(BaseModel):
    """One question's answer, as the caller must return it.

    An entry of :attr:`AskUserMetadata.answers`; a frontend building
    them one at a time has this to build them with.
    """

    question: str = Field(
        description="The question this answers, verbatim.",
    )

    selected: list[str] = Field(
        default_factory=list,
        description="Labels of the options the user chose.",
    )

    other: str | None = Field(
        default=None,
        description=(
            "What the user typed instead of choosing, when they answered "
            "in their own words."
        ),
    )


class AskUserMetadata(BaseModel):
    """What the caller must put in ``ToolResultBlock.metadata``.

    ``output`` is for the model to read; this is the half a program may
    branch on — a step deciding whether its work was approved cannot do
    that on prose.
    """

    answers: list[AskUserAnswer]


class AskUserParams(BaseModel):
    """What a caller asks for — the questions, and their options.

    A model fills this in from :attr:`AskUser.input_schema`; a program
    building the call itself has this to build it with."""

    questions: list[_Question] = Field(
        min_length=1,
        max_length=4,
        description=(
            "Questions to ask the user (1-4 questions). Question texts must "
            "be unique; option labels must be unique within each question."
        ),
    )


class AskUser(ToolBase):
    """The tool to collect information from the user via multiple-choice
    questions.

    External by nature: only whatever is driving the agent can put a
    question in front of a person, so the agent yields the call and waits
    for the answer to come back rather than executing anything itself.
    """

    name: str = "AskUser"
    """The tool name."""

    # pylint: disable=line-too-long
    description: str = """Asks the user multiple-choice questions to gather information, clarify ambiguity, understand preferences, make decisions, or offer choices.

Use this tool when you need to ask the user questions during execution:
1. Gather user preferences or requirements.
2. Clarify ambiguous instructions before proceeding.
3. Get decisions on implementation choices as you work.
4. Offer the user a set of directions to take.

## Usage rules
- You may batch 1–4 questions in a single call; batch related questions together.
- Each question must have 2–4 options. Option labels must be unique within a question.
- Question texts must be unique across the batch.
- Users will always be able to select "Other" to provide free-text input — do not add an "Other" option yourself.
- Use `multi_select: true` only when the choices are genuinely not mutually exclusive.
- If you recommend a specific option, place it first and append "(Recommended)" to its label.
- The `preview` field on an option renders a side-by-side visual comparison; only use it for concrete artifacts (code snippets, ASCII mockups, config examples) where seeing the content helps the user choose. Preview is only supported for single-select questions.
- Put whatever the user must look at to answer — a draft, a diff, an error — in `context` rather than in the question text.

## What comes back
`output` is written for you to read. The caller also returns the same answers in `ToolResultBlock.metadata`, shaped by `AskUserMetadata`, for programs that must branch on the choice rather than read prose."""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = AskUserParams.model_json_schema()

    metadata_schema: dict[
        str,
        Any,
    ] | None = AskUserMetadata.model_json_schema()

    is_read_only: bool = True
    is_state_injected: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = True

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Never asks to ask.

        The call is answered by the user in the first place, so a
        confirmation in front of it would only be a prompt about a
        prompt.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="The AskUser tool is always permitted.",
        )
