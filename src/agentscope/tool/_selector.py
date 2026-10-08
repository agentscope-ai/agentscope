# -*- coding: utf-8 -*-
"""Shared contract for task-aware tool schema selection."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from ._types import ToolChoice


@dataclass
class ToolSelection:
    """One selection from the caller's current available tool snapshot."""

    tools: list[dict]
    """Unmodified schemas in their original candidate order."""

    schema_tokens: int
    """Estimated schema cost reported by the caller's token counter."""

    used_fallback: bool = False
    """Whether a retrieval failure caused all candidates to be restored."""

    fallback_reason: str | None = None
    """The retrieval failure description, if a fallback was used."""

    budget_exceeded: bool = False
    """Whether schema_tokens exceeds a supplied max_tokens budget."""


class ToolSelectorBase(ABC):
    """Select model-visible schemas without registering or executing tools.

    The caller supplies a fresh snapshot of eligible tools. Selection never
    reactivates unavailable tools or replaces execution permission checks.
    The returned snapshot must be reused for input accounting and inference.
    """

    @abstractmethod
    async def select(
        self,
        query: str | None,
        tools: list[dict],
        *,
        count_tokens: Callable[[list[dict]], Awaitable[int]],
        max_tokens: int | None = None,
        required_tools: Sequence[str] = (),
        tool_choice: ToolChoice | None = None,
    ) -> ToolSelection:
        """Select a stable subset without mutating the candidate schemas.

        Args:
            query (`str | None`):
                Current task text. Empty text uses candidate order without
                an embedding request, while still respecting the budget.
            tools (`list[dict]`):
                Current schemas from the basic and activated tool groups.
            count_tokens (`Callable[[list[dict]], Awaitable[int]]`):
                Counter bound to one model and fixed messages. It reports
                the nonnegative incremental input cost of the supplied
                schemas relative to those messages without tools. This is
                an estimate when the model's counter is an estimate.
            max_tokens (`int | None`, optional):
                Nonnegative schema budget. None disables the budget. An
                implementation may exceed it only through its explicitly
                documented retrieval failure policy.
            required_tools (`Sequence[str]`, optional):
                Candidate names that selection must preserve. The caller
                includes required control and structured-output tools.
            tool_choice (`ToolChoice | None`, optional):
                Existing model constraint. Every name in its tools list,
                and a specifically forced tool name, must remain present.

        Returns:
            `ToolSelection`:
                Selected candidates, estimated cost and fallback status.

        Raises:
            `ValueError`:
                If a required name is absent, constraints are invalid, or
                required schemas exceed the normal-path budget.

        Token counter errors and task cancellation must propagate. Returning
        all tools after a retrieval failure does not guarantee that the
        model's complete input will fit its context window.
        """
        raise NotImplementedError
