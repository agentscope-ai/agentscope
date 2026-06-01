# -*- coding: utf-8 -*-
"""The memory tool for explicit agent memory management."""
import time
from typing import Any

from .._base import ToolBase
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from ...message import TextBlock
from ...state import AgentState


class MemoryTool(ToolBase):
    """A tool that allows the agent to explicitly store, retrieve, and
    manage its own memories.

    Unlike automatic context compression (which summarizes old conversation
    turns), this tool gives the agent fine-grained control over what
    specific information to remember across context windows. Memories
    persist within the session and survive context compression.

    This is inspired by Claude Code's memory system, which allows agents
    to save facts, user preferences, decisions, and discoveries for later
    retrieval.
    """

    name: str = "memory"
    description: str = (
        "Manage your persistent memory. Use this tool to store, retrieve, "
        "update, or delete information that should be remembered across "
        "context windows.\n\n"
        "**Actions:**\n"
        "- 'save': Store or update a memory entry by key. If the key "
        "already exists, its value will be overwritten.\n"
        "- 'retrieve': Get a specific memory by key, or list all memories "
        "if no key is provided.\n"
        "- 'delete': Remove a memory entry by key.\n\n"
        "**Best practices:**\n"
        "- Save user preferences, important decisions, and discoveries.\n"
        "- Use descriptive, kebab-case keys (e.g., 'user-preferred-language',"
        " 'project-database-schema').\n"
        "- Retrieve relevant memories at the start of a new task.\n"
        "- Delete outdated or corrected memories."
    )
    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = True

    @property
    def input_schema(self) -> dict[str, Any]:  # type: ignore[override]
        """The input schema for the memory tool."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["save", "retrieve", "delete"],
                    "description": (
                        "The action to perform on the memory store."
                    ),
                },
                "key": {
                    "type": "string",
                    "description": (
                        "The key of the memory entry. Required for "
                        "'save' and 'delete'. Optional for 'retrieve' "
                        "(if omitted, all memories are listed)."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": (
                        "The content to store. Required for 'save'."
                    ),
                },
            },
            "required": ["action"],
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """The memory tool is always allowed."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="The memory tool is always allowed.",
        )

    async def __call__(
        self,
        _agent_state: AgentState,
        action: str,
        key: str | None = None,
        value: str | None = None,
    ) -> ToolChunk:
        """Execute the memory operation.

        Args:
            _agent_state (`AgentState`):
                The agent state injected by the framework.
            action (`str`):
                The action to perform: 'save', 'retrieve', or 'delete'.
            key (`str | None`):
                The memory key. Required for 'save' and 'delete'.
            value (`str | None`):
                The memory value. Required for 'save'.

        Returns:
            `ToolChunk`:
                The result of the memory operation.
        """
        mem = _agent_state.memory_context

        if action == "save":
            if not key:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text="Error: 'key' is required for 'save' action.",
                        ),
                    ],
                )
            if value is None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text="Error: 'value' is required for "
                            "'save' action.",
                        ),
                    ],
                )
            existed = key in mem.entries
            mem.set(key, value, time.time())
            verb = "Updated" if existed else "Saved"
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"{verb} memory '{key}'.",
                    ),
                ],
            )

        if action == "retrieve":
            if key:
                entry = mem.entries.get(key)
                if entry is None:
                    return ToolChunk(
                        content=[
                            TextBlock(
                                text=f"No memory found for key '{key}'.",
                            ),
                        ],
                    )
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"Memory '{key}':\n{entry.value}",
                        ),
                    ],
                )

            if not mem.entries:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text="No memories stored yet.",
                        ),
                    ],
                )

            lines = [
                f"- {e.key}: {e.value[:100]}{'...' if len(e.value) > 100 else ''}"
                for e in sorted(
                    mem.entries.values(),
                    key=lambda e: e.created_at,
                    reverse=True,
                )
            ]
            return ToolChunk(
                content=[
                    TextBlock(
                        text="Stored memories:\n" + "\n".join(lines),
                    ),
                ],
            )

        if action == "delete":
            if not key:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text="Error: 'key' is required for "
                            "'delete' action.",
                        ),
                    ],
                )
            deleted = mem.delete(key)
            if deleted:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"Deleted memory '{key}'.",
                        ),
                    ],
                )
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"No memory found for key '{key}'.",
                    ),
                ],
            )

        return ToolChunk(
            content=[
                TextBlock(
                    text=f"Unknown action '{action}'. "
                    f"Valid actions: save, retrieve, delete.",
                ),
            ],
        )
