# -*- coding: utf-8 -*-
"""Framework skeleton for agents exposed as toolkit tools."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, ClassVar, TYPE_CHECKING, TypedDict, Callable

from .._logging import logger
from ..message import Msg, TextBlock
from ..session import SessionBase
from ..types import JSONSerializableObject
from ._agent_base import AgentBase

if TYPE_CHECKING:  # pragma: no cover
    from ..memory._memory_base import MemoryBase
    from ..memory._long_term_memory_base import LongTermMemoryBase
    from ..model import ChatModelBase
    from ..tool import Toolkit, ToolResponse

if False:  # pragma: no cover
    from opentelemetry.trace import Tracer  # type: ignore[attr-defined]
else:  # pragma: no cover
    Tracer = Any


@dataclass(slots=True)
class PermissionBundle:
    """Shared resources copied from the host agent.

    Filesystem policy is provided via a FileDomainService instance owned by
    the host. The subagent skeleton must not create or widen policy.
    """

    logger: logging.Logger
    tracer: Tracer | None = None
    filesystem_service: "object | None" = None  # FileDomainService at runtime
    session: SessionBase | None = None
    long_term_memory: "LongTermMemoryBase" | None = None
    safety_limits: dict[str, JSONSerializableObject] = field(
        default_factory=dict,
    )
    supervisor_name: str = "host"


@dataclass(slots=True)
class ContextBundle:
    """Snapshot of the host agent state before delegation."""

    conversation: list[Msg] = field(default_factory=list)
    recent_tool_results: list[Msg] = field(default_factory=list)
    long_term_refs: list[dict[str, JSONSerializableObject]] = field(
        default_factory=list,
    )
    workspace_handles: list[str] = field(default_factory=list)
    safety_flags: dict[str, JSONSerializableObject] = field(
        default_factory=dict,
    )


@dataclass(slots=True)
class DelegationContext:
    """Normalized payload shared with subagents."""

    task_summary: str
    recent_events: list[dict[str, JSONSerializableObject]]
    long_term_refs: list[dict[str, JSONSerializableObject]]
    workspace_pointers: list[str]
    safety_flags: dict[str, JSONSerializableObject]

    def to_payload(self) -> dict[str, JSONSerializableObject]:
        """Convert to metadata payload."""
        return {
            "task_summary": self.task_summary,
            "recent_events": self.recent_events,
            "long_term_refs": self.long_term_refs,
            "workspace_pointers": self.workspace_pointers,
            "safety_flags": self.safety_flags,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, JSONSerializableObject]) -> (
        "DelegationContext"
    ):
        """Hydrate from metadata payload."""
        return cls(
            task_summary=str(payload.get("task_summary", "")),
            recent_events=list(payload.get("recent_events", [])),
            long_term_refs=list(payload.get("long_term_refs", [])),
            workspace_pointers=list(payload.get("workspace_pointers", [])),
            safety_flags=dict(payload.get("safety_flags", {})),
        )


class SubAgentUnavailable(RuntimeError):
    """Raised when the subagent skeleton cannot be exported."""


class ToolSpec(TypedDict, total=False):
    func: Callable[..., Any]
    group: str
    preset_kwargs: dict[str, Any]
    func_description: str
    json_schema: dict[str, Any]


class SubAgentBase(AgentBase):
    """Skeleton agent dedicated to delegation-as-tool flows."""

    spec_name: ClassVar[str] = "subagent"

    def __init__(
        self,
        *,
        permissions: PermissionBundle,
        spec_name: str,
        toolkit: "Toolkit" | None = None,
        memory: "MemoryBase" | None = None,
        tools: list[Callable[..., Any] | ToolSpec] | None = None,
        model_override: "ChatModelBase" | None = None,
        ephemeral_memory: bool = True,
    ) -> None:
        super().__init__()

        self.permissions = permissions
        self.spec_name = spec_name
        self.model_override = model_override
        if toolkit is None:
            from ..tool import Toolkit as _Toolkit
            toolkit = _Toolkit()
            if tools:
                self._hydrate_toolkit(toolkit, tools)
        self.toolkit = toolkit
        # Auto-inherit full filesystem toolset when a service is provided.
        svc = permissions.filesystem_service
        if svc is not None and hasattr(svc, "tools"):
            try:
                for func, bound in svc.tools():
                    name = getattr(func, "__name__", None)
                    if name and name in self.toolkit.tools:
                        continue
                    self.toolkit.register_tool_function(
                        func,
                        preset_kwargs={"service": bound},
                    )
            except Exception:
                pass
        if memory is None:
            from ..memory._in_memory_memory import InMemoryMemory

            memory = InMemoryMemory()
        self.memory = memory
        self._delegation_context: DelegationContext | None = None
        self._ephemeral_memory = ephemeral_memory

        # Inherit host-provided FileDomainService as-is; no hardcoded namespace.
        self.filesystem_service = permissions.filesystem_service

        self.set_console_output_enabled(False)
        self.set_msg_queue_enabled(False)

    def _hydrate_toolkit(
        self,
        toolkit: "Toolkit",
        tools: list[Callable[..., Any] | ToolSpec],
    ) -> None:
        """Batch-register provided tools into the subagent's own Toolkit."""
        for entry in tools:
            if callable(entry):
                toolkit.register_tool_function(entry)
            else:
                func = entry.get("func")
                if not callable(func):
                    continue
                toolkit.register_tool_function(
                    func,  # type: ignore[arg-type]
                    group_name=entry.get("group", "basic"),
                    preset_kwargs=dict(entry.get("preset_kwargs", {})),
                    func_description=entry.get("func_description", ""),
                    json_schema=entry.get("json_schema"),
                )

    @classmethod
    def _pre_context_compress(
        cls,
        parent_context: ContextBundle,
        task: str,
    ) -> DelegationContext:
        """Build a normalized delegation payload."""
        recent_events = [
            _summarize_msg(msg) for msg in parent_context.recent_tool_results
        ][:4]

        if not recent_events and parent_context.conversation:
            recent_events = [
                _summarize_msg(parent_context.conversation[-1]),
            ]

        return DelegationContext(
            task_summary=task,
            recent_events=recent_events,
            long_term_refs=list(parent_context.long_term_refs),
            workspace_pointers=list(parent_context.workspace_handles),
            safety_flags=dict(parent_context.safety_flags),
        )

    @classmethod
    async def export_agent(
        cls,
        *,
        permissions: PermissionBundle,
        parent_context: ContextBundle,
        task: str,
        spec_name: str,
        ephemeral_memory: bool = True,
        tools: list[Callable[..., Any] | ToolSpec] | None = None,
        model_override: "ChatModelBase" | None = None,
        delegation_context: DelegationContext | None = None,
    ) -> "SubAgentBase":
        """Construct a fresh subagent instance (no healthcheck)."""
        _ = (parent_context, task)
        instance = cls(
            permissions=permissions,
            spec_name=spec_name,
            tools=tools,
            model_override=model_override,
            ephemeral_memory=ephemeral_memory,
        )

        if delegation_context is not None:
            instance.load_delegation_context(delegation_context)

        return instance

    def load_delegation_context(
        self,
        context: DelegationContext,
    ) -> None:
        """Store the delegation context for later retrieval."""
        self._delegation_context = context

    async def delegate(
        self,
        *,
        task_summary: str,
        delegation_context: DelegationContext | None = None,
        **kwargs: Any,
    ) -> "ToolResponse":
        """Uniform delegate entrypoint invoked by host agents."""
        context = delegation_context or self._delegation_context

        if context is None:
            context = DelegationContext(
                task_summary=task_summary,
                recent_events=[],
                long_term_refs=[],
                workspace_pointers=[],
                safety_flags={},
            )

        self.load_delegation_context(context)

        synthetic_msg = Msg(
            name=self.permissions.supervisor_name,
            content=task_summary,
            role="user",
            metadata={
                "delegation_context": context.to_payload(),
            },
        )

        await self.memory.add(synthetic_msg)

        try:
            reply_msg = await self.reply(synthetic_msg, **kwargs)
        except Exception as error:  # pylint: disable=broad-except
            logger.warning(
                "Subagent `%s` failed: %s",
                self.spec_name,
                error,
                exc_info=True,
            )
            from ..tool import ToolResponse as _ToolResponse

            return _ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "Subagent execution unavailable. "
                            "See metadata for diagnostics."
                        ),
                    ),
                ],
                metadata={
                    "unavailable": True,
                    "error": str(error),
                    "subagent": self.spec_name,
                    "supervisor": self.permissions.supervisor_name,
                },
            )
        finally:
            if self._ephemeral_memory:
                await self.memory.clear()

        if not isinstance(reply_msg, Msg):
            from ..tool import ToolResponse as _ToolResponse

            return _ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Subagent produced invalid reply payload.",
                    ),
                ],
                metadata={
                    "unavailable": True,
                    "error": "invalid_reply",
                    "subagent": self.spec_name,
                    "supervisor": self.permissions.supervisor_name,
                },
            )

        return _msg_to_tool_response(
            reply_msg,
            subagent_name=self.spec_name,
            supervisor=self.permissions.supervisor_name,
            context=context,
        )


