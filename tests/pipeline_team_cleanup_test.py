# -*- coding: utf-8 -*-
"""Regression coverage for failed concurrent team assignment rounds."""
import asyncio
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from pipeline_team_test import SlowTool, _agent, _text, _tool_call
from utils import MockModel

from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.pipeline import TeamMember, TeamPipeline
from agentscope.tool import ToolChunk


class CleanupTool(SlowTool):
    """Track cancellation and asynchronous cleanup without external effects."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.stopped = asyncio.Event()

    async def call(self) -> ToolChunk:
        """Block until released, and record when cleanup has completed."""
        try:
            self.started.set()
            await self.release.wait()
            return ToolChunk(content=[TextBlock(text="finished")])
        finally:
            await asyncio.sleep(0)
            self.stopped.set()


class FailingMemberModel(MockModel):
    """Fail only after all sibling tools have started."""

    def __init__(self, tools: list[CleanupTool], error: Exception) -> None:
        super().__init__()
        self.tools = tools
        self.error = error

    async def _call_api(self, *args: Any, **kwargs: Any) -> ChatResponse:
        await asyncio.gather(*(tool.started.wait() for tool in self.tools))
        raise self.error


class TeamCleanupTest(IsolatedAsyncioTestCase):
    """A failing member must not leave sibling tool executions alive."""

    async def test_model_failure_joins_all_siblings(self) -> None:
        """Preserve the original failure after every sibling has cleaned up.

        Based on the deterministic reproduction in issue #2972.
        """
        for failing_first in (True, False):
            with self.subTest(failing_first=failing_first):
                tools = [CleanupTool(), CleanupTool()]
                workers = [
                    _agent(f"worker{i}", [t]) for i, t in enumerate(tools)
                ]
                for worker in workers:
                    worker.model.set_responses(
                        [_tool_call("slow", "{}", "slow_tool"), _text("done")],
                    )
                failure = RuntimeError("member model failed")
                failing = _agent("failing")
                failing.model = FailingMemberModel(tools, failure)
                members = (
                    [failing, *workers]
                    if failing_first
                    else [*workers, failing]
                )
                leader = _agent("leader")
                leader.model.set_responses(
                    [
                        [
                            ChatResponse(
                                content=[
                                    ToolCallBlock(
                                        id=member.name,
                                        name="TeamAssign",
                                        input='{"member":"'
                                        + member.name
                                        + '","prompt":"work"}',
                                    )
                                    for member in members
                                ],
                                is_last=True,
                                usage=None,
                            ),
                        ],
                    ],
                )
                pipeline = TeamPipeline(
                    leader,
                    [TeamMember(agent=m, description=m.name) for m in members],
                )

                async def consume(team: TeamPipeline) -> None:
                    """Consume the full reply so member failures propagate."""
                    async for _ in team.reply_stream(
                        UserMsg(name="user", content="go"),
                    ):
                        pass

                try:
                    with self.assertRaises(RuntimeError) as caught:
                        await asyncio.wait_for(consume(pipeline), timeout=5)
                    self.assertIs(caught.exception, failure)
                    self.assertTrue(
                        all(tool.stopped.is_set() for tool in tools),
                    )
                finally:
                    # Also let the unfixed baseline exit without orphan tasks.
                    for tool in tools:
                        tool.release.set()
                    await asyncio.wait_for(
                        asyncio.gather(
                            *(tool.stopped.wait() for tool in tools),
                        ),
                        timeout=5,
                    )
