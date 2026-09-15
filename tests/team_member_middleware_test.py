# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for :class:`TeamMemberLoopMiddleware`.

The middleware is exercised against a real :class:`Agent` driven by a
mock model and a stand-in ``TeamSay`` tool, so the reasoning-acting
loop is genuine while nothing touches the app service layer.
"""
import itertools
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.app.middleware import TeamMemberLoopMiddleware
from agentscope.event import ReplyEndEvent
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.message import UserMsg
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit
from agentscope.types import ReplyFinishedReason


class _FakeTeamSay(ToolBase):
    """Stand-in for the real ``TeamSay`` — records calls, never fails."""

    name: str = "TeamSay"
    description: str = "Report to the team leader."
    input_schema: dict = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["content"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, state: ToolResultState = ToolResultState.SUCCESS):
        super().__init__()
        self.calls: list[dict] = []
        self._state = state

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def __call__(self, content: str, to: str | None = None) -> ToolChunk:
        """Record the call and answer with the configured state."""
        self.calls.append({"to": to, "content": content})
        return ToolChunk(
            content=[TextBlock(text="delivered")],
            state=self._state,
        )


class _OtherTool(_FakeTeamSay):
    """A non-``TeamSay`` tool, used to invalidate an earlier report."""

    name: str = "Note"
    description: str = "Write a note."

    async def __call__(self, content: str, to: str | None = None) -> ToolChunk:
        """Answer successfully without reporting anywhere."""
        self.calls.append({"to": to, "content": content})
        return ToolChunk(content=[TextBlock(text="noted")])


def _text(text: str) -> ChatResponse:
    """A plain text reply — ends the reasoning-acting loop."""
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


_CALL_SEQ = itertools.count()


def _call(tool: str, **kwargs: Any) -> ChatResponse:
    """A single tool call reply; ``input`` is the raw JSON string."""
    return ChatResponse(
        content=[
            ToolCallBlock(
                type="tool_call",
                id=f"call-{tool}-{next(_CALL_SEQ)}",
                name=tool,
                input=json.dumps(kwargs),
            ),
        ],
        is_last=True,
    )


class TeamMemberLoopMiddlewareTest(IsolatedAsyncioTestCase):
    """The nudge / release / give-up decisions of the middleware."""

    async def asyncSetUp(self) -> None:
        """Build a worker agent wired to the middleware."""
        self.model = MockModel()
        self.team_say = _FakeTeamSay()
        self.extra_tools: list = []

    async def _run(
        self,
        responses: list,
        *,
        max_iters: int = 5,
        max_nudges: int = 3,
        extra_tools: list | None = None,
    ) -> list:
        """Drive one reply and return the streamed items."""
        toolkit = Toolkit(tools=[self.team_say, *(extra_tools or [])])
        self.model.set_responses(responses)
        agent = Agent(
            name="worker",
            system_prompt="You are a worker.",
            model=self.model,
            toolkit=toolkit,
            react_config=ReActConfig(max_iters=max_iters),
            middlewares=[
                TeamMemberLoopMiddleware(
                    leader_name="Leader",
                    max_nudges=max_nudges,
                ),
            ],
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        items = []
        async for item in agent.reply_stream(UserMsg("user", "do it")):
            items.append(item)
        return items

    @staticmethod
    def _end_events(items: list) -> list[ReplyEndEvent]:
        """The reply-end events that escaped the middleware."""
        return [_ for _ in items if isinstance(_, ReplyEndEvent)]

    async def test_successful_report_passes_through(self) -> None:
        """A final successful ``TeamSay`` to the leader ends the reply."""
        items = await self._run(
            [
                _call("TeamSay", to="Leader", content="done"),
                _text("reported"),
            ],
        )
        ends = self._end_events(items)
        self.assertDictEqual(
            {
                "model_calls": self.model.cnt,
                "reasons": [_.finished_reason for _ in ends],
                "team_say_calls": self.team_say.calls,
            },
            {
                "model_calls": 2,
                "reasons": [ReplyFinishedReason.COMPLETED],
                "team_say_calls": [{"to": "Leader", "content": "done"}],
            },
        )

    async def test_broadcast_report_passes_through(self) -> None:
        """``TeamSay`` without ``to`` broadcasts and still counts."""
        items = await self._run(
            [_call("TeamSay", content="done"), _text("reported")],
        )
        self.assertListEqual(
            [_.finished_reason for _ in self._end_events(items)],
            [ReplyFinishedReason.COMPLETED],
        )

    async def test_report_to_other_member_is_not_accepted(self) -> None:
        """A report addressed to another worker does not end the reply."""
        items = await self._run(
            [
                _call("TeamSay", to="OtherWorker", content="fyi"),
                _text("done"),
                _call("TeamSay", to="Leader", content="done"),
                _text("reported"),
            ],
        )
        self.assertListEqual(
            [_.finished_reason for _ in self._end_events(items)],
            [ReplyFinishedReason.COMPLETED],
        )
        self.assertListEqual(
            self.team_say.calls,
            [
                {"to": "OtherWorker", "content": "fyi"},
                {"to": "Leader", "content": "done"},
            ],
        )

    async def test_failed_report_is_not_accepted(self) -> None:
        """A ``TeamSay`` that errored does not count as a report."""
        self.team_say = _FakeTeamSay(state=ToolResultState.ERROR)
        items = await self._run(
            [
                _call("TeamSay", to="Leader", content="done"),
                _text("done"),
                _text("still not reporting"),
                _text("nor now"),
                _text("nor now either"),
            ],
            max_nudges=2,
        )
        ends = self._end_events(items)
        self.assertListEqual(
            [_.finished_reason for _ in ends],
            [ReplyFinishedReason.ERROR],
        )

    async def test_later_tool_invalidates_earlier_report(self) -> None:
        """A non-``TeamSay`` call after the report re-opens the reply."""
        note = _OtherTool()
        items = await self._run(
            [
                _call("TeamSay", to="Leader", content="done"),
                _call("Note", content="afterthought"),
                _text("finished"),
                _call("TeamSay", to="Leader", content="really done"),
                _text("reported"),
            ],
            extra_tools=[note],
        )
        self.assertListEqual(
            [_.finished_reason for _ in self._end_events(items)],
            [ReplyFinishedReason.COMPLETED],
        )
        self.assertEqual(len(self.team_say.calls), 2)

    async def test_silent_worker_is_failed_after_max_nudges(self) -> None:
        """A worker that never reports fails instead of looping."""
        items = await self._run(
            [_text(f"turn {i}") for i in range(10)],
            max_nudges=2,
        )
        ends = self._end_events(items)
        self.assertDictEqual(
            {
                "reasons": [_.finished_reason for _ in ends],
                "error_type": ends[-1].error.type if ends[-1].error else None,
                "team_say_calls": self.team_say.calls,
                # 1 initial reply + 2 nudged retries, then give up
                "model_calls": self.model.cnt,
            },
            {
                "reasons": [ReplyFinishedReason.ERROR],
                "error_type": "internal",
                "team_say_calls": [],
                "model_calls": 3,
            },
        )

    async def test_exceed_max_iters_is_nudged_then_failed(self) -> None:
        """The EXCEED ending is nudged too, and also bounded."""
        note = _OtherTool()
        items = await self._run(
            [_call("Note", content=f"n{i}") for i in range(10)],
            max_iters=2,
            max_nudges=1,
            extra_tools=[note],
        )
        self.assertListEqual(
            [_.finished_reason for _ in self._end_events(items)],
            [ReplyFinishedReason.ERROR],
        )

    async def test_completed_on_the_last_iteration_does_not_raise(
        self,
    ) -> None:
        """A COMPLETED ending exactly at ``max_iters`` must not trip the
        agent's swallow-without-progress guard.

        Without freeing an iteration the next decision would be
        EXCEED_MAX_ITERS with no reasoning in between, and swallowing
        that raises inside the agent.
        """
        items = await self._run(
            [
                _text("done early"),
                _call("TeamSay", to="Leader", content="done"),
                _text("reported"),
            ],
            max_iters=1,
        )
        self.assertDictEqual(
            {
                "reasons": [
                    _.finished_reason for _ in self._end_events(items)
                ],
                "team_say_calls": self.team_say.calls,
            },
            {
                # The nudged round spends the freed iteration, so the
                # released ending is the EXCEED one.
                "reasons": [ReplyFinishedReason.EXCEED_MAX_ITERS],
                "team_say_calls": [{"to": "Leader", "content": "done"}],
            },
        )
