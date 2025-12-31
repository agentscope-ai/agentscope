# -*- coding: utf-8 -*-
"""Factory helpers for registering SubAgent skeletons as toolkit tools."""
from __future__ import annotations
from dataclasses import dataclass
import json
from typing import Any, Callable, TYPE_CHECKING, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic.fields import PydanticUndefined

from .._logging import logger
from ..message import Msg, TextBlock
from ..types import ToolFunction
from .._utils._common import _remove_title_field
from ._agent_base import AgentBase
from ._subagent_base import (
    ContextBundle,
    DelegationContext,
    PermissionBundle,
    SubAgentBase,
    SubAgentUnavailable,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..model import ChatModelBase
    from ..tool import ToolResponse


@dataclass(slots=True)
class SubAgentSpec:
    """Minimal declarative config for subagent registration.

    Only two concerns remain by design:
    - name: the outward tool name registered on the host Toolkit
    - tools: the internal tool functions to batch-register into the subagent's
      own Toolkit (not visible to the model)
    """

    name: str
    tools: list[Callable[..., Any]] | None = None


async def make_subagent_tool(
    cls: type[SubAgentBase],
    spec: SubAgentSpec,
    *,
    host: AgentBase,
    tool_name: str | None = None,
    ephemeral_memory: bool = True,
    override_model: "ChatModelBase" | None = None,
) -> tuple[ToolFunction, dict[str, Any]]:
    """Create a toolkit-ready wrapper for a SubAgentBase subclass.

    Raises:
        SubAgentUnavailable: if registration-time construction probe fails.
    """
    resolved_name = tool_name or f"{spec.name}_tool"

    try:
        input_model = cls.get_input_model()
    except NotImplementedError as error:
        raise SubAgentUnavailable(str(error)) from error

    sample_payload = _build_sample_input(input_model)

    json_schema = _build_model_schema(resolved_name, "", input_model)

    def permissions_builder() -> PermissionBundle:
        return _build_permissions(host)

    context_snapshot = await _build_context_bundle(host)

    initial_permissions = permissions_builder()

    # Resolve model for subagent: explicit override > host.model (fail-fast if none)
    model_for_subagent = override_model if override_model is not None else getattr(host, "model", None)
    if model_for_subagent is None:
        raise SubAgentUnavailable(
            "Host does not provide a ChatModel; make_subagent_tool requires a model "
            "to be propagated to the subagent via model_override.",
        )

    # Registration-time probe: construct once to gate tool exposure.
    try:
        probe_context = cls._pre_context_compress(
            context_snapshot,
            sample_payload,
        )
        await cls.export_agent(
            permissions=initial_permissions,
            parent_context=context_snapshot,
            spec_name=spec.name,
            ephemeral_memory=ephemeral_memory,
            tools=spec.tools,
            model_override=model_for_subagent,
            input_obj=sample_payload,
            delegation_context=probe_context,
        )
    except Exception as error:  # pylint: disable=broad-except
        raise SubAgentUnavailable(str(error)) from error

    async def _invoke_subagent(
        *,
        _host: AgentBase = host,
        _spec: SubAgentSpec = spec,
        _cls: type[SubAgentBase] = cls,
        _ephemeral_memory: bool = ephemeral_memory,
        _permissions_builder: Callable[[], PermissionBundle] = permissions_builder,
        **raw_input: Any,
    ) -> ToolResponse:
        from ..tool import ToolResponse as _ToolResponse

        try:
            input_obj = input_model.model_validate(raw_input)
        except ValidationError as error:
            logger.warning(
                "Validation error for subagent `%s`: %s",
                _spec.name,
                error,
            )
            error_details = json.loads(error.json())
            return _ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "Subagent input validation failed. "
                            f"Details: {error}"
                        ),
                    ),
                ],
                metadata={
                    "unavailable": True,
                    "error": error_details,
                    "subagent": _spec.name,
                    "supervisor": getattr(host, "name", "host"),
                },
            )

        parent_context = await _build_context_bundle(_host)
        permissions = _permissions_builder()

        delegation_context = _cls._pre_context_compress(
            parent_context,
            input_obj,
        )

        _annotate_latest_user_message(parent_context.conversation, delegation_context)

        try:
            subagent = await _cls.export_agent(
                permissions=permissions,
                parent_context=parent_context,
                spec_name=_spec.name,
                ephemeral_memory=_ephemeral_memory,
                tools=_spec.tools,
                model_override=model_for_subagent,
                delegation_context=delegation_context,
                input_obj=input_obj,
            )
        except SubAgentUnavailable as error:
            logger.warning(
                "Subagent `%s` unavailable during export: %s",
                _spec.name,
                error,
            )
            return _ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Delegation skipped: subagent unavailable.",
                    ),
                ],
                metadata={
                    "unavailable": True,
                    "error": str(error),
                    "subagent": _spec.name,
                    "supervisor": permissions.supervisor_name,
                },
            )

        return await subagent.delegate(
            input_obj=input_obj,
            delegation_context=delegation_context,
        )

    register_kwargs = {
        "func_description": "",
        "json_schema": json_schema,
        "preset_kwargs": {},
        "group_name": "subagents",
    }

    _invoke_subagent.__name__ = resolved_name

    return _invoke_subagent, register_kwargs


