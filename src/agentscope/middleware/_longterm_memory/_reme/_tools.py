# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Agent-control tool exposed by the ReMe middleware.

The single ``memory_search`` tool is listed by :class:`ReMeMiddleware`
when ``mode`` is ``"agent_control"`` or ``"both"``. The tool drives
ReMe's ``search`` action through the embedded ReMe app held on the
middleware.

Unlike the mem0 middleware (which also exposes an ``add_memory`` tool),
ReMe has **no manual add tool**: it records memory through its
``auto_memory`` job — an LLM extraction over the *conversation* — which
the middleware runs automatically on the reply path (write-back). So the
agent-facing surface is search-only.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ....message import TextBlock, ToolResultState
from ....permission import PermissionBehavior, PermissionDecision
from ....tool import ToolBase, ToolChunk

if TYPE_CHECKING:
    from ._middleware import ReMeMiddleware


class _ReMeMemoryToolBase(ToolBase):
    """Base class for ReMe tools that auto-allow themselves.

    Middleware-provided memory tools are part of the agent's standard
    capabilities — prompting on every call would defeat the point.
    """

    is_external_tool: bool = False
    is_state_injected: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        mw: "ReMeMiddleware",
    ) -> None:
        """Bind the tool to its owning middleware.

        Args:
            mw (`ReMeMiddleware`):
                The middleware whose embedded ReMe app and parameters this
                tool drives.
        """
        self._mw = mw

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Auto-allow the call without prompting.

        Middleware-provided memory tools are part of the agent's standard
        capabilities, so they never require user confirmation.

        Returns:
            `PermissionDecision`:
                An ``ALLOW`` decision.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="auto-allowed: ReMe long-term memory tool",
        )


class _MemorySearchTool(_ReMeMemoryToolBase):
    """Agent-callable ReMe search tool.

    The advertised ``limit`` default is the middleware's ``top_k`` (set
    per instance in :meth:`__init__`), so configuring ``top_k`` on the
    middleware also governs how many memories this tool retrieves when the
    agent omits ``limit``.
    """

    name: str = "memory_search"
    description: str = (
        "Retrieve memories from past conversations relevant to a query."
    )
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    def __init__(
        self,
        mw: "ReMeMiddleware",
    ) -> None:
        """Bind the tool to ``mw`` and build its per-instance schema.

        The advertised ``limit`` default is read from the middleware's
        ``top_k`` here (rather than a hardcoded constant), so configuring
        ``top_k`` on the middleware also governs the tool's default.

        Args:
            mw (`ReMeMiddleware`):
                The middleware whose embedded ReMe app and ``top_k`` this
                search tool uses.
        """
        super().__init__(mw)
        # Per-instance schema so the default ``limit`` reflects this
        # middleware's ``top_k`` rather than a hardcoded constant.
        self.input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to retrieve from memory — for example a "
                        "person's name, a preference, or a past decision."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memories to retrieve.",
                    "default": mw._parameters.top_k,
                },
            },
            "required": ["query"],
        }

    async def __call__(
        self,
        query: str,
        limit: int | None = None,
    ) -> ToolChunk:
        """Retrieve memory relevant to ``query``.

        Args:
            query (str):
                What to retrieve from the ReMe workspace (for example a
                person's name, a preference, or a past decision).
            limit (int | None):
                The maximum number of memories to retrieve. When omitted,
                falls back to the middleware's ``top_k``.
        """
        if not query:
            return _text_chunk("(no query supplied — nothing to search)")

        try:
            memories = await self._mw._search(query, limit=limit)
        except Exception as e:  # noqa: BLE001
            return _error_chunk(f"Error retrieving memory: {e}")

        if not memories:
            return _text_chunk("(no relevant memories found)")
        return _text_chunk("\n".join(f"- {m}" for m in memories))


def _build_memory_tools(
    mw: "ReMeMiddleware",
) -> list[ToolBase]:
    """Return the ``memory_search`` tool bound to ``mw``.

    ReMe's agent-facing surface is search-only — writing is handled
    automatically by the middleware's ``auto_memory`` write-back, not a
    manual tool.
    """
    return [
        _MemorySearchTool(mw),
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
