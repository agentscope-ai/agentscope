# -*- coding: utf-8 -*-
"""Side-effect-free tool for the permission audit example."""
from typing import Any

from agentscope.message import TextBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk


class PermissionDemoTool(ToolBase):
    """Return deterministic ALLOW, ASK, or DENY permission decisions."""

    name: str = "PermissionDemoTool"
    description: str = (
        "Side-effect-free permission audit demo. Set decision to allow, ask, "
        "or deny; label is a non-sensitive description for the result."
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["allow", "ask", "deny"],
                "description": "Permission behavior to demonstrate.",
            },
            "label": {
                "type": "string",
                "description": "Non-sensitive label for the demonstration.",
            },
        },
        "required": ["decision", "label"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Return the permission behavior selected by the demo input."""
        del context
        behavior = PermissionBehavior(tool_input["decision"])
        return PermissionDecision(
            behavior=behavior,
            message=f"Demo tool returned {behavior.value}.",
            decision_reason="Deterministic demo-tool decision.",
        )

    async def call(self, decision: str, label: str) -> ToolChunk:
        """Return a short acknowledgement without side effects."""
        return ToolChunk(
            content=[
                TextBlock(
                    text=f"demo executed: decision={decision}, label={label}",
                ),
            ],
        )
