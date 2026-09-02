# -*- coding: utf-8 -*-
"""Builtin tool for requesting a structured choice from the user."""
from typing import Any

from .._base import ToolBase
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)


class RequestUserInput(ToolBase):
    """Pause the agent until the user selects an option or enters text."""

    name: str = "RequestUserInput"
    description: str = (
        "Ask the user one question when their choice is required before "
        "continuing. Provide 2 to 4 mutually exclusive options with concise "
        "labels and optional descriptions. Mark at most one option as "
        "recommended. Do not add an Other option because the client always "
        "adds it and lets the user enter custom text."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "The question the user must answer.",
            },
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "description": (
                    "Mutually exclusive choices. Do not include Other."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 300,
                        },
                        "recommended": {
                            "type": "boolean",
                            "default": False,
                        },
                    },
                    "required": ["label"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["question", "options"],
        "additionalProperties": False,
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_external_tool: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow the side-effect-free request without a second prompt."""
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Structured user input is allowed.",
            decision_reason="RequestUserInput only collects user input.",
        )
