# -*- coding: utf-8 -*-
"""Policies for selecting context to retain verbatim."""
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Awaitable, Callable

from ..message import Msg, ToolCallBlock, ToolResultBlock


def _select_fragments(
    context: list[Msg],
    positions: set[tuple[int, int]] | frozenset[tuple[int, int]],
) -> list[Msg]:
    """Copy selected blocks without losing message identity or empty messages.

    An empty message has the position ``(message_index, -1)`` so its metadata
    and accumulated usage belong to exactly one partition as well.
    """
    selected = []
    for msg_index, msg in enumerate(context):
        block_indexes = [
            index
            for index in range(len(msg.content))
            if (msg_index, index) in positions
        ]
        if not block_indexes and (msg_index, -1) not in positions:
            continue
        fragment = msg.model_copy(deep=True)
        fragment.content = [fragment.content[index] for index in block_indexes]
        selected.append(fragment)
    return selected


@dataclass
class ContextRetentionResult:
    """A context partition before summary generation or state mutation."""

    msgs_to_compress: list[Msg]
    """Message fragments selected for summarization, in original order."""

    msgs_to_reserve: list[Msg]
    """Message fragments retained verbatim, in original order."""

    reserved_tokens: int
    """Estimated retained input cost, including the counter's fixed prefix."""

    budget_exceeded: bool = False
    """Whether reserved_tokens exceeds the retained-input target."""


class ContextRetentionPolicy(ABC):
    """Partition existing context without modifying agent state.

    Every original content block belongs to exactly one partition. The same
    message may appear as distinct fragments in both partitions, with its
    identity and metadata preserved. Tool calls and their results stay
    together; unfinished calls must remain in the retained partition.

    Policies cannot recover data removed before partitioning, such as images
    already offloaded or tool results already truncated. Summary generation,
    offloading, usage accounting and state persistence remain Agent duties.
    """

    @abstractmethod
    async def split(
        self,
        context: list[Msg],
        *,
        token_budget: float,
        count_tokens: Callable[[list[Msg]], Awaitable[int]],
        unfinished_tool_call_ids: frozenset[str],
    ) -> ContextRetentionResult:
        """Declare a partition using the caller's frozen input budget.

        Args:
            context (`list[Msg]`):
                Original context. Neither messages nor their blocks may be
                mutated, duplicated, silently discarded or reordered.
            token_budget (`float`):
                Nonnegative target for retained input, including system
                prompt, existing summary and selected tool schemas. A fixed
                prefix or unfinished calls can exceed this soft target and
                must be reported.
                This is not a guarantee on the final model context size.
            count_tokens (`Callable[[list[Msg]], Awaitable[int]]`):
                Counter bound to one model and fixed system prompt, old
                summary and tool-selection snapshot. It counts the complete
                retained input with the supplied message fragments.
            unfinished_tool_call_ids (`frozenset[str]`):
                Calls whose results have not arrived and must remain visible.

        Returns:
            `ContextRetentionResult`:
                Independent message partitions and their budget status.

        Raises:
            `NotImplementedError`:
                Interface declaration only; supplied by implementations.

        Counter errors and cancellation propagate without changing state.
        The Agent must validate the complete input again after generating a
        new summary, which can be longer than the previous summary.
        """
        raise NotImplementedError


