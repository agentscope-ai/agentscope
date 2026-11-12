# -*- coding: utf-8 -*-
"""Single model-facing entry point over an existing native tool catalog."""

import asyncio
import hashlib
import json
import keyword
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from ._config import CodeModeConfig
from ._script import Script
from ..message import Msg, ToolCallBlock, UserMsg
from ..permission import PermissionBehavior, PermissionDecision
from ..state import AgentState
from ..tool import (
    BackendBase,
    LocalBackend,
    ToolBase,
    ToolChunk,
    ToolChoice,
    Toolkit,
    ToolResponse,
)
from ..tool._execution_context import nested_tool_call


DESCRIPTION = """Run Python in a fresh process with top-level await.
Variables do not persist. Use await tools.<python_name>(**arguments) for all
native tool actions. ALL_TOOLS is the initial catalog; await search_tools()
refreshes the available catalog after changing tool groups. Use
await describe_tool(name) to inspect exact argument schemas and aliases.
Tool results contain content (native text/data blocks), metadata and state.
Text is not automatically parsed as JSON. Filter full results in Python;
text(value), print(), the last expression, image/audio/video(block), and
ordinary stdout/stderr are sent back to the model within byte/block limits.
On backends without process-tree isolation, a best-effort guard rejects
standard process-launch APIs. It is bypassable, not a security boundary.
Use enabled native command tools through tools. Subprocess stdin is EOF.
Output helpers are synchronous. Media requires
inline data or a source returned by a tool. Independent operations can use
asyncio.gather; await every task before exiting. Failed or denied calls raise
exceptions. Cancelling an awaited call cancels its host task and waits for
cleanup; completed effects are not rolled back. Nested calls requiring
confirmation or external execution fail without executing the action.
Use run_code even when other instructions name a native tool directly.
For SOP submissions, await all earlier calls and make SubmitHandover or
SubmitVerdict the last native tool call in the script.
Scripts have time, CPU, memory, message and output limits. LocalBackend runs
with host user permissions; Python isolation flags are not a security sandbox.
Approval authorizes the entire script. Direct Python file and environment
access is not checked against native Write/Bash permission rules.
"""


def python_name(name: str) -> str:
    """Use stable aliases independent of catalog order or active groups."""
    if name.isascii() and name.isidentifier() and not keyword.iskeyword(name):
        if not name.startswith(("_", "tool_")):
            return name
    return f"tool_{hashlib.sha256(name.encode('utf-8')).hexdigest()}"