def _summarize_msg(msg: Msg) -> dict[str, JSONSerializableObject]:
    """Produce a compact representation of a message."""
    if isinstance(msg.content, str):
        preview = msg.content[:200]
    else:
        preview = []
        for block in msg.get_content_blocks():
            block_type = block.get("type")
            if block_type == "text":
                preview.append(str(block.get("text", "")))
            elif block_type == "tool_result":
                preview.append(
                    f"[tool_result:{block.get('name', '')}]",
                )
        preview = " ".join(preview)[:200]

    return {
        "id": msg.id,
        "role": msg.role,
        "name": msg.name,
        "preview": preview,
        "timestamp": msg.timestamp,
    }


def _msg_to_tool_response(
    msg: Msg,
    *,
    subagent_name: str,
    supervisor: str,
    context: DelegationContext,
) -> "ToolResponse":
    """Convert a message into a ToolResponse chunk."""
    from ..tool import ToolResponse as _ToolResponse

    if isinstance(msg.content, str):
        content_blocks = [
            TextBlock(
                type="text",
                text=msg.content,
            ),
        ]
    else:
        allowed_types = {"text", "image", "audio"}
        content_blocks = [
            block
            for block in msg.get_content_blocks()
            if block.get("type") in allowed_types
        ]

        if not content_blocks:
            content_blocks = [
                TextBlock(
                    type="text",
                    text="",
                ),
            ]

    metadata = {
        "subagent": subagent_name,
        "supervisor": supervisor,
        "delegation_context": context.to_payload(),
    }
    if msg.metadata:
        metadata["response_metadata"] = msg.metadata

    return _ToolResponse(
        content=content_blocks,  # type: ignore[arg-type]
        metadata=metadata,
        is_last=True,
    )
