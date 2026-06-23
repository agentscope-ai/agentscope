# -*- coding: utf-8 -*-
"""Agent-callable tools for file-backed long-term memory.

The tools are custom :class:`ToolBase` implementations rather than ordinary
decorated functions because they need the live :class:`AgentState` injected by
the toolkit. The middleware maps that state to a workspace-scoped store at
call time, allowing one tool set and middleware instance to serve concurrent
agents without retaining an Agent object.

Tool failures are returned as ``ToolChunk(state=ERROR)`` so an invalid memory
edit becomes model-visible feedback instead of terminating the reply loop.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ....message import TextBlock, ToolResultState
from ....permission import PermissionBehavior, PermissionDecision
from ....state import AgentState
from ....tool import ToolBase, ToolChunk

if TYPE_CHECKING:
    from ._middleware import FileLongTermMemoryMiddleware


class _FileLTMToolBase(ToolBase):
    """Base class for state-injected, auto-allowed LTM tools.

    Memory operations are ordinary internal agent capabilities. Prompting for
    external-execution permission on every read or write would make autonomous
    memory impractical, so all three tools allow themselves explicitly.
    """

    is_external_tool = False
    is_state_injected = True
    is_mcp = False
    mcp_name = None

    def __init__(self, middleware: "FileLongTermMemoryMiddleware") -> None:
        """Bind a tool instance to the middleware's store registry."""
        self._middleware = middleware

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Allow an internal workspace-memory operation."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="auto-allowed: workspace long-term memory tool",
        )


