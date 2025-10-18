# -*- coding: utf-8 -*-
"""Factory helpers for registering SubAgent skeletons as toolkit tools."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from .._logging import logger
from ..message import Msg, TextBlock
from ..model import ChatModelBase
from ..types import ToolFunction
from ._agent_base import AgentBase
from ._subagent_base import (
    ContextBundle,
    DelegationContext,
    PermissionBundle,
    SubAgentBase,
    SubAgentUnavailable,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..tool import ToolResponse


@dataclass(slots=True)
class SubAgentSpec:
    """Declarative configuration for a subagent registration."""

    name: str
    description: str
    tools_allowlist: list[str] | None = None
    model_override: ChatModelBase | None = None
    tags: list[str] | None = None
    healthcheck: Callable[[], Awaitable[bool]] | None = None
    group_name: str = "subagents"
    json_schema: dict[str, Any] | None = None


async def make_subagent_tool(
    cls: type[SubAgentBase],
    spec: SubAgentSpec,
    *,
    host: AgentBase,
    tool_name: str | None = None,
    ephemeral_memory: bool = True,
) -> tuple[ToolFunction, dict[str, Any]]:
    """Create a toolkit-ready wrapper for a SubAgentBase subclass.

    Raises:
        SubAgentUnavailable: if registration-time healthcheck fails.
    """
    resolved_name = tool_name or f"{spec.name}_tool"

    def permissions_builder() -> PermissionBundle:
        return _build_permissions(host)

    context_snapshot = await _build_context_bundle(host)

    initial_permissions = permissions_builder()

    try:
        await cls.export_agent(
            permissions=initial_permissions,
            parent_context=context_snapshot,
            task="registration-healthcheck",
            spec_name=spec.name,
            ephemeral_memory=ephemeral_memory,
            tools_allowlist=spec.tools_allowlist,
            model_override=spec.model_override,
            host_toolkit=getattr(host, "toolkit", None),
            delegation_context=None,
            run_healthcheck=True,
        )
    except SubAgentUnavailable:
        raise
    except Exception as error:  # pylint: disable=broad-except
        raise SubAgentUnavailable(str(error)) from error

    if spec.healthcheck is not None:
        ok = await _ensure_async(spec.healthcheck)
        if not ok:
            raise SubAgentUnavailable(
                f"Spec healthcheck rejected subagent `{spec.name}`.",
            )

    async def _invoke_subagent(
        task_summary: str,
        *,
        _host: AgentBase = host,
        _spec: SubAgentSpec = spec,
        _cls: type[SubAgentBase] = cls,
        _ephemeral_memory: bool = ephemeral_memory,
        _permissions_builder: Callable[[], PermissionBundle] = permissions_builder,
    ) -> ToolResponse:
        parent_context = await _build_context_bundle(_host)
        permissions = _permissions_builder()

        delegation_context = _cls._pre_context_compress(
            parent_context,
            task_summary,
        )

        _annotate_latest_user_message(parent_context.conversation, delegation_context)

        try:
            subagent = await _cls.export_agent(
                permissions=permissions,
                parent_context=parent_context,
                task=task_summary,
                spec_name=_spec.name,
                ephemeral_memory=_ephemeral_memory,
                tools_allowlist=_spec.tools_allowlist,
                model_override=_spec.model_override,
                host_toolkit=getattr(_host, "toolkit", None),
                delegation_context=delegation_context,
                run_healthcheck=False,
            )
        except SubAgentUnavailable as error:
            from ..tool import ToolResponse as _ToolResponse

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
            task_summary=task_summary,
            delegation_context=delegation_context,
        )

    json_schema = spec.json_schema or _build_default_schema(
        resolved_name,
        spec.description,
    )

    register_kwargs = {
        "func_description": spec.description,
        "json_schema": json_schema,
        "preset_kwargs": {},
        "group_name": spec.group_name,
    }

    _invoke_subagent.__name__ = resolved_name

    return _invoke_subagent, register_kwargs


async def _ensure_async(func: Callable[[], Awaitable[bool]]) -> bool:
    """Await healthcheck callables regardless of sync/async style."""
    result = func()
    if asyncio.iscoroutine(result):
        return await result
    return bool(result)


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
    filesystem = getattr(host, "filesystem", None)
    session = getattr(host, "session", None)
    long_term_memory = getattr(host, "long_term_memory", None)
    safety_limits = dict(getattr(host, "safety_limits", {}))
    tracer = getattr(host, "tracer", None)

    return PermissionBundle(
        logger=logger,
        tracer=tracer,
        filesystem=filesystem,
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


def _build_default_schema(
    tool_name: str,
    description: str,
) -> dict[str, Any]:
    """Construct a minimal JSON schema for the subagent tool."""
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task_summary": {
                        "type": "string",
                        "description": (
                            "Concise summary of the delegated task for the "
                            "subagent to execute."
                        ),
                    },
                },
                "required": ["task_summary"],
            },
        },
    }
