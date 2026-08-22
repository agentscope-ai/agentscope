# -*- coding: utf-8 -*-
"""Client-provided external tool definitions and runtime adapters."""
import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ..tool import ToolBase


_MAX_INPUT_SCHEMA_BYTES = 64 * 1024
CLIENT_EXTERNAL_TOOL_NAME_PREFIX = "client__"
_REFERENCE_KEYWORDS = {"$ref", "$dynamicRef", "$recursiveRef"}


def _validate_local_references(value: object) -> None:
    """Reject references that can make JSON Schema retrieve a URI."""
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                key in _REFERENCE_KEYWORDS
                and isinstance(child, str)
                and not child.startswith("#")
            ):
                raise ValueError(
                    f"Client tool schemas only support local {key} values.",
                )
            _validate_local_references(child)
    elif isinstance(value, list):
        for child in value:
            _validate_local_references(child)


class ClientExternalToolDefinition(BaseModel):
    """A tool that the active client can execute for one chat run."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^client__[a-zA-Z0-9_-]+$",
        description=(
            "The namespaced tool name exposed to the model. It must start "
            f"with '{CLIENT_EXTERNAL_TOOL_NAME_PREFIX}'."
        ),
    )
    description: str = Field(
        min_length=1,
        max_length=2000,
        description="The model-facing tool description.",
    )
    read_only: bool = Field(
        default=False,
        description=(
            "Whether executing the tool can only read client-side state."
        ),
    )
    input_schema: dict[str, Any] = Field(
        description="The tool input schema in JSON Schema format.",
    )

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate schema syntax and the object shape tools require."""
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as error:
            raise ValueError(
                f"Invalid JSON Schema: {error.message}",
            ) from error

        _validate_local_references(value)

        if value.get("type") != "object" or not isinstance(
            value.get("properties"),
            dict,
        ):
            raise ValueError(
                "Tool input_schema must define an object with properties.",
            )

        schema_size = len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        if schema_size > _MAX_INPUT_SCHEMA_BYTES:
            raise ValueError(
                f"Tool input_schema exceeds {_MAX_INPUT_SCHEMA_BYTES} "
                f"bytes.",
            )
        return value


class ClientExternalTool(ToolBase):
    """Runtime proxy for a tool executed by the active chat client."""

    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_external_tool: bool = True
    is_state_injected: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, definition: ClientExternalToolDefinition) -> None:
        """Initialize the proxy from a validated client definition."""
        super().__init__()
        self.name = definition.name
        self.description = definition.description
        self.is_read_only = definition.read_only
        self.input_schema = definition.input_schema

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow client reads and ask before other client-side effects."""
        del tool_input, context
        if self.is_read_only:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Client external tool '{self.name}' is read-only.",
                decision_reason=(
                    f"The active client declared '{self.name}' read-only."
                ),
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=(
                f"Client external tool '{self.name}' may change "
                "client-side state."
            ),
            decision_reason=(
                f"The active client did not declare '{self.name}' read-only."
            ),
        )