# no healthcheck helper needed


async def _build_context_bundle(host: AgentBase) -> ContextBundle:
    """Collect host context prior to delegation."""
    conversation: list[Msg] = []
    memory = getattr(host, "memory", None)
    if memory is not None and hasattr(memory, "get_memory"):
        try:
            conversation = list(await memory.get_memory())  # type: ignore[arg-type]
        except Exception:  # pragma: no cover
            conversation = []

    recent_tool_results = [
        msg
        for msg in reversed(conversation)
        if isinstance(msg.content, list)
        and any(block.get("type") == "tool_result" for block in msg.content)
    ][:4]
    recent_tool_results.reverse()

    long_term_refs = []
    long_term_memory = getattr(host, "long_term_memory", None)
    if long_term_memory is not None:
        long_term_refs = [
            {
                "provider": long_term_memory.__class__.__name__,
                "available": True,
            },
        ]

    workspace_handles: list[str] = []

    safety_flags = dict(getattr(host, "safety_limits", {}))

    return ContextBundle(
        conversation=conversation,
        recent_tool_results=recent_tool_results,
        long_term_refs=long_term_refs,
        workspace_handles=workspace_handles,
        safety_flags=safety_flags,
    )


def _build_permissions(host: AgentBase) -> PermissionBundle:
    """Copy host-level shared resources into a bundle."""
    filesystem_service = getattr(host, "filesystem_service", None)
    session = getattr(host, "session", None)
    long_term_memory = getattr(host, "long_term_memory", None)
    safety_limits = dict(getattr(host, "safety_limits", {}))
    tracer = getattr(host, "tracer", None)

    return PermissionBundle(
        logger=logger,
        tracer=tracer,
        filesystem_service=filesystem_service,
        session=session,
        long_term_memory=long_term_memory,
        safety_limits=safety_limits,
        supervisor_name=getattr(host, "name", "host"),
    )


def _annotate_latest_user_message(
    conversation: list[Msg],
    context: DelegationContext,
) -> None:
    """Write delegation context into the latest user message metadata."""
    for msg in reversed(conversation):
        if msg.role == "user":
            metadata = msg.metadata or {}
            metadata["delegation_context"] = context.to_payload()
            msg.metadata = metadata
            break


def _build_model_schema(
    tool_name: str,
    description: str,
    input_model: type[BaseModel],
) -> dict[str, Any]:
    """Construct an OpenAI function schema from the subagent's Pydantic model."""
    schema = input_model.model_json_schema()
    _remove_title_field(schema)
    if "type" not in schema:
        schema["type"] = "object"
    schema.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": schema,
        },
    }


def _build_sample_input(model: type[BaseModel]) -> BaseModel:
    """Construct a best-effort sample payload for registration probes."""
    values: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if field.default is not PydanticUndefined:
            values[name] = field.default
            continue
        default_factory = getattr(field, "default_factory", PydanticUndefined)
        if default_factory is not PydanticUndefined and default_factory is not None:
            values[name] = default_factory()
            continue
        values[name] = _placeholder_value(field.annotation)
    return model.model_construct(**values)


def _placeholder_value(annotation: Any) -> Any:
    """Return a neutral placeholder for the given field annotation."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if annotation is Any:
        return None

    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return _build_sample_input(annotation)
        if annotation is str:
            return ""
        if annotation is int:
            return 0
        if annotation is float:
            return 0.0
        if annotation is bool:
            return False
        if annotation in (list, tuple):
            return []
        if annotation is set:
            return set()
        if annotation is dict:
            return {}
        return None

    if origin in (list, tuple, set):
        return origin() if origin is not tuple else ()

    if origin is dict:
        return {}

    if origin is type(None):  # pragma: no cover
        return None

    # Handle Optional / Union types
    if origin is Union:
        non_none = [arg for arg in args if arg is not type(None)]
        if not non_none:
            return None
        return _placeholder_value(non_none[0])

    return None
