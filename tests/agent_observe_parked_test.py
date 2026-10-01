# -*- coding: utf-8 -*-
"""Regression tests for observations arriving during a parked reply."""

from unittest.async_case import IsolatedAsyncioTestCase
from typing import Any

from utils import MockModel
from agent_interrupt_test import (
    _ExternalConcurrentTool,
    _UserConfirmConcurrentTool,
)

from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.state import AgentState
from agentscope.tool import Toolkit


class _CountingContext(list):
    """Count indexed reads to verify lookup cost without timing noise."""

    reads = 0

    def __getitem__(self, index: Any) -> Any:
        self.reads += 1
        return super().__getitem__(index)


class AgentObserveParkedTest(IsolatedAsyncioTestCase):
    """Observations must not detach the active reply from its tools."""

    def test_reply_cache_lookup_cost(self) -> None:
        """New replies and cached replies do not scan historical messages."""
        state = AgentState(reply_id="current")
        context = _CountingContext(
            UserMsg(name="user", content="history") for _ in range(10000)
        )
        state.context = context
        state.prepare_reply_msg("A")
        for _ in range(5):
            self.assertIsNone(state.get_reply_msg("A"))
        self.assertEqual(context.reads, 0)
        state.append_context("A", [TextBlock(text="reply")])
        reply = state.get_reply_msg("A")
        context.extend(
            AssistantMsg(name="B", content="observe") for _ in range(1000)
        )
        context.reads = 0
        for _ in range(5):
            self.assertIs(state.get_reply_msg("A"), reply)
        self.assertEqual(context.reads, 5)

    def test_reply_cache_invalidation(self) -> None:
        """Replacement, edits, new IDs and restoration refresh the cache."""
        state = AgentState(reply_id="current")
        state.append_context("A", [TextBlock(text="reply")])
        original = state.get_reply_msg("A")
        replacement = original.model_copy(deep=True)
        state.context = [replacement]
        self.assertIs(state.get_reply_msg("A"), replacement)
        state.context.insert(0, UserMsg(name="user", content="prepend"))
        self.assertIs(state.get_reply_msg("A"), replacement)
        state.context[1] = original
        self.assertIs(state.get_reply_msg("A"), original)
        restored = AgentState.model_validate_json(state.model_dump_json())
        self.assertIs(restored.get_reply_msg("A"), restored.context[1])
        self.assertNotIn("_reply_msg_cache", state.model_dump())
        state.context.clear()
        self.assertIsNone(state.get_reply_msg("A"))
        state.reply_id = "next"
        state.append_context("A", [TextBlock(text="next")])
        self.assertEqual(state.get_reply_msg("A").id, "next")

    async def test_resume_after_observation(self) -> None:
        """Approval, denial, external results and interrupts keep identity."""
        for mode in ("allow", "deny", "external", "interrupt", "partial"):
            with self.subTest(mode=mode):
                tool = (
                    _ExternalConcurrentTool()
                    if mode == "external"
                    else _UserConfirmConcurrentTool()
                )
                calls = [
                    ToolCallBlock(
                        id="c1",
                        name=tool.name,
                        input='{"timeout": 0}',
                    ),
                ]
                if mode == "partial":
                    calls.append(
                        ToolCallBlock(
                            id="c2",
                            name=tool.name,
                            input='{"timeout": 0}',
                        ),
                    )
                model = MockModel(context_size=100000)
                model.set_responses(
                    [
                        ChatResponse(content=calls, is_last=True),
                        ChatResponse(
                            content=[TextBlock(text="done")],
                            is_last=True,
                        ),
                    ],
                )
                agent = Agent(
                    name="A",
                    system_prompt="test",
                    model=model,
                    toolkit=Toolkit(tools=[tool]),
                )
                await agent.reply(UserMsg(name="user", content="run"))
                reply_id = agent.state.reply_id
                original = agent.state.context[-1]
                observed = AssistantMsg(name="B", content="broadcast")
                await agent.observe(observed)
                self.assertEqual(
                    [c.id for c in agent.state.get_awaiting_tool_calls("A")],
                    ["c1"],
                )
                with self.assertRaises(ValueError):
                    await agent.reply(UserMsg(name="user", content="new"))
                if mode == "external":
                    event = ExternalExecutionResultEvent(
                        reply_id=reply_id,
                        execution_results=[
                            ToolResultBlock(
                                id="c1",
                                name=tool.name,
                                output="external done",
                            ),
                        ],
                    )
                elif mode == "interrupt":
                    event = UserInterruptEvent(reply_id=reply_id)
                else:
                    event = UserConfirmResultEvent(
                        reply_id=reply_id,
                        confirm_results=[
                            ConfirmResult(
                                confirmed=mode != "deny",
                                tool_call=calls[0],
                            ),
                        ],
                    )
                await agent.reply(event)
                if mode == "partial":
                    self.assertEqual(
                        [
                            c.id
                            for c in agent.state.get_awaiting_tool_calls("A")
                        ],
                        ["c2"],
                    )
                    await agent.observe(UserMsg(name="user", content="note"))
                    await agent.reply(
                        UserConfirmResultEvent(
                            reply_id=reply_id,
                            confirm_results=[
                                ConfirmResult(
                                    confirmed=True,
                                    tool_call=calls[1],
                                ),
                            ],
                        ),
                    )
                self.assertEqual(agent.state.reply_id, reply_id)
                self.assertIn(observed, agent.state.context)
                self.assertEqual(
                    [m for m in agent.state.context if m.id == reply_id],
                    [original],
                )
                self.assertEqual(
                    [b.id for b in original.get_content_blocks("tool_result")],
                    [c.id for c in calls],
                )
                self.assertFalse(agent.state.get_unfinished_tool_calls("A"))
                self.assertTrue(all(c.state == "finished" for c in calls))
                formatted = await model.formatter.format(agent.state.context)
                call_ids = [
                    call["id"]
                    for msg in formatted
                    for call in msg.get("tool_calls", [])
                ]
                result_ids = [
                    msg["tool_call_id"]
                    for msg in formatted
                    if msg["role"] == "tool"
                ]
                self.assertEqual(call_ids, result_ids)
                model.set_responses(
                    [
                        ChatResponse(
                            content=[TextBlock(text="next")],
                            is_last=True,
                        ),
                    ],
                )
                await agent.reply(UserMsg(name="user", content="next"))
                self.assertNotEqual(agent.state.reply_id, reply_id)
                self.assertFalse(agent.state.get_awaiting_tool_calls("A"))
