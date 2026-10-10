# -*- coding: utf-8 -*-
"""Unittests for :class:`SOPStepSubmitMiddleware`.

Driven against a real :class:`Agent` with a mock model and a stand-in
submit tool, so the reasoning-acting loop is genuine while nothing
touches the app service layer.
"""
import itertools
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.app.middleware import SOPStepSubmitMiddleware
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


class _FakeSubmit(ToolBase):
    """Stand-in for ``SubmitHandover`` — records calls, never writes."""

    name: str = "SubmitHandover"
    description: str = "Hand over the result of this step."
    input_schema: dict = {
        "type": "object",
        "properties": {"handover": {"type": "string"}},
        "required": ["handover"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, state: ToolResultState = ToolResultState.SUCCESS):
        """Answer with the given state every time."""
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

    async def __call__(self, handover: str) -> ToolChunk:
        """Record the call and answer with the configured state."""
        self.calls.append({"handover": handover})
        return ToolChunk(
            content=[TextBlock(text="handed over")],
            state=self._state,
        )


class _OtherTool(_FakeSubmit):
    """A non-submit tool, used to invalidate an earlier submission."""

    name: str = "Note"
    description: str = "Write a note."

    async def __call__(self, handover: str) -> ToolChunk:
        """Answer successfully without submitting anything."""
        self.calls.append({"handover": handover})
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


class SOPStepSubmitMiddlewareTest(IsolatedAsyncioTestCase):
    """The nudge / release / give-up decisions of the middleware."""

    async def asyncSetUp(self) -> None:
        """Build a step agent wired to the middleware."""
        self.model = MockModel()
        self.submit = _FakeSubmit()

    async def _run(
        self,
        responses: list,
        *,
        max_iters: int = 5,
        max_nudges: int = 3,
        extra_tools: list | None = None,
        submit: ToolBase | None = None,
    ) -> list:
        """Drive one reply and return the streamed items."""
        toolkit = Toolkit(
            tools=[submit or self.submit, *(extra_tools or [])],
        )
        self.model.set_responses(responses)
        agent = Agent(
            name="modeller",
            system_prompt="You make hulls.",
            model=self.model,
            toolkit=toolkit,
            react_config=ReActConfig(max_iters=max_iters),
            middlewares=[SOPStepSubmitMiddleware(max_nudges=max_nudges)],
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        return [_ async for _ in agent.reply_stream(UserMsg("sop", "do it"))]

    @staticmethod
    def _end_events(items: list) -> list[ReplyEndEvent]:
        """The reply-end events that escaped the middleware."""
        return [_ for _ in items if isinstance(_, ReplyEndEvent)]

    async def test_a_successful_submission_ends_the_reply(self) -> None:
        """Nothing is swallowed once the step has something to show."""
        items = await self._run(
            [
                _call("SubmitHandover", handover="a hull"),
                _text("handed over"),
            ],
        )

        self.assertDictEqual(
            {
                "model_calls": self.model.cnt,
                "reasons": [
                    _.finished_reason for _ in self._end_events(items)
                ],
                "submissions": self.submit.calls,
            },
            {
                "model_calls": 2,
                "reasons": [ReplyFinishedReason.COMPLETED],
                "submissions": [{"handover": "a hull"}],
            },
        )

    async def test_a_reply_that_submits_nothing_is_sent_back(self) -> None:
        """Then accepted as soon as it submits."""
        items = await self._run(
            [
                _text("all done!"),
                _call("SubmitHandover", handover="a hull"),
                _text("handed over"),
            ],
        )

        self.assertDictEqual(
            {
                "reasons": [
                    _.finished_reason for _ in self._end_events(items)
                ],
                "submissions": self.submit.calls,
            },
            {
                "reasons": [ReplyFinishedReason.COMPLETED],
                "submissions": [{"handover": "a hull"}],
            },
        )

    async def test_the_nudges_run_out(self) -> None:
        """An attempt that never submits fails rather than looping."""
        items = await self._run([_text("all done!")] * 6, max_nudges=2)

        ends = self._end_events(items)
        self.assertListEqual(
            [_.finished_reason for _ in ends],
            [ReplyFinishedReason.ERROR],
        )
        self.assertIn("without submitting", ends[0].error.message)
        self.assertListEqual(self.submit.calls, [])

    async def test_a_later_tool_call_undoes_the_submission(self) -> None:
        """Whatever the reply did last is what its turn amounts to."""
        items = await self._run(
            [
                _call("SubmitHandover", handover="a hull"),
                _call("Note", handover="and one more thing"),
                _text("all done!"),
            ],
            max_nudges=0,
            extra_tools=[_OtherTool()],
        )

        self.assertListEqual(
            [_.finished_reason for _ in self._end_events(items)],
            [ReplyFinishedReason.ERROR],
        )

    async def test_a_submission_that_failed_does_not_count(self) -> None:
        """The tool answered, but the step has nothing filed for it."""
        items = await self._run(
            [_call("SubmitHandover", handover="a hull"), _text("done")],
            max_nudges=0,
            submit=_FakeSubmit(state=ToolResultState.ERROR),
        )

        self.assertListEqual(
            [_.finished_reason for _ in self._end_events(items)],
            [ReplyFinishedReason.ERROR],
        )