class CodeModeToolkit(Toolkit):
    """Wrap a native Toolkit and expose only ``run_code`` to the model.

    Pass this toolkit to one Agent. The backend and cwd explicitly select
    where generated Python executes. Install ``agentscope[code-mode]``.
    LocalBackend and BubblewrapBackend support the initial process contract.
    Mutate tool groups, MCPs and skill loaders through ``catalog``.
    ``auto_approve=True`` explicitly authorizes generated Python without the
    default confirmation. Native tool deny rules still apply to RPC calls;
    direct Python operations are outside those rules.
    """

    def __init__(
        self,
        catalog: Toolkit,
        *,
        backend: BackendBase,
        cwd: str,
        python_executable: str | None = None,
        config: CodeModeConfig | None = None,
        auto_approve: bool = False,
    ) -> None:
        if isinstance(catalog, CodeModeToolkit):
            raise ValueError("Code Mode toolkits cannot be nested.")
        if any(
            tool.name == CodeModeTool.name
            for group in catalog.tool_groups
            for tool in group.tools
        ):
            raise ValueError("The native name 'run_code' is reserved.")
        if type(backend).start_process is BackendBase.start_process:
            raise NotImplementedError(
                f"{type(backend).__name__} does not support Code Mode.",
            )
        self.catalog: Toolkit = catalog
        self.backend: BackendBase = backend
        self.cwd: str = cwd
        self.python: str = python_executable or (
            sys.executable if isinstance(backend, LocalBackend) else "python3"
        )
        self.config: CodeModeConfig = config or CodeModeConfig()
        # Explicit SDK opt-in; app callers always use the permission engine.
        self.auto_approve = auto_approve
        self.allowed_tools: frozenset[str] | None = None
        self.required_tool: str | None = None
        self._state: AgentState | None = None
        self.executor: Callable[..., Awaitable[ToolResponse]] | None = None
        self.condition = asyncio.Condition()
        self.readers = 0
        self.writer = False
        self.waiting_writers = 0
        self.code = CodeModeTool(self)
        super().__init__(tools=[self.code])

    def bind(
        self,
        executor: Callable[..., Awaitable[ToolResponse]],
        state: AgentState,
    ) -> None:
        """Bind to one agent's permission-aware execution entry point."""
        if self.executor is not None and self.executor != executor:
            raise ValueError("Create a separate CodeModeToolkit per Agent.")
        self.executor = executor
        self._state = state
        saved = state.middle_context.get("code_mode_tool_choice", {})
        if saved.get("reply_id") == state.reply_id:
            names = saved.get("tools")
            self.allowed_tools = None if names is None else frozenset(names)
            self.required_tool = saved.get("required_tool")

    async def definitions(
        self,
        groups: list[str] | None = None,
    ) -> dict[str, dict]:
        """Read current schemas from the original catalog."""
        schemas = await self.catalog.get_tool_schemas(groups)
        definitions = {}
        for schema in schemas:
            function = schema["function"]
            name = function["name"]
            if name == self.code.name:
                raise ValueError("The native name 'run_code' is reserved.")
            definitions[name] = {
                **function,
                "python_name": python_name(name),
            }
        return definitions

    async def get_tool_schemas(
        self,
        groups: list[str] | None = None,
    ) -> list[dict]:
        """Expose one entry with bounded native tool discovery hints."""
        definitions = await self.definitions(groups)
        sections = []
        remaining = 12000
        for definition in definitions.values():
            if (
                self.allowed_tools is not None
                and definition["name"] not in self.allowed_tools
            ):
                continue
            section = json.dumps(definition, ensure_ascii=False)
            if len(section) <= remaining:
                sections.append(section)
                remaining -= len(section)
        schemas = await super().get_tool_schemas()
        process_policy = (
            "Direct subprocesses are enabled by this backend."
            if self.backend.isolates_process_tree is True
            else "Standard process-launch APIs are guarded, not isolated."
        )
        schemas[0]["function"]["description"] = (
            f"{DESCRIPTION}\n{process_policy}\n"
            "Available contracts (possibly partial):\n" + "\n".join(sections)
        )
        return schemas

    async def get_skill_instructions(
        self,
        activated_groups: list[str] | None = None,
    ) -> str:
        """Keep native skill guidance and route its viewer through Python."""
        instructions = await self.catalog.get_skill_instructions(
            activated_groups,
        )
        return (
            f"{instructions or ''}\nUse run_code to call native tools. "
            "Use describe_tool(name) to obtain their Python aliases."
        )

    async def get_tool(self, name: str) -> ToolBase | None:
        """Allow middleware to inspect nested native tool metadata."""
        if name == self.code.name:
            return self.code
        return await self.catalog.get_tool(name)

    async def check_tool_available(
        self,
        tool_name: str,
        activated_groups: list[str],
    ) -> ToolBase:
        """Only host-controlled nested calls may reach native tools."""
        if nested_tool_call.get() and tool_name != self.code.name:
            if (
                self.allowed_tools is not None
                and tool_name not in self.allowed_tools
            ):
                raise ValueError(f"Tool excluded by tool choice: {tool_name}")
            return await self.catalog.check_tool_available(
                tool_name,
                activated_groups,
            )
        return await super().check_tool_available(tool_name, activated_groups)

    async def call_tool(
        self,
        tool_call: ToolCallBlock,
        state: AgentState,
    ) -> AsyncGenerator[ToolChunk | ToolResponse, None]:
        """Route nested execution without copying or mutating registrations."""
        if nested_tool_call.get() and tool_call.name != self.code.name:
            stream = self.catalog.call_tool(tool_call, state)
        else:
            stream = super().call_tool(tool_call, state)
        async for item in stream:
            yield item

    async def add_tool(
        self,
        tool: ToolBase | list[ToolBase],
        group_name: str = "basic",
    ) -> None:
        """Keep dynamic registrations in the original toolkit."""
        items = tool if isinstance(tool, list) else [tool]
        if any(item.name == self.code.name for item in items):
            raise ValueError("The name 'run_code' is reserved.")
        await self.catalog.add_tool(tool, group_name)

    async def remove_tool(self, tool_name: str | list[str]) -> None:
        """Remove dynamic tools from their actual registration source."""
        await self.catalog.remove_tool(tool_name)

    def clear(self) -> None:
        """Clear native registrations while preserving the code entry."""
        self.catalog.clear()

    def adapt_tool_choice(
        self,
        messages: list[Msg],
        choice: ToolChoice | None,
    ) -> tuple[list[Msg], ToolChoice | None]:
        """Map forced native calls, including structured output, to code."""
        self.allowed_tools = None
        self.required_tool = None
        if choice is not None and choice.mode not in (
            "none",
            "auto",
            "required",
            self.code.name,
        ):
            if choice.tools is not None and choice.mode not in choice.tools:
                raise ValueError(
                    f"Forced tool excluded by tool choice: {choice.mode}",
                )
            self.required_tool = choice.mode
        if choice is not None and choice.tools is not None:
            if self.code.name not in choice.tools:
                self.allowed_tools = frozenset(choice.tools)
            choice = ToolChoice(mode=choice.mode)
            if self.allowed_tools == frozenset():
                choice = ToolChoice(mode="none")
        if choice is not None and choice.mode == "none":
            self.allowed_tools = frozenset()
        if self.required_tool is not None:
            self.allowed_tools = frozenset([self.required_tool])
        if self.allowed_tools:
            hint = UserMsg(
                name="system",
                content=(
                    "Inside run_code, only these native tools are "
                    f"allowed for this turn: {sorted(self.allowed_tools)}"
                ),
            )
            messages = [*messages, hint]
        if self._state is not None:
            # Keep host restrictions when an app rebuilds a parked agent.
            self._state.middle_context["code_mode_tool_choice"] = {
                "reply_id": self._state.reply_id,
                "required_tool": self.required_tool,
                "tools": (
                    sorted(self.allowed_tools)
                    if self.allowed_tools is not None
                    else None
                ),
            }
        if choice is None or choice.mode in ("none", "auto", "required"):
            return messages, choice
        if choice.mode == self.code.name:
            return messages, choice
        hint = UserMsg(
            name="system",
            content=(
                f"Call native tool {choice.mode!r} inside run_code now. "
                "Use describe_tool for its exact arguments and Python name."
            ),
        )
        return [*messages, hint], ToolChoice(mode=self.code.name)

    @asynccontextmanager
    async def execution_slot(
        self,
        tool: ToolBase,
    ) -> AsyncGenerator[None, None]:
        """Keep unsafe or state-injected native calls exclusive."""
        safe = tool.is_concurrency_safe and not tool.is_state_injected
        async with self.condition:
            if not safe:
                self.waiting_writers += 1
            try:
                await self.condition.wait_for(
                    lambda: not self.writer
                    and (
                        self.readers < 32 and not self.waiting_writers
                        if safe
                        else self.readers == 0
                    ),
                )
                if safe:
                    self.readers += 1
                else:
                    self.writer = True
            finally:
                if not safe:
                    self.waiting_writers -= 1
                    self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                if safe:
                    self.readers -= 1
                else:
                    self.writer = False
                self.condition.notify_all()


class CodeModeTool(ToolBase):
    """Run one independent worker using the enclosing native catalog."""

    name = "run_code"
    description = DESCRIPTION
    is_read_only = False
    is_concurrency_safe = False
    is_state_injected = True
    input_schema = {
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
        "additionalProperties": False,
    }

    def __init__(self, toolkit: CodeModeToolkit) -> None:
        super().__init__()
        self.toolkit = toolkit

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Authorize the entire script through the normal permission policy."""
        return PermissionDecision(
            behavior=(
                PermissionBehavior.ALLOW
                if self.toolkit.auto_approve
                else PermissionBehavior.PASSTHROUGH
            ),
            message=(
                "Approve arbitrary Python execution on the selected backend. "
                "Direct Python operations bypass native tool rules."
            ),
        )

    async def call(self, code: str, _agent_state: AgentState) -> ToolChunk:
        """Run a script with invocation-local output and RPC state."""
        if self.toolkit.executor is None:
            raise RuntimeError("Pass CodeModeToolkit to an Agent first.")
        return await Script(self.toolkit, _agent_state).run(code)
