# -*- coding: utf-8 -*-
"""Side-effect-free demo tool for the permission audit example.

``PermissionAuditDemoTool`` emits either an ordinary ASK or a
bypass-immune safety ASK based on a ``risk`` parameter, so the audit
example can demonstrate every permission scenario without running
destructive commands.
"""
from typing import Any

from agentscope.message import TextBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase
from agentscope.tool import ToolChunk


class PermissionAuditDemoTool(ToolBase):
    """Deterministic demo tool emitting ordinary or safety ASKs."""

    name: str = "PermissionAuditDemoTool"
    description: str = (
        "Demo tool for the permission audit example. Set risk='ordinary' "
        "for a normal confirmation prompt or risk='safety' for a "
        "bypass-immune safety check. Returns a short acknowledgement "
        "without side effects."
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "risk": {
                "type": "string",
                "enum": ["ordinary", "safety"],
                "description": "Ordinary confirmation or safety check.",
            },
            "label": {
                "type": "string",
                "description": "Non-sensitive label for the demonstration.",
            },
        },
        "required": ["risk", "label"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Emit an ordinary ASK or a bypass-immune safety ASK."""
        risk = tool_input.get("risk")
        if risk == "safety":
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message="Safety check: bypass-immune confirmation required.",
                decision_reason="Demo safety ASK (bypass-immune).",
                bypass_immune=True,
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Confirmation required for the demo operation.",
            decision_reason="Demo ordinary ASK.",
        )

    async def __call__(
        self,
        risk: str,
        label: str,
    ) -> ToolChunk:
        """Return a short acknowledgement without side effects."""
        return ToolChunk(
            content=[
                TextBlock(text=f"demo executed: risk={risk}, label={label}"),
            ],
        )
