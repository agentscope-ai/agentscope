# -*- coding: utf-8 -*-
"""Client-provided external tool definitions and runtime adapters."""
import json
from collections.abc import Iterator
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
)
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from .._logging import logger
from ..permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ..tool import ToolBase


_MAX_INPUT_SCHEMA_BYTES = 64 * 1024
ClientBuiltinToolName = Literal["AskUser"]
CLIENT_EXTERNAL_TOOL_NAME_PREFIX = "client__"
_REFERENCE_KEYWORDS = {"$ref", "$dynamicRef", "$recursiveRef"}
_SUPPORTED_SCHEMA_URIS = {
    "https://json-schema.org/draft/2020-12/schema",
    "https://json-schema.org/draft/2020-12/schema#",
}
_SINGLE_SCHEMA_KEYWORDS = {
    "additionalProperties",
    "unevaluatedProperties",
    "propertyNames",
    "items",
    "contains",
    "unevaluatedItems",
    "not",
    "if",
    "then",
    "else",
    "contentSchema",
}
_SCHEMA_MAP_KEYWORDS = {
    "$defs",
    "definitions",
    "properties",
    "patternProperties",
    "dependentSchemas",
    "dependencies",
}
_SCHEMA_ARRAY_KEYWORDS = {"allOf", "anyOf", "oneOf", "prefixItems"}


def _iter_schema_nodes(value: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Walk subschemas and local pointer targets, excluding literal data."""
    resource = DRAFT202012.create_resource(value)
    # The default registry cannot retrieve resources from the network.
    pending = [(value, Registry().resolver_with_root(resource))]
    seen: set[tuple[int, int]] = set()
    while pending:
        node, resolver = pending.pop()
        if not isinstance(node, dict):
            continue
        try:
            scope = resolver.lookup("#").contents
        except (Unresolvable, ValueError):
            scope = None
        identity = (id(node), id(scope))
        if identity in seen:
            continue
        seen.add(identity)
        yield node
        for keyword, child in node.items():
            children = []
            if keyword in _SINGLE_SCHEMA_KEYWORDS:
                children = [child]
            elif keyword in _SCHEMA_MAP_KEYWORDS and isinstance(child, dict):
                children = list(child.values())
            elif keyword in _SCHEMA_ARRAY_KEYWORDS and isinstance(child, list):
                children = child
            elif (
                keyword in _REFERENCE_KEYWORDS
                and isinstance(child, str)
                and child.startswith("#")
            ):
                try:
                    resolved = resolver.lookup(child)
                except (Unresolvable, ValueError, TypeError):
                    # Broken references remain tool errors at execution.
                    continue
                pending.append((resolved.contents, resolved.resolver))
            for item in children:
                if isinstance(item, dict):
                    pending.append(
                        (
                            item,
                            resolver.in_subresource(
                                DRAFT202012.create_resource(item),
                            ),
                        ),
                    )


def _validate_schema_metadata(value: dict[str, Any]) -> None:
    """Reject unsupported dialects and references to remote schemas."""
    for node in _iter_schema_nodes(value):
        for key, child in node.items():
            if (
                key == "$schema"
                and isinstance(child, str)
                and child not in _SUPPORTED_SCHEMA_URIS
            ):
                raise ValueError(
                    "Client tool schemas only support JSON Schema "
                    "Draft 2020-12.",
                )
            if (
                key in _REFERENCE_KEYWORDS
                and isinstance(child, str)
                and not child.startswith("#")
            ):
                raise ValueError(
                    f"Client tool schemas only support local {key} values.",
                )


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
                    "bytes.",
                )
            Draft202012Validator.check_schema(value)
            _validate_schema_metadata(value)
        except SchemaError as error:
            raise ValueError(
                f"Invalid JSON Schema: {error.message}",
            ) from error
        except RecursionError as error:
            raise ValueError(
                "Tool input_schema is nested too deeply.",
            ) from error

        if value.get("type") != "object" or not isinstance(
            value.get("properties"),
            dict,
        ):
            raise ValueError(
                "Tool input_schema must define an object with properties.",
            )

        return value


_CLIENT_EXTERNAL_TOOLS_ADAPTER: TypeAdapter = TypeAdapter(
    list[ClientExternalToolDefinition],
)
_CLIENT_BUILTIN_TOOLS_ADAPTER: TypeAdapter = TypeAdapter(
    list[ClientBuiltinToolName],
)


def parse_client_tool_capabilities(
    raw_external_tools: object,
    raw_builtin_tools: object,
) -> tuple[list[ClientExternalToolDefinition], list[ClientBuiltinToolName]]:
    """Decode optional queued capabilities without discarding valid input."""
    try:
        external_tools = (
            _CLIENT_EXTERNAL_TOOLS_ADAPTER.validate_python(raw_external_tools)
            if raw_external_tools is not None
            else []
        )
    except (ValueError, RecursionError):
        logger.exception("Ignoring invalid queued client external tools.")
        external_tools = []
    try:
        builtin_tools = (
            _CLIENT_BUILTIN_TOOLS_ADAPTER.validate_python(raw_builtin_tools)
            if raw_builtin_tools is not None
            else []
        )
    except (ValueError, RecursionError):
        logger.exception("Ignoring invalid queued client builtin tools.")
        builtin_tools = []
    return external_tools, builtin_tools


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
