# -*- coding: utf-8 -*-
"""Agent-callable tools for filesystem-backed long-term memory.

The tools are custom :class:`ToolBase` implementations rather than ordinary
decorated functions because they need the live :class:`AgentState` injected by
the toolkit. The middleware maps that state to a workspace-scoped store at
call time, allowing one tool set and middleware instance to serve concurrent
agents without retaining an Agent object.

Tool failures are returned as ``ToolChunk(state=ERROR)`` so an invalid memory
edit becomes model-visible feedback instead of terminating the reply loop.
"""
from __future__ import annotations

from typing import Any, Literal, TYPE_CHECKING, TypeAlias

from ....message import TextBlock, ToolResultState
from ....permission import PermissionBehavior, PermissionDecision
from ....state import AgentState
from ....tool import ToolBase, ToolChunk

if TYPE_CHECKING:
    from ._middleware import FileSystemMemoryMiddleware


MemoryTarget: TypeAlias = Literal["user", "memory", "daily"]
MemoryScope: TypeAlias = Literal["daily", "all"]
MemoryAction: TypeAlias = Literal["add", "replace", "remove"]


class _FileSystemMemoryToolBase(ToolBase):
    """Base class for state-injected, auto-allowed memory tools.

    Memory operations are ordinary internal agent capabilities. Prompting for
    external-execution permission on every read or write would make autonomous
    memory impractical, so every FileSystemMemory tool allows itself
    explicitly.
    """

    is_external_tool: bool = False
    is_state_injected: bool = True
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, middleware: "FileSystemMemoryMiddleware") -> None:
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


class _MemoryReadTool(_FileSystemMemoryToolBase):
    """Read one complete memory document and expose its section names."""

    name: str = "memory_read"
    description: str = (
        "Read the workspace's persistent user profile, long-term memory, "
        "or one dated daily-memory file."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["user", "memory", "daily"],
            },
            "date": {
                "type": "string",
                "format": "date",
                "description": (
                    "Daily-memory date in YYYY-MM-DD format. Omit to read "
                    "today; ignored for user and memory targets."
                ),
            },
        },
        "required": ["target"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    async def __call__(
        self,
        target: MemoryTarget,
        date: str | None = None,
        *,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """Read ``target`` for the active agent state.

        The section preamble is intentionally part of the result: the agent is
        expected to inspect it before choosing a section for ``memory_manage``.

        Args:
            target (MemoryTarget):
                ``"user"``, ``"memory"``, or ``"daily"``.
            date (str | None):
                ISO date for a daily target; ignored by other targets.
            _agent_state (AgentState):
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


class _MemorySearchTool(_FileSystemMemoryToolBase):
    """Search Markdown memory sections with lightweight lexical matching."""

    name: str = "memory_search"
    description: str = (
        "Search Markdown memory sections with lightweight lexical matching. "
        "The default searches recent daily notebooks; use scope=all to also "
        "include MEMORY.md and USER.md."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Phrase or keywords to find in memory.",
            },
            "scope": {
                "type": "string",
                "enum": ["daily", "all"],
                "default": "daily",
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "default": 30,
                "description": "Inclusive daily-memory lookback window.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "default": 5,
                "description": "Maximum number of sections to return.",
            },
        },
        "required": ["query"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    async def __call__(
        self,
        query: str,
        scope: MemoryScope = "daily",
        days: int = 30,
        limit: int = 5,
        *,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """Return the highest-ranked matching Markdown sections.

        The search is intentionally lightweight and filesystem friendly:

        - Daily-memory candidates are discovered by filename and filtered by
          the inclusive ``days`` window before file reads.
        - Markdown files are split into ``##`` sections, and each returned
          result is one complete section chunk.
        - Query and section text are normalized with case folding and
          whitespace compaction.
        - Exact normalized phrase matches rank ahead of token-only matches.
        - English-like text uses word tokens; CJK runs are expanded into
          character bigrams, avoiding an external tokenizer dependency.
        - No embedding model, semantic scorer, recency weighting, vector
          database, or reranker is involved.

        Example tool calls:

        - ``memory_search(query="app-service demo todo")`` searches recent
          daily notebooks only.
        - ``memory_search(query="Hangzhou concise Chinese", scope="all")``
          also searches ``USER.md`` and ``MEMORY.md``.

        A result with ``Relevance: 1.0`` usually means the full query phrase
        appeared in the section. Lower scores are simple matched-term ratios,
        useful for rough recall but not semantic similarity.

        Args:
            query (str):
                Phrase or keywords to find.
            scope (MemoryScope):
                ``"daily"`` for dated files only or ``"all"`` to include
                USER and MEMORY.
            days (int):
                Inclusive daily-memory lookback window.
            limit (int):
                Maximum number of matching sections returned.
            _agent_state (AgentState):
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


class _MemoryManageTool(_FileSystemMemoryToolBase):
    """Apply one constrained add, replace, or remove operation."""

    name: str = "memory_manage"
    description: str = (
        "Update persistent workspace memory. Always call memory_read for the "
        "same target first, then use its current content and sections. Add "
        "to an existing section when possible; set create_section=true only "
        "when no current section is suitable."
    )
    input_schema: dict[str, Any] = {
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
                "description": (
                    "New text required for add and replace. Add writes it as "
                    "a bullet; replace substitutes the exact old_text."
                ),
            },
            "section": {
                "type": "string",
                "description": (
                    "Exact existing level-two Markdown heading for add. "
                    "Valid for user, memory, and today's daily notebook. If "
                    "none fits, provide a new plain heading together with "
                    "create_section=true."
                ),
            },
            "create_section": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Create section as a new ## heading when it does not "
                    "exist. Only valid for action=add, and only after "
                    "memory_read confirms no existing section is suitable."
                ),
            },
            "old_text": {
                "type": "string",
                "description": (
                    "Current text required for replace/remove. It must match "
                    "exactly once in the target document."
                ),
            },
            "thinking": {
                "type": "string",
                "description": "Why this is durable; not persisted.",
            },
        },
        "required": ["action", "target", "thinking"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = False

    async def __call__(
        self,
        action: MemoryAction,
        target: MemoryTarget,
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
            action (MemoryAction):
                ``"add"``, ``"replace"``, or ``"remove"``.
            target (MemoryTarget):
                ``"user"``, ``"memory"``, or ``"daily"``.
            thinking (str):
                Rationale retained only in the tool result.
            content (str | None):
                New text required by add/replace.
            old_text (str | None):
                Exact unique text required by replace/remove.
            section (str | None):
                Existing level-two heading used by add.
            create_section (bool):
                Whether add may create a missing heading in the target.
            _agent_state (AgentState):
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
                if target in ("user", "memory"):
                    self._middleware._invalidate_snapshot(_agent_state)
            rationale = f" Rationale: {thinking}" if thinking else ""
            return _text_chunk(f"Memory updated at {path}.{rationale}")
        except Exception as error:  # noqa: BLE001
            return _error_chunk(f"Error updating memory: {error}")


def build_memory_tools(
    middleware: "FileSystemMemoryMiddleware",
    *,
    writable: bool,
) -> list[ToolBase]:
    """Build state-bound memory tools for one middleware instance.

    Read and search are available in every mode. ``memory_manage`` is included
    only when the caller enables autonomous writes (``agent_control`` or
    ``both``).
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


