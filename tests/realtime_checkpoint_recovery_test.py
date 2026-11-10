# -*- coding: utf-8 -*-
"""Recover parked Live delegations through a newly constructed Chat Agent."""
# pylint: disable=protected-access

import asyncio
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from utils import MockModel

from agentscope.agent import Agent, RealtimeAgent
from agentscope.credential import OpenAICredential
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import ChatResponse, ChatUsage
from agentscope.realtime import OpenAILiveModel, TimelineTranscriptDeltaEvent
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit


class RealtimeCheckpointRecoveryTest(IsolatedAsyncioTestCase):
    """Exercise serialized checkpoints rather than the in-memory backend."""

    async def _checkpoint(
        self,
        external: bool,
        voice_role: str | None,
    ) -> tuple[AgentState, ToolCallBlock, Toolkit, list[str], dict]:
        """Park a real delegate, optionally append speech, then persist."""
        executed: list[str] = []
        backends: list[Agent] = []

        def write() -> str:
            """Record execution of the approved tool."""
            executed.append("write")
            return "written"

        tool = FunctionTool(write, is_read_only=external)
        tool.is_external_tool = external
        toolkit = Toolkit(tools=[tool])

        def factory(state: AgentState) -> Agent:
            """Create a deterministic backend that requests one tool."""
            model = MockModel(context_size=10000)
            model.set_responses(
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="write",
                                name="write",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            )
            backend = Agent(
                "Friday",
                "Be brief.",
                model,
                state=state,
                toolkit=toolkit,
            )
            backends.append(backend)
            return backend

        live_model = OpenAILiveModel(
            "gpt-live-1",
            OpenAICredential(api_key="test"),
        )
        live_model.send_delegation_result = AsyncMock()
        live = RealtimeAgent(
            "Friday",
            "Be brief.",
            live_model,
            delegation_agent_factory=factory,
        )
        self.addAsyncCleanup(live.close)
        live._start_client_delegation("task")
        while live._tasks:
            await asyncio.gather(*list(live._tasks))
        if voice_role is not None:
            await live._on_model_event(
                TimelineTranscriptDeltaEvent(
                    role=voice_role,
                    delta="Later speech",
                    start_ms=0,
                    end_ms=100,
                ),
            )
        snapshot = None
        call = None
        for event in live.drain_events():
            checkpoint = live.checkpoint_snapshot(event)
            if checkpoint is not None:
                snapshot = checkpoint
            if isinstance(
                event,
                (
                    RequireUserConfirmEvent,
                    RequireExternalExecutionEvent,
                ),
            ):
                call = event.tool_calls[0].model_copy(deep=True)
        assert snapshot is not None and call is not None
        reply_context = backends[0].state.reply_context.model_dump(mode="json")
        restored = AgentState.model_validate_json(snapshot.model_dump_json())
        await live.close()
        return restored, call, toolkit, executed, reply_context

    async def test_checkpoint_keeps_reply_context(self) -> None:
        """The persisted reply metadata must match the parked backend."""
        state, _, _, _, expected = await self._checkpoint(False, None)
        self.assertEqual(state.reply_context.model_dump(mode="json"), expected)

    async def test_chat_recovers_parked_tools_after_speech(self) -> None:
        """Resume each input kind on its original reply, preserving speech."""
        for action in (
            "accept",
            "deny",
            "external",
            "interrupt",
            "interrupt_external",
        ):
            for voice_role in (None, "user", "assistant"):
                with self.subTest(action=action, voice_role=voice_role):
                    (
                        state,
                        call,
                        toolkit,
                        executed,
                        reply_context,
                    ) = await self._checkpoint(
                        action in {"external", "interrupt_external"},
                        voice_role,
                    )
                    reply_id = reply_context["reply_id"]
                    original_ids = [msg.id for msg in state.context]
                    speech = [
                        msg.model_dump()
                        for msg in state.context
                        if msg.id != reply_id
                    ]
                    self.assertTrue(state.has_awaiting_tool_calls("Friday"))
                    if action.startswith("interrupt"):
                        event = UserInterruptEvent(reply_id=reply_id)
                        expected_result = ToolResultState.INTERRUPTED
                    elif action == "external":
                        event = ExternalExecutionResultEvent(
                            reply_id=reply_id,
                            execution_results=[
                                ToolResultBlock(
                                    id=call.id,
                                    name=call.name,
                                    output="written",
                                    state=ToolResultState.SUCCESS,
                                ),
                            ],
                        )
                        expected_result = ToolResultState.SUCCESS
                    else:
                        event = UserConfirmResultEvent(
                            reply_id=reply_id,
                            confirm_results=[
                                ConfirmResult(
                                    confirmed=action == "accept",
                                    tool_call=call,
                                ),
                            ],
                        )
                        expected_result = (
                            ToolResultState.SUCCESS
                            if action == "accept"
                            else ToolResultState.DENIED
                        )
                    model = MockModel(context_size=10000)
                    model.set_responses(
                        [
                            ChatResponse(
                                content=[TextBlock(text="Done")],
                                is_last=True,
                                usage=ChatUsage(
                                    input_tokens=10,
                                    output_tokens=2,
                                    time=0.1,
                                ),
                            ),
                        ],
                    )
                    agent = Agent(
                        "Friday",
                        "Be brief.",
                        model,
                        state=state,
                        toolkit=toolkit,
                    )
                    events = [item async for item in agent.reply_stream(event)]
                    reply = next(
                        msg for msg in state.context if msg.id == reply_id
                    )
                    self.assertEqual(
                        {
                            "ids": [msg.id for msg in state.context],
                            "speech": [
                                msg.model_dump()
                                for msg in state.context
                                if msg.id != reply_id
                            ],
                            "calls": [
                                (block.id, block.state)
                                for block in reply.get_content_blocks(
                                    "tool_call",
                                )
                            ],
                            "results": [
                                (block.id, block.state)
                                for block in reply.get_content_blocks(
                                    "tool_result",
                                )
                            ],
                            "event_reply_ids": {
                                item.reply_id
                                for item in events
                                if item.reply_id
                            },
                            "pending": state.get_awaiting_tool_calls("Friday"),
                            "executed": executed,
                            "usage": (
                                reply.usage.input_tokens if reply.usage else 0
                            ),
                        },
                        {
                            "ids": original_ids,
                            "speech": speech,
                            "calls": [(call.id, "finished")],
                            "results": [(call.id, expected_result)],
                            "event_reply_ids": {reply_id},
                            "pending": [],
                            "executed": ["write"]
                            if action == "accept"
                            else [],
                            "usage": 0
                            if action.startswith("interrupt")
                            else 10,
                        },
                    )


class ReplyMessageLookupTest(TestCase):
    """Match both reply ID and author without reviving historical calls."""

    def test_current_reply_owns_its_blocks(self) -> None:
        """Append to the matching reply and ignore unrelated tool calls."""
        current = AssistantMsg("Friday", [], id="current")
        history = AssistantMsg(
            "Friday",
            [
                ToolCallBlock(
                    id="stale",
                    name="write",
                    input="{}",
                    state="asking",
                ),
            ],
            id="history",
        )
        other = AssistantMsg("Other", [], id="current")
        speech = UserMsg("user", "Later speech")
        state = AgentState(context=[history, current, other, speech])
        state.reply_id = "current"
        block = TextBlock(text="Done")
        state.append_context("Friday", [block])
        self.assertEqual(
            {
                "context": [msg.model_dump() for msg in state.context],
                "pending": state.get_awaiting_tool_calls("Friday"),
            },
            {
                "context": [
                    history.model_dump(),
                    {**current.model_dump(), "content": [block.model_dump()]},
                    other.model_dump(),
                    speech.model_dump(),
                ],
                "pending": [],
            },
        )