class PinnedContextRetentionPolicy(ContextRetentionPolicy):
    """Retain explicitly marked messages and a recent context window.

    A message is pinned only when its metadata value for metadata_key is
    True. Pinning covers the entire message and any matching tool call or
    result needed for protocol integrity. Block-level pinning and automatic
    importance scoring are not supported.

    After pins and unfinished calls, the policy visits content blocks from
    newest to oldest. A tool call and all its matching results are selected
    together. The recent window stops at the first group that does not fit;
    it does not skip expensive recent groups in favor of older messages.
    """

    def __init__(
        self,
        *,
        max_pinned_tokens: int,
        metadata_key: str = "context_retention",
    ) -> None:
        """Initialize message-level retention with explicit overflow errors.

        Args:
            max_pinned_tokens (`int`):
                Nonnegative cap on the incremental cost of pinned messages
                and their tool-pair closure, excluding the fixed prefix.
                Cost is max(0, count_tokens(pins) - count_tokens([])).
                If pins exceed this cap or cannot fit the retained-input
                target, raise ValueError and leave the original state intact.
                Pins must not silently enter a summary as a fallback.
                The empty pin set adds no strict constraint on the fixed
                prefix. Required unfinished calls are then retained even if
                doing so exceeds the soft target.
            metadata_key (`str`):
                Nonempty key in Msg.metadata. Markers use existing message
                persistence; the runtime policy is supplied again when an
                Agent is reconstructed, not serialized into ContextConfig.
        """
        if (
            not isinstance(max_pinned_tokens, int)
            or isinstance(max_pinned_tokens, bool)
            or max_pinned_tokens < 0
        ):
            raise ValueError(
                "max_pinned_tokens must be a nonnegative integer.",
            )
        if not isinstance(metadata_key, str) or not metadata_key:
            raise ValueError("metadata_key must be a nonempty string.")
        self.max_pinned_tokens = max_pinned_tokens
        self.metadata_key = metadata_key

    async def split(
        self,
        context: list[Msg],
        *,
        token_budget: float,
        count_tokens: Callable[[list[Msg]], Awaitable[int]],
        unfinished_tool_call_ids: frozenset[str],
    ) -> ContextRetentionResult:
        """Apply explicit pins and recent context without weakening pins.

        An empty compression partition must not trigger a second split that
        discards pins. The integration must report an uncompressible context
        if it still exceeds the model input threshold.

        To preserve state on a pin-budget error, the Agent must also defer
        other compression mutations, including image limiting, until the
        pin checks succeed, or prepare them on a separate state snapshot.
        """
        if (
            not isinstance(token_budget, (int, float))
            or isinstance(token_budget, bool)
            or not isfinite(token_budget)
            or token_budget < 0
        ):
            raise ValueError("token_budget must be finite and nonnegative.")

        # Own the snapshot before the first await. Counters receive separate
        # copies, so counter failures or mutations cannot alter the partition.
        snapshot = deepcopy(context)
        positions: list[tuple[int, int]] = []
        pins: set[tuple[int, int]] = set()
        unfinished: set[tuple[int, int]] = set()
        tool_groups: dict[tuple[str, str], set[tuple[int, int]]] = {}
        for msg_index, msg in enumerate(snapshot):
            msg_positions = [
                (msg_index, index) for index in range(len(msg.content))
            ] or [(msg_index, -1)]
            positions.extend(msg_positions)
            if msg.metadata.get(self.metadata_key) is True:
                pins.update(msg_positions)
            for block_index, block in enumerate(msg.content):
                position = (msg_index, block_index)
                if isinstance(block, (ToolCallBlock, ToolResultBlock)):
                    # Observed messages from distinct agents can reuse a call
                    # id. Pair only within the same message author's history.
                    key = (msg.name, block.id)
                    tool_groups.setdefault(key, set()).add(position)
                if (
                    isinstance(block, ToolCallBlock)
                    and block.id in unfinished_tool_call_ids
                ):
                    unfinished.add(position)

        groups = {
            position: group
            for group in tool_groups.values()
            for position in group
        }

        def close_pairs(
            selected: set[tuple[int, int]],
        ) -> set[tuple[int, int]]:
            """Include every existing counterpart of a selected tool block."""
            closed = set(selected)
            for position in selected:
                closed.update(groups.get(position, ()))
            return closed

        token_counts: dict[frozenset[tuple[int, int]], int] = {}

        async def measure(selected: set[tuple[int, int]]) -> int:
            """Measure a partition against the same fixed input prefix."""
            key = frozenset(selected)
            if key not in token_counts:
                tokens = await count_tokens(_select_fragments(snapshot, key))
                if (
                    not isinstance(tokens, int)
                    or isinstance(tokens, bool)
                    or tokens < 0
                ):
                    raise ValueError(
                        "count_tokens must return a nonnegative integer.",
                    )
                token_counts[key] = tokens
            return token_counts[key]

        prefix_tokens = await measure(set())
        pins = close_pairs(pins)
        if pins:
            pinned_tokens = await measure(pins)
            incremental_tokens = max(0, pinned_tokens - prefix_tokens)
            if incremental_tokens > self.max_pinned_tokens:
                raise ValueError(
                    f"Pinned context requires {incremental_tokens} tokens, "
                    f"exceeding max_pinned_tokens={self.max_pinned_tokens}.",
                )
            if pinned_tokens > token_budget:
                raise ValueError(
                    f"Pinned context and the fixed prefix require "
                    f"{pinned_tokens} tokens, exceeding "
                    f"token_budget={token_budget}.",
                )

        reserved = pins | close_pairs(unfinished)
        reserved_tokens = await measure(reserved)
        if reserved_tokens <= token_budget:
            for position in reversed(positions):
                if position in reserved:
                    continue
                candidate = reserved | close_pairs({position})
                candidate_tokens = await measure(candidate)
                if candidate_tokens > token_budget:
                    break
                reserved = candidate
                reserved_tokens = candidate_tokens

        return ContextRetentionResult(
            msgs_to_compress=_select_fragments(
                snapshot,
                set(positions) - reserved,
            ),
            msgs_to_reserve=_select_fragments(snapshot, reserved),
            reserved_tokens=reserved_tokens,
            budget_exceeded=reserved_tokens > token_budget,
        )
