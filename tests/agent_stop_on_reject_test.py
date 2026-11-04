# -*- coding: utf-8 -*-
"""Tests for ``ReActConfig.stop_on_reject``:

- A tool call denied by the permission check ends the reply with
  ``ReplyFinishedReason.TOOL_REJECTED`` without a second reasoning round.
- The denial feeds back for another reasoning round by default
  (``stop_on_reject=False``).
- Tool calls in the batches after the denied one are skipped and closed
  with a ``denied`` tool result.
"""
from typing import Any

from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.event import ReplyEndEvent
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit
from agentscope.types import ReplyFinishedReason


def _input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }


class _DeniedTool(ToolBase):
    """A sequential tool whose permission check always denies the call."""

    name: str = "denied_tool"
    description: str = "A tool that is always denied"
    input_schema: dict[str, Any] = _input_schema()
    is_concurrency_safe: bool = False
    # Not read-only: the read-only fast path of the permission engine
    # would allow the call before ``check_permissions`` is consulted
    is_read_only: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Deny every call to this tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            decision_reason="The test rule denies this tool",
            message="The tool call is denied by a test rule.",
        )

    # pylint: disable=redefined-builtin
    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Never reachable: a denied tool call must not execute."""
        raise AssertionError("A denied tool call must never execute")


class _NeverRunsTool(ToolBase):
    """A concurrent tool that records whether it actually ran."""

    name: str = "never_runs_tool"
    description: str = "A tool that must be skipped by stop_on_reject"
    input_schema: dict[str, Any] = _input_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    executed: list[str] = []

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow every call to this tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="The test tool always allows",
            message="The test tool always allows",
        )

    # pylint: disable=redefined-builtin
    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Record the execution and finish."""
        type(self).executed.append(input)
        return ToolChunk(content=[TextBlock(text=f"Ran: {input}")])


def _tool_call_response(*tool_calls: ToolCallBlock) -> ChatResponse:
    """A model response that only contains tool calls."""
    return ChatResponse(content=list(tool_calls), is_last=True)


class StopOnRejectTest(IsolatedAsyncioTestCase):
    """The ``stop_on_reject`` behavior of the reasoning-acting loop."""

    def _build_agent(
        self,
        model: MockModel,
        tools: list[ToolBase],
        stop_on_reject: bool,
    ) -> Agent:
        """Build an agent with the runtime state injection off, so the
        assertions stay focused."""
        return Agent(
            name="Friday",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(tools=tools),
            injection_config=InjectionConfig(inject_runtime_state=False),
            react_config=ReActConfig(stop_on_reject=stop_on_reject),
        )

    async def test_denied_call_stops_the_reply(self) -> None:
        """A denied tool call ends the reply without another round."""
        model = MockModel(
            mock_chat_responses=[
                _tool_call_response(
                    ToolCallBlock(
                        id="tool_call_1",
                        name="denied_tool",
                        input='{"input": "x"}',
                    ),
                ),
                # Never consumed: the reply must stop after the denial
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        agent = self._build_agent(
            model,
            [_DeniedTool()],
            stop_on_reject=True,
        )

        events: list[Any] = []
        async for evt in agent.reply_stream(
            UserMsg(name="user", content="Go"),
            yield_final_msg=True,
        ):
            events.append(evt)

        end_events = [_ for _ in events if isinstance(_, ReplyEndEvent)]
        self.assertEqual(len(end_events), 1)
        self.assertEqual(
            end_events[0].finished_reason,
            ReplyFinishedReason.TOOL_REJECTED,
        )

        # The fallback msg terminates the stream
        self.assertEqual(events[-1].role, "assistant")
        self.assertEqual(
            events[-1].finished_reason,
            ReplyFinishedReason.TOOL_REJECTED,
        )
        self.assertIn("denied_tool", events[-1].content[0].text)

        # No second reasoning round on the denial
        self.assertEqual(model.cnt, 1)

    async def test_denial_feeds_back_by_default(self) -> None:
        """Without stop_on_reject, the denial feeds back and the model
        finishes the reply."""
        model = MockModel(
            mock_chat_responses=[
                _tool_call_response(
                    ToolCallBlock(
                        id="tool_call_1",
                        name="denied_tool",
                        input='{"input": "x"}',
                    ),
                ),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        agent = self._build_agent(
            model,
            [_DeniedTool()],
            stop_on_reject=False,
        )

        msg = await agent.reply(UserMsg(name="user", content="Go"))

        self.assertEqual(msg.finished_reason, ReplyFinishedReason.COMPLETED)
        self.assertEqual(
            [block.text for block in msg.get_content_blocks("text")],
            ["done"],
        )
        # The denial was fed back for a second reasoning round
        self.assertEqual(model.cnt, 2)

    async def test_stopped_reply_skips_later_batches(self) -> None:
        """The batches after the denied one are skipped and closed with a
        ``denied`` tool result."""
        _NeverRunsTool.executed = []
        model = MockModel(
            mock_chat_responses=[
                # A sequential denied call followed by a concurrent call,
                # which lands in a second batch that must never start
                _tool_call_response(
                    ToolCallBlock(
                        id="tool_call_1",
                        name="denied_tool",
                        input='{"input": "x"}',
                    ),
                    ToolCallBlock(
                        id="tool_call_2",
                        name="never_runs_tool",
                        input='{"input": "y"}',
                    ),
                ),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        agent = self._build_agent(
            model,
            [_DeniedTool(), _NeverRunsTool()],
            stop_on_reject=True,
        )

        events: list[Any] = []
        async for evt in agent.reply_stream(
            UserMsg(name="user", content="Go"),
            yield_final_msg=True,
        ):
            events.append(evt)

        end_events = [_ for _ in events if isinstance(_, ReplyEndEvent)]
        self.assertEqual(
            end_events[0].finished_reason,
            ReplyFinishedReason.TOOL_REJECTED,
        )

        # The concurrent tool in the second batch never executed
        self.assertEqual(_NeverRunsTool.executed, [])

        # ... and its call was closed with a denied tool result
        results = {
            _.name: _
            for _ in agent.state.context[-1].get_content_blocks(
                "tool_result",
            )
        }
        self.assertEqual(
            set(results),
            {"denied_tool", "never_runs_tool"},
        )
        self.assertIn("denied by a test rule", results["denied_tool"].output)
        self.assertIn("skipped", results["never_runs_tool"].output)
