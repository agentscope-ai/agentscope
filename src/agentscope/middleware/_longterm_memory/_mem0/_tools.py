# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Agent-control tools exposed by the mem0 middleware.

These ``search_memory`` / ``add_memory`` tools are listed by
:class:`Mem0Middleware` when ``mode`` is ``"agent_control"`` or
``"both"``. Callers pass them into the agent's toolkit explicitly.
Each tool reads ``user_id`` / ``agent_id`` directly from the
middleware instance — both are plain strings set at construction
time, so no Agent instance is stored or referenced at call time.

Shape and behavior mirror AgentScope 1.x's
``Mem0LongTermMemory.retrieve_from_memory`` / ``record_to_memory``
(multi-keyword parallel search, fallback write, verbose result
text) — adapted to AgentScope 2.x's tool conventions
(custom ``ToolBase`` implementations; failures return a ``ToolChunk``
with ``state=ERROR`` so the toolkit aggregates it properly).
"""
from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from ....message import TextBlock, ToolResultState
from ....permission import PermissionBehavior, PermissionDecision
from ....tool import ToolBase, ToolChunk

if TYPE_CHECKING:
    from ._middleware import Mem0Middleware


class _Mem0MemoryToolBase(ToolBase):
    """Base class for mem0 tools that auto-allow themselves.

    Middleware-provided memory tools are part of the agent's standard
    capabilities — prompting on every call would defeat the point.
    """

    is_external_tool: bool = False
    is_state_injected: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        mw: "Mem0Middleware",
    ) -> None:
        self._mw = mw

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="auto-allowed: mem0 long-term memory tool",
        )


class _SearchMemoryTool(_Mem0MemoryToolBase):
    """Agent-callable mem0 search tool."""

    name: str = "search_memory"
    description: str = (
        "Retrieve memories based on short, targeted search keywords. "
        "Each keyword is issued as an independent query; results are merged "
        "and deduplicated."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Short, targeted search phrases such as a person's name, "
                    "a specific date, a location, or a phrase describing what "
                    "to retrieve from memory."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Maximum number of memories to retrieve per keyword."
                ),
                "default": 5,
            },
        },
        "required": ["keywords"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    async def __call__(
        self,
        keywords: list[str],
        limit: int = 5,
    ) -> ToolChunk:
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
            return _text_chunk("(no keywords supplied — nothing to search)")

        user_id = self._mw._user_id
        agent_id = self._mw._agent_id
        search_agent_id = agent_id if self._mw._scope_search_by_agent else None

        # Match v1: each keyword is an independent search, run them in
        # parallel and merge.
        try:
            per_keyword = await asyncio.gather(
                *[
                    self._mw._async_search(
                        kw,
                        user_id=user_id,
                        agent_id=search_agent_id,
                        top_k=limit,
                    )
                    for kw in keywords
                ],
            )
        except Exception as e:  # noqa: BLE001
            return _error_chunk(f"Error retrieving memory: {e}")

        seen: set[str] = set()
        merged: list[str] = []
        for results in per_keyword:
            for r in results:
                if r not in seen:
                    seen.add(r)
                    merged.append(r)

        if not merged:
            return _text_chunk("(no relevant memories found)")
        return _text_chunk("\n".join(f"- {m}" for m in merged))


class _AddMemoryTool(_Mem0MemoryToolBase):
    """Agent-callable mem0 write tool."""

    name: str = "add_memory"
    description: str = (
        "Record important, durable information that may be useful later. "
        "Only the provided content is persisted; thinking is retained in the "
        "tool result for auditability."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "thinking": {
                "type": "string",
                "description": (
                    "Reasoning about why this information is worth "
                    "remembering. This is not persisted to mem0."
                ),
            },
            "content": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific facts to remember. Each item should be a "
                    "complete, standalone sentence."
                ),
            },
        },
        "required": ["thinking", "content"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = False

    async def __call__(
        self,
        thinking: str,
        content: list[str],
    ) -> ToolChunk:
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

        user_id = self._mw._user_id
        agent_id = self._mw._agent_id

        # Only the user-facing content goes into mem0. ``thinking`` is
        # the agent's internal rationale — meta about the agent's
        # decision, not a fact about the user — so feeding it to
        # mem0's extraction LLM would muddy the stored memories with
        # agent self-narration. We keep it in the tool response so the
        # decision is auditable in the transcript.
        text = "\n".join(content)

        try:
            result = await self._mw._async_add_with_fallback(
                text,
                user_id=user_id,
                agent_id=agent_id,
            )
        except Exception as e:  # noqa: BLE001
            return _error_chunk(f"Error recording memory: {e}")

        rationale = f" (rationale: {thinking})" if thinking else ""
        return _text_chunk(
            f"Successfully recorded to memory{rationale} → {result}",
        )


def _build_memory_tools(
    mw: "Mem0Middleware",
) -> list[ToolBase]:
    """Return the ``search_memory`` / ``add_memory`` tools bound to
    ``mw``.

    The tool classes intentionally reach into ``mw``'s private state
    (``_resolve_user_id`` / ``_async_search`` / ...) —
    we live in the same package and ``mw`` is the natural place for
    that state.
    """
    return [
        _SearchMemoryTool(mw),
        _AddMemoryTool(mw),
    ]


def _text_chunk(message: str) -> ToolChunk:
    """Wrap a text message as a normal tool chunk."""
    return ToolChunk(
        content=[TextBlock(type="text", text=message)],
    )


def _error_chunk(message: str) -> ToolChunk:
    """Wrap an error message as a ``ToolChunk(state=ERROR)`` so the
    toolkit aggregates it as a failed tool call — the agent sees the
    message and can decide whether to retry or move on."""
    return ToolChunk(
        content=[TextBlock(type="text", text=message)],
        state=ToolResultState.ERROR,
    )
