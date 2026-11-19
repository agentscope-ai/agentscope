# -*- coding: utf-8 -*-
"""Observe bounded retries through the real Toolkit, without an API key."""
import asyncio
from typing import Any, AsyncGenerator, Callable, Literal

from agentscope.message import ToolCallBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import (
    FunctionTool,
    ToolBase,
    ToolChunk,
    Toolkit,
    ToolMiddlewareBase,
    ToolResponse,
)


class TransientLookupError(Exception):
    """A fixture failure explicitly eligible for retry before output."""


class ReadOnlyRetryMiddleware(ToolMiddlewareBase):
    """Retry a declared read-only lookup at most three times before output.

    This example policy handles only ``TransientLookupError``. A tool's
    read-only declaration is trusted metadata, not a side-effect guarantee.
    """

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
    ) -> AsyncGenerator[ToolChunk, None]:
        """Forward chunks; never replay after a chunk or other exception."""
        emitted = False
        for attempt in range(1, 4):
            try:
                async for chunk in next_handler(**input_kwargs):
                    emitted = True
                    yield chunk
                return
            except TransientLookupError:
                if not tool.is_read_only or emitted or attempt == 3:
                    raise


class LocalLookup:
    """Read a fixed local status after a chosen number of fixture failures."""

    def __init__(self, failures: int) -> None:
        """Configure deterministic failures and an observable attempt count."""
        self.failures = failures
        self.attempts = 0

    async def lookup(self) -> str:
        """Return the fixed status, or raise the declared transient error."""
        self.attempts += 1
        if self.attempts <= self.failures:
            raise TransientLookupError(
                f"lookup unavailable ({self.attempts})",
            )
        return "status: available"


def make_tool(lookup: LocalLookup, retry: bool) -> FunctionTool:
    """Attach the policy to a read-only local function when requested."""
    return FunctionTool(
        lookup.lookup,
        is_read_only=True,
        middlewares=[ReadOnlyRetryMiddleware()] if retry else [],
        permission=PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="This example only reads a fixed local fixture.",
        ),
    )


async def run_scenario(
    scenario: Literal["baseline", "recovered", "exhausted"],
) -> tuple[int, ToolResponse]:
    """Dispatch one call and return its attempts and accumulated result."""
    lookup = LocalLookup(failures=3 if scenario == "exhausted" else 2)
    tool = make_tool(lookup, retry=scenario != "baseline")
    async for result in Toolkit(tools=[tool]).call_tool(
        ToolCallBlock(id="lookup-1", name=tool.name, input="{}"),
        AgentState(),
    ):
        if isinstance(result, ToolResponse):
            return lookup.attempts, result
    raise RuntimeError("Toolkit did not produce a final response")


async def main() -> None:
    """Print baseline failure, recovery, and retry exhaustion."""
    scenarios: list[Literal["baseline", "recovered", "exhausted"]] = [
        "baseline",
        "recovered",
        "exhausted",
    ]
    for scenario in scenarios:
        attempts, response = await run_scenario(scenario)
        output = "".join(block.text for block in response.content)
        print(
            f"{scenario}: attempts={attempts}, "
            f"state={response.state.value}, output={output}",
        )


if __name__ == "__main__":
    asyncio.run(main())
