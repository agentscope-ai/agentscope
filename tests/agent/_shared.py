# -*- coding: utf-8 -*-
"""Shared fixtures and test doubles for subagent skeleton tests."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import BaseModel

from agentscope.agent import (
    ReActAgent,
    SubAgentBase,
    SubAgentSpec,
)
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, TextBlock, ToolUseBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit
from agentscope.filesystem._memory import InMemoryFileSystem
from agentscope.filesystem._errors import AccessDeniedError
from agentscope.filesystem._service import FileDomainService

if TYPE_CHECKING:  # pragma: no cover
    from agentscope.tool import ToolResponse


class StaticModel(ChatModelBase):
    """Deterministic chat model returning a no-op text chunk."""

    def __init__(self) -> None:
        super().__init__("static", stream=False)

    # type: ignore[override]
    async def __call__(self, _messages, **_) -> ChatResponse:
        return ChatResponse(
            content=[
                TextBlock(
                    type="text",
                    text="noop",
                ),
            ],
        )


def build_host_agent(*, parallel: bool = False) -> ReActAgent:
    """Construct a host agent with deterministic components."""
    return ReActAgent(
        name="Supervisor",
        sys_prompt="You are a supervisor.",
        model=StaticModel(),
        formatter=DashScopeChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=Toolkit(),
        parallel_tool_calls=parallel,
    )


def attach_filesystem(agent: ReActAgent) -> None:
    """Attach a policy-bound FileDomainService to the host agent.

    Grants only the subagent-specific workspace: /workspace/subagents/fs/
    (used by NamespaceSubAgent test). No broader /workspace/ access.
    """
    fs = InMemoryFileSystem()
    handle = fs.create_handle(
        [
            {
                "prefix": "/workspace/subagents/fs/",
                "ops": {
                    "list",
                    "file",
                    "read_binary",
                    "read_file",
                    "read_re",
                    "write",
                    "delete",
                },
            },
        ],
    )
    agent.filesystem_service = FileDomainService(handle)


class SubAgentInput(BaseModel):
    message: str = "placeholder"
    tag: str | None = None
    delay: float = 0.0


class EchoSubAgent(SubAgentBase):
    """Minimal subagent implementation used for skeleton verification."""

    memory_events: list[int] = []
    console_states: list[bool] = []
    queue_states: list[bool] = []
    delegation_payloads: list[dict] = []
    filesystem_roots: list[str] = []

    InputModel = SubAgentInput

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        await self.memory.add(msg)

    async def reply(
        self,
        input_obj: SubAgentInput,
        **_,
    ) -> Msg:
        self.__class__.console_states.append(self._disable_console_output)
        self.__class__.queue_states.append(self._disable_msg_queue)

        size = await self.memory.size()
        self.__class__.memory_events.append(size)

        context = getattr(self, "_delegation_context", None)
        if context:
            self.__class__.delegation_payloads.append(context.to_payload())

        # No hardcoded filesystem root under new policy; keep placeholder list.

        text = input_obj.message
        return Msg(
            name=self.spec_name,
            content=f"echo:{text}",
            role="assistant",
        )

    @classmethod
    def reset(cls) -> None:
        cls.memory_events = []
        cls.console_states = []
        cls.queue_states = []
        cls.delegation_payloads = []
        cls.filesystem_roots = []


class RecordingSubAgent(EchoSubAgent):
    """Subagent variant capturing delegation context metadata."""

    async def reply(
        self,
        input_obj: SubAgentInput,
        **kwargs,
    ) -> Msg:
        response = await super().reply(input_obj, **kwargs)
        context = getattr(self, "_delegation_context", None)
        payload = context.to_payload() if context else {}
        response.metadata = {"delegation_context": payload}
        return response


class FailingSubAgent(EchoSubAgent):
    """Subagent raising to test failure propagation."""

    async def reply(
        self,
        input_obj: SubAgentInput,
        **kwargs,
    ) -> Msg:
        raise RuntimeError("delegation failed")


# Removed legacy healthcheck-based stub; constructor probe is used instead.


class CountingSubAgent(EchoSubAgent):
    """Counts context compression invocations."""

    compress_calls: int = 0

    @classmethod
    def _pre_context_compress(
        cls,
        parent_context,
        input_obj: SubAgentInput,
    ):
        cls.compress_calls += 1
        return super()._pre_context_compress(parent_context, input_obj)

    @classmethod
    def reset(cls) -> None:
        super().reset()
        cls.compress_calls = 0


class AllowlistSubAgent(SubAgentBase):
    """Records tools visible to the subagent toolkit."""

    seen_tools: list[set[str]] = []
    InputModel = SubAgentInput

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        await self.memory.add(msg)

    async def reply(
        self,
        input_obj: SubAgentInput,
        **_,
    ) -> Msg:
        self.__class__.seen_tools.append(set(self.toolkit.tools.keys()))
        return Msg(
            name=self.spec_name,
            content="allowlist-ok",
            role="assistant",
        )

    @classmethod
    def reset(cls) -> None:
        cls.seen_tools = []


class NamespaceSubAgent(SubAgentBase):
    """Attempts to write inside and outside the delegated namespace."""

    writes: list[str] = []
    errors: list[str] = []
    InputModel = SubAgentInput

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        await self.memory.add(msg)

    async def reply(
        self,
        input_obj: SubAgentInput,
        **_,
    ) -> Msg:
        if self.filesystem_service is None:
            raise AssertionError("filesystem service not injected")

        allowed_path = "/workspace/subagents/fs/artifact.txt"
        self.filesystem_service.write_file(allowed_path, "ok")
        self.__class__.writes.append(allowed_path)

        try:
            self.filesystem_service.write_file(
                "/workspace/forbidden.txt",
                "nope",
            )
        except AccessDeniedError as exc:
            self.__class__.errors.append(type(exc).__name__)

        return Msg(
            name=self.spec_name,
            content="fs-checked",
            role="assistant",
        )

    @classmethod
    def reset(cls) -> None:
        cls.writes = []
        cls.errors = []


class ParallelSubAgent(SubAgentBase):
    """Records execution order and memory isolation for concurrent calls."""

    memory_sizes: list[int] = []
    order: list[str] = []
    InputModel = SubAgentInput

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        await self.memory.add(msg)

    async def reply(
        self,
        input_obj: SubAgentInput,
        **kwargs,
    ) -> Msg:
        delay, tag = input_obj.delay, input_obj.tag or "unknown"
        await asyncio.sleep(delay)
        size = await self.memory.size()
        self.__class__.memory_sizes.append(size)
        self.__class__.order.append(tag)
        return Msg(
            name=self.spec_name,
            content=f"parallel:{tag}",
            role="assistant",
        )

    @classmethod
    def reset(cls) -> None:
        cls.memory_sizes = []
        cls.order = []


class PermissionSubAgent(SubAgentBase):
    """Captures injected permission bundle for verification."""

    permissions_snapshot = None
    InputModel = SubAgentInput

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        await self.memory.add(msg)

    async def reply(
        self,
        input_obj: SubAgentInput,
        **_,
    ) -> Msg:
        self.__class__.permissions_snapshot = self.permissions
        return Msg(
            name=self.spec_name,
            content="permissions-ok",
            role="assistant",
        )

    @classmethod
    def reset(cls) -> None:
        cls.permissions_snapshot = None


async def invoke_tool(
    agent: ReActAgent,
    tool_call: ToolUseBlock,
) -> "ToolResponse":
    """Execute a registered tool and return the final ToolResponse."""
    chunk = None
    response_stream = await agent.toolkit.call_tool_function(tool_call)
    async for chunk in response_stream:
        pass
    assert chunk is not None
    return chunk


def build_spec(name: str) -> SubAgentSpec:
    """Factory for common spec configuration."""
    return SubAgentSpec(name=name)
