# -*- coding: utf-8 -*-
"""Recover parked Live delegations through a newly constructed Chat Agent."""
# pylint: disable=protected-access

import asyncio
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from utils import MockModel

from agentscope.agent import Agent, RealtimeAgent
from agentscope.app._service._webrtc_audio_transport import (
    WebRTCAudioTransport,
)
from agentscope.app._service._webrtc_session import WebRTCSession
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
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

    async def test_hidden_tool_checkpoint_before_next_model_response(
        self,
    ) -> None:
        """Persist and restore completed work while reasoning is blocked."""
        waiting = asyncio.Event()
        persisted = asyncio.Event()
        executed: list[str] = []
        saved: list[str] = []

        def read() -> str:
            """Record one automatically allowed execution."""
            executed.append("read")
            return "contents"

        toolkit = Toolkit(tools=[FunctionTool(read, is_read_only=True)])
        model = MockModel(context_size=10000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(id="read", name="read", input="{}"),
                    ],
                    is_last=True,
                ),
            ],
        )
        call_api = model._call_api

        async def blocked_api(*args: Any, **kwargs: Any) -> Any:
            """Hold the response after tool execution until cancellation."""
            if model.cnt:
                waiting.set()
                await asyncio.Event().wait()
            return await call_api(*args, **kwargs)

        def factory(state: AgentState) -> Agent:
            """Use a real backend with a deterministic model boundary."""
            return Agent(
                "Friday",
                "Be brief.",
                model,
                state=state,
                toolkit=toolkit,
            )

        async def save_state(**kwargs: Any) -> None:
            """Serialize at the actual WebRTC persistence boundary."""
            saved.append(kwargs["state"].model_dump_json())
            persisted.set()

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
        transport = WebRTCAudioTransport(
            live_model.input_sample_rate,
            live_model.output_sample_rate,
        )
        storage = AsyncMock()
        storage.update_session_state.side_effect = save_state
        bus = InMemoryMessageBus()
        session = WebRTCSession(
            peer_connection=AsyncMock(),
            transport=transport,
            agent_factory=AsyncMock(return_value=live),
            storage=storage,
            message_bus=bus,
            user_id="user",
            agent_id="agent",
            session_id="session",
            on_closed=lambda _: None,
        )
        with (
            patch.object(model, "_call_api", side_effect=blocked_api),
            patch.object(live, "connect", AsyncMock()),
            patch.object(bus, "publish", wraps=bus.publish) as publish,
        ):
            session.start()
            try:
                self.assertTrue(await session.wait_until_lock_acquired(1))
                live._start_client_delegation("task")
                await asyncio.wait_for(waiting.wait(), timeout=1)
                await asyncio.wait_for(persisted.wait(), timeout=1)
                delegation = live._delegations["task"]
                assert delegation.agent is not None
                reply = delegation.agent.state.get_reply_message("Friday")
                assert reply is not None
                expected = reply.model_copy(deep=True)
                expected.content = [
                    block
                    for block in expected.content
                    if block.type in {"tool_call", "tool_result"}
                ]
                restored = AgentState.model_validate_json(saved[0])
                self.assertEqual(
                    {
                        "visible": delegation.visible,
                        "executed": executed,
                        "context": restored.model_dump(mode="json")["context"],
                        "unfinished": restored.get_unfinished_tool_calls(
                            "Friday",
                        ),
                        "events": [
                            (call.args[1]["name"], call.args[1]["value"])
                            for call in publish.await_args_list
                        ],
                    },
                    {
                        "visible": False,
                        "executed": ["read"],
                        "context": [expected.model_dump(mode="json")],
                        "unfinished": [],
                        "events": [
                            (
                                "delegation_checkpoint",
                                {
                                    "delegation_id": "task",
                                    "reply_id": reply.id,
                                    "tool_call_id": "read",
                                },
                            ),
                        ],
                    },
                )
                self.assertEqual(
                    live.state.model_dump(mode="json")["context"],
                    restored.model_dump(mode="json")["context"],
                )
                self.assertEqual(
                    await bus.registry_get(
                        MessageBusKeys.session_event_checkpoint("session"),
                        MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
                    ),
                    publish.await_args.args[1]["_entry_id"],
                )
                self.assertFalse(session._task.done())
                live_model.send_delegation_result.assert_not_awaited()
                recovery_model = MockModel(context_size=10000)
                recovery_model.set_responses(
                    [
                        ChatResponse(
                            content=[TextBlock(text="Done")],
                            is_last=True,
                        ),
                    ],
                )
                recovered = Agent(
                    "Friday",
                    "Be brief.",
                    recovery_model,
                    state=restored,
                    toolkit=toolkit,
                )
                await recovered.reply(UserMsg("user", "Continue."))
                self.assertEqual(executed, ["read"])
                self.assertEqual(
                    restored.context[0].model_dump(mode="json"),
                    expected.model_dump(mode="json"),
                )
            finally:
                await session.close()

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