class _MemoryReadTool(_FileLTMToolBase):
    """Read one complete memory document and expose its section names."""

    name = "memory_read"
    description = (
        "Read the workspace's persistent user profile, long-term memory, "
        "or one dated daily-memory file. "
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["user", "memory", "daily"],
            },
            "date": {
                "type": "string",
                "description": "YYYY-MM-DD; only used for target=daily.",
            },
        },
        "required": ["target"],
    }
    is_concurrency_safe = True
    is_read_only = True

    async def __call__(
        self,
        target: str,
        date: str | None = None,
        *,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """Read ``target`` for the active agent state.

        The section preamble is intentionally part of the result: the agent is
        expected to inspect it before choosing a section for ``memory_manage``.

        Args:
            target:
                ``"user"``, ``"memory"``, or ``"daily"``.
            date:
                ISO date for a daily target; ignored by other targets.
            _agent_state:
                Live state injected by the AgentScope toolkit.
        """
        try:
            store = self._middleware._store_for_state(_agent_state)
            content, sections = await store.read_target_with_sections(
                target,
                daily_date=date,
            )
            section_text = ", ".join(sections) or "(none)"
            body = content or "(memory file is empty or missing)"
            return _text_chunk(
                f"Available ## sections: {section_text}\n\n{body}",
            )
        except Exception as error:  # noqa: BLE001
            return _error_chunk(f"Error reading memory: {error}")


class _MemorySearchTool(_FileLTMToolBase):
    """Search Markdown memory sections with lightweight lexical matching."""

    name = "memory_search"
    description = (
        "Search recent daily memories using lightweight lexical matching. "
        "Use scope=all to include MEMORY.md and USER.md."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "scope": {
                "type": "string",
                "enum": ["daily", "all"],
                "default": "daily",
            },
            "days": {"type": "integer", "minimum": 1, "default": 30},
            "limit": {"type": "integer", "minimum": 1, "default": 5},
        },
        "required": ["query"],
    }
    is_concurrency_safe = True
    is_read_only = True

    async def __call__(
        self,
        query: str,
        scope: str = "daily",
        days: int = 30,
        limit: int = 5,
        *,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """Return the highest-ranked matching Markdown sections.

        Args:
            query:
                Phrase or keywords to find.
            scope:
                ``"daily"`` for dated files only or ``"all"`` to include
                USER and MEMORY.
            days:
                Inclusive daily-memory lookback window.
            limit:
                Maximum number of matching sections returned.
            _agent_state:
                Live state injected by the AgentScope toolkit.
        """
        try:
            store = self._middleware._store_for_state(_agent_state)
            results = await store.search(
                query,
                scope=scope,
                days=days,
                limit=limit,
            )
            if not results:
                return _text_chunk("(no relevant memories found)")
            rendered = []
            for result in results:
                rendered.append(
                    f"## Search from {result.source} | {result.section}\n"
                    f"Relevance: {result.relevance}\n"
                    f"Content: {result.content}",
                )
            return _text_chunk("\n\n".join(rendered))
        except Exception as error:  # noqa: BLE001
            return _error_chunk(f"Error searching memory: {error}")


class _MemoryManageTool(_FileLTMToolBase):
    """Apply one constrained add, replace, or remove operation."""

    name = "memory_manage"
    description = (
        "Update persistent workspace memory. Always call memory_read for the "
        "same target first, then use its current content and sections. Add "
        "to an existing section when possible; set create_section=true only "
        "when no current section is suitable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
            },
            "target": {
                "type": "string",
                "enum": ["user", "memory", "daily"],
            },
            "content": {
                "type": "string",
                "description": "New content for add or replace.",
            },
            "section": {
                "type": "string",
                "description": (
                    "Exact existing level-two Markdown heading for add. "
                    "Custom existing headings are also valid when no "
                    "existing sections are suitable. "
                ),
            },
            "create_section": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Create section as a new ## heading when it does not "
                    "exist. Only valid for action=add with target=user or "
                    "target=memory, and only after memory_read confirms no "
                    "existing section is suitable."
                ),
            },
            "old_text": {
                "type": "string",
                "description": "Exact unique text for replace or remove.",
            },
            "thinking": {
                "type": "string",
                "description": "Why this is durable; not persisted.",
            },
        },
        "required": ["action", "target", "thinking"],
    }
    is_concurrency_safe = False
    is_read_only = False

    async def __call__(
        self,
        action: str,
        target: str,
        thinking: str,
        content: str | None = None,
        old_text: str | None = None,
        section: str | None = None,
        create_section: bool = False,
        *,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """Update memory for the workspace bound to ``_agent_state``.

        Writes use the middleware's workspace lock. ``thinking`` is echoed in
        the result for auditability but is not forwarded to the store.

        Args:
            action:
                ``"add"``, ``"replace"``, or ``"remove"``.
            target:
                ``"user"``, ``"memory"``, or ``"daily"``.
            thinking:
                Rationale retained only in the tool result.
            content:
                New text required by add/replace.
            old_text:
                Exact unique text required by replace/remove.
            section:
                Existing level-two heading used by add.
            create_section:
                Whether add may create a missing USER/MEMORY heading.
            _agent_state:
                Live state injected by the AgentScope toolkit.
        """
        try:
            store = self._middleware._store_for_state(_agent_state)
            async with self._middleware._lock_for_state(_agent_state):
                path = await store.update_target(
                    action=action,
                    target=target,
                    content=content,
                    old_text=old_text,
                    section=section,
                    create_section=create_section,
                )
            rationale = f" Rationale: {thinking}" if thinking else ""
            return _text_chunk(f"Memory updated at {path}.{rationale}")
        except Exception as error:  # noqa: BLE001
            return _error_chunk(f"Error updating memory: {error}")


def build_memory_tools(
    middleware: "FileLongTermMemoryMiddleware",
    *,
    writable: bool,
) -> list[ToolBase]:
    """Build state-bound memory tools for one middleware instance.

    Read and search are available in every mode. ``memory_manage`` is included
    only when the caller enables autonomous writes (``auto`` or ``both``).
    """
    tools: list[ToolBase] = [
        _MemoryReadTool(middleware),
        _MemorySearchTool(middleware),
    ]
    if writable:
        tools.append(_MemoryManageTool(middleware))
    return tools


def _text_chunk(message: str) -> ToolChunk:
    """Wrap successful tool output in a text-only chunk."""
    return ToolChunk(content=[TextBlock(text=message)])


def _error_chunk(message: str) -> ToolChunk:
    """Wrap a recoverable tool failure in an error-state chunk."""
    return ToolChunk(
        content=[TextBlock(text=message)],
        state=ToolResultState.ERROR,
    )
