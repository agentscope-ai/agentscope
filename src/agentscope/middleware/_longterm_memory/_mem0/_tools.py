# -*- coding: utf-8 -*-
"""Agent-control tools exposed by the mem0 middleware.

These ``search_memory`` / ``add_memory`` tools are registered into the
agent's toolkit by :class:`Mem0Middleware` when ``mode`` is
``"agent_control"`` or ``"both"``. They close over the middleware
instance and the target agent so per-call ``user_id`` / ``agent_id``
resolvers keep working after registration.

Shape and behavior mirror AgentScope 1.x's
``Mem0LongTermMemory.retrieve_from_memory`` / ``record_to_memory``
(multi-keyword parallel search, 3-tier add fallback, verbose result
text) — adapted to AgentScope 2.x's tool conventions
(``FunctionTool`` wrapping plain async functions; failures return a
``ToolChunk`` with ``state=ERROR`` so the toolkit aggregates it
properly).
"""
from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from ....message import TextBlock, ToolResultState
from ....permission import PermissionBehavior, PermissionDecision
from ....tool import FunctionTool, ToolBase, ToolChunk

if TYPE_CHECKING:
    from ....agent import Agent

    from ._middleware import Mem0Middleware


class _AllowedFunctionTool(FunctionTool):
    """FunctionTool that auto-allows itself (no permission prompt).

    Middleware-provided memory tools are part of the agent's standard
    capabilities — prompting on every call would defeat the point.
    """

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="auto-allowed: mem0 long-term memory tool",
        )


def build_memory_tools(
    mw: "Mem0Middleware",
    agent: "Agent",
) -> list[ToolBase]:
    """Return the ``search_memory`` / ``add_memory`` tools bound to
    ``mw`` and ``agent``."""

    async def search_memory(
        keywords: list[str],
        limit: int = 5,
    ) -> str | ToolChunk:
        """Retrieve the memory based on the given keywords.

        Args:
            keywords (list[str]):
                Short, targeted search phrases (for example, a person's
                name, a specific date, a location, or a phrase
                describing something you want to retrieve from the
                memory). Each keyword is issued as an independent query
                against the memory store; results are merged and
                deduplicated.
            limit (int):
                The maximum number of memories to retrieve per keyword.
                Defaults to 5.
        """
        if not keywords:
            return "(no keywords supplied — nothing to search)"

        user_id = mw._resolve_user_id(agent)
        agent_id = (
            mw._resolve_agent_id(agent) if mw._scope_search_by_agent else None
        )

        # Match v1: each keyword is an independent search, run them in
        # parallel and merge.
        original_top_k = mw._top_k
        mw._top_k = limit
        try:
            try:
                per_keyword = await asyncio.gather(
                    *[
                        mw._async_search(
                            kw,
                            user_id=user_id,
                            agent_id=agent_id,
                        )
                        for kw in keywords
                    ],
                )
            except Exception as e:  # noqa: BLE001
                return _error_chunk(f"Error retrieving memory: {e}")
        finally:
            mw._top_k = original_top_k

        seen: set[str] = set()
        merged: list[str] = []
        for results in per_keyword:
            for r in results:
                if r not in seen:
                    seen.add(r)
                    merged.append(r)

        if not merged:
            return "(no relevant memories found)"
        return "\n".join(f"- {m}" for m in merged)

    async def add_memory(
        thinking: str,
        content: list[str],
    ) -> str | ToolChunk:
        """Use this function to record important information that you
        may need later. The target content should be specific and
        concise, e.g. who, when, where, do what, why, how, etc.

        Do NOT pass back content that appears earlier in the
        conversation history as a previous ``search_memory`` tool
        result — those facts are already in the store, re-adding them
        wastes an extraction call.

        Args:
            thinking (str):
                Your reasoning about why this is worth remembering.
                Stays in the agent transcript but is NOT persisted to
                the memory store — only ``content`` is. Use it to
                force yourself to think before writing.
            content (list[str]):
                The content to remember, as a list of strings (one
                item per fact). Each item should be a complete,
                standalone sentence — only this is sent to mem0 for
                extraction.
        """
        if not content:
            return _error_chunk("`content` is empty — nothing to record.")

        user_id = mw._resolve_user_id(agent)
        agent_id = mw._resolve_agent_id(agent)

        # Only the user-facing content goes into mem0. ``thinking`` is
        # the agent's internal rationale — meta about the agent's
        # decision, not a fact about the user — so feeding it to
        # mem0's extraction LLM would muddy the stored memories with
        # agent self-narration. We keep it in the tool response so the
        # decision is auditable in the transcript.
        text = "\n".join(content)

        try:
            result = await mw._async_add_with_fallback(
                text,
                user_id=user_id,
                agent_id=agent_id,
            )
        except Exception as e:  # noqa: BLE001
            return _error_chunk(f"Error recording memory: {e}")

        rationale = f" (rationale: {thinking})" if thinking else ""
        return f"Successfully recorded to memory{rationale} → {result}"

    return [
        _AllowedFunctionTool(
            search_memory,
            is_read_only=True,
            is_concurrency_safe=True,
            is_state_injected=False,
        ),
        _AllowedFunctionTool(
            add_memory,
            is_read_only=False,
            is_concurrency_safe=False,
            is_state_injected=False,
        ),
    ]


def _error_chunk(message: str) -> ToolChunk:
    """Wrap an error message as a ``ToolChunk(state=ERROR)`` so the
    toolkit aggregates it as a failed tool call — the agent sees the
    message and can decide whether to retry or move on."""
    return ToolChunk(
        content=[TextBlock(type="text", text=message)],
        state=ToolResultState.ERROR,
    )
