# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Steering interruption persists complete replies through ChatService."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from hitl_mixed_test import MockMixedSequentialTool
from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.app import _bus_ops
from agentscope.app._service import ChatService, _chat
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.middleware import _steering_middleware as _adapter
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    AssistantMsg,
    HintBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.state import ReplyContext
from agentscope.tool import Toolkit
from agentscope.types import ReplyFinishedReason


class TestSteeringPersistence(IsolatedAsyncioTestCase):
    """Exercise actual service projection, Agent cleanup and storage writes."""

    async def asyncSetUp(self) -> None:
        """Assemble a real Agent with local model, bus and storage doubles."""
        self.bus = InMemoryMessageBus()
        self.model = MockModel(context_size=100_000)
        self.model.set_responses([ChatResponse(content=[], is_last=True)])
        self.toolkit = Toolkit()
        self.messages: list = []
        self.states: list = []
        self.events: list = []
        self.input_msg: Any = UserMsg("user", "initial")
        self.agent_record = AgentRecord(
            id="agent",
            user_id="user",
            data=AgentData(name="agent"),
        )
        self.session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(
                workspace_id="workspace",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="credential",
                    model="test",
                    parameters={},
                ),
            ),
        )

        async def save_message(_user: str, _session: str, msg: Any) -> None:
            self.messages.append(msg.model_copy(deep=True))

        async def save_state(**kwargs: Any) -> None:
            self.states.append(kwargs["state"].model_copy(deep=True))

        storage = SimpleNamespace(
            get_session=AsyncMock(return_value=self.session),
            upsert_message=AsyncMock(side_effect=save_message),
            update_session_state=AsyncMock(side_effect=save_state),
        )
        self.service = ChatService(
            storage=storage,
            workspace_manager=SimpleNamespace(
                get_workspace=AsyncMock(
                    return_value=SimpleNamespace(workdir=str(Path.cwd())),
                ),
            ),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=self.bus,
            resource_access_service=SimpleNamespace(
                resolve_agent=AsyncMock(return_value=self.agent_record),
            ),
            custom_agent_cls=self._make_agent,
        )

    def _make_agent(self, **kwargs: Any) -> Agent:
        """Keep service middleware without external offloading dependencies."""
        kwargs["offloader"] = None
        kwargs["injection_config"] = InjectionConfig(
            inject_runtime_state=False,
        )
        return Agent(**kwargs)

    async def _run(self, cancel_hint_publication: bool = False) -> None:
        """Run actual service code and retain events before checkpoint trim."""
        publish = _chat.publish_session_event

        async def record_event(*args: Any, **kwargs: Any) -> Any:
            payload = args[2]
            self.events.append(payload)
            if cancel_hint_publication and payload["type"] == "HINT_BLOCK":
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            return await publish(*args, **kwargs)

        with (
            patch.object(
                _chat,
                "get_model",
                AsyncMock(return_value=self.model),
            ),
            patch.object(
                _chat,
                "get_toolkit",
                AsyncMock(return_value=self.toolkit),
            ),
            patch.object(_chat, "publish_session_event", record_event),
            patch.object(self.service, "_auto_name_session", AsyncMock()),
        ):
            await self.service._run_impl(
                "user",
                "session",
                "agent",
                self.input_msg,
            )

    def _assert_interrupted_reply(self, hints: list[HintBlock]) -> None:
        """Check the actual stored reply and its terminal event."""
        replies = [m for m in self.messages if m.role == "assistant"]
        self.assertEqual(len(replies), 1)
        reply = replies[0]
        self.assertEqual(
            reply.model_dump(
                exclude={"id", "created_at", "finished_at", "content"},
            ),
            AssistantMsg(
                "agent",
                [],
                finished_reason=ReplyFinishedReason.INTERRUPTED,
            ).model_dump(
                exclude={"id", "created_at", "finished_at", "content"},
            ),
        )
        self.assertIsNotNone(reply.finished_at)
        self.assertEqual(
            [
                b.model_dump(exclude={"created_at", "finished_at"})
                for b in reply.content
            ],
            [
                b.model_dump(exclude={"created_at", "finished_at"})
                for b in hints
            ],
        )
        self.assertEqual(
            [
                (e["reply_id"], e["finished_reason"])
                for e in self.events
                if e["type"] == "REPLY_END"
            ],
            [(reply.id, "interrupted")],
        )
        self.assertEqual(self.model.cnt, 0)

    async def test_registration_cancellation_closes_reply(self) -> None:
        """Cancellation before or after registration reaches Agent cleanup."""
        for committed in (False, True):
            with self.subTest(committed=committed):
                await self.asyncSetUp()
                await self._check_registration_cancellation(committed)

    async def _check_registration_cancellation(self, committed: bool) -> None:
        """Stop at the registration await before the Agent calls its model."""
        entered = asyncio.Event()
        original = _chat.register_active_chat_reply

        async def register(*args: Any) -> None:
            if committed:
                await original(*args)
            entered.set()
            await asyncio.Event().wait()

        with patch.object(_chat, "register_active_chat_reply", register):
            task = asyncio.create_task(self._run())
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(task, timeout=2)
        self._assert_interrupted_reply([])
        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.chat_active_reply_registry(),
            ),
            {},
        )

    async def test_acknowledgement_cancellation_persists_hints(self) -> None:
        """Single and repeated Stop preserve all committed hints in storage."""
        for boundary in (
            "commit_response",
            "payload_cleanup",
            "queue_notification",
            "injection_notification",
        ):
            for cancellations in (1, 3):
                with self.subTest(
                    boundary=boundary,
                    cancellations=cancellations,
                ):
                    await self.asyncSetUp()
                    await self._check_committed_hints(boundary, cancellations)

    async def test_late_receipts_leave_reply_and_stream_unchanged(
        self,
    ) -> None:
        """Reject stale receipts throughout an external tool's lifecycle."""
        tool = MockMixedSequentialTool()
        self.toolkit = Toolkit(tools=[tool])
        call = ToolCallBlock(
            id="current-call",
            name=tool.name,
            input='{"input":"draft"}',
            state="asking",
        )
        reply = AssistantMsg("agent", [call], id="current-reply")
        reply.set_confirmation_request(call.id, "card")
        state = self.session.state
        state.reply_id = reply.id
        state.context = [reply.model_copy(deep=True)]
        self.service._storage.get_message = AsyncMock(return_value=reply)
        result = ConfirmResult(
            request_id="card",
            confirmed=True,
            tool_call=call,
        )
        receipt = UserConfirmResultEvent(
            reply_id=reply.id,
            confirm_results=[result],
        )

        async def assert_ignored(event: UserConfirmResultEvent) -> None:
            self.input_msg = event
            self.events.clear()
            self.messages.clear()
            self.states.clear()
            before = self.session.state.model_dump()
            await self._run()
            self.assertEqual(
                (
                    self.events,
                    self.messages,
                    self.states,
                    self.session.state.model_dump(),
                ),
                ([], [], [], before),
            )

        previous = result.model_copy(deep=True)
        previous.tool_call.id = "previous-call"
        await assert_ignored(
            UserConfirmResultEvent(
                reply_id="previous-reply",
                confirm_results=[previous],
            ),
        )
        self.input_msg = receipt
        await self._run()
        self.assertEqual(
            [e["type"] for e in self.events][:2],
            ["USER_CONFIRM_RESULT", "REPLY_START"],
        )
        stored = self.messages[-1]
        self.assertEqual(
            stored.get_content_blocks("tool_call")[0].state,
            "submitted",
        )
        self.service._storage.get_message.return_value = stored
        self.session.state = self.states[-1]
        await assert_ignored(receipt)
        self.input_msg = ExternalExecutionResultEvent(
            reply_id=reply.id,
            execution_results=[
                ToolResultBlock(
                    id=call.id,
                    name=tool.name,
                    state="success",
                    output="done",
                ),
            ],
        )
        await self._run()
        stored = self.messages[-1]
        (result_block,) = stored.get_content_blocks("tool_result")
        self.assertEqual(
            (
                stored.finished_reason,
                self.events[-1]["type"],
                result_block.state,
                [block.text for block in result_block.output],
            ),
            ("completed", "REPLY_END", "success", ["done"]),
        )
        self.session.state = self.states[-1]
        self.service._storage.get_message.return_value = stored
        await assert_ignored(receipt)

    async def test_continuation_registration_cancellation_closes_tools(
        self,
    ) -> None:
        """A resumed reply retains its record and closes pending tool calls."""
        call = ToolCallBlock(
            id="call",
            name="work",
            input="{}",
            state=ToolCallState.ASKING,
        )
        reply = AssistantMsg(id="reply", name="agent", content=[call])
        self.session.state.context = [reply.model_copy(deep=True)]
        self.session.state.reply_context = ReplyContext(reply_id="reply")
        self.service._storage.get_message = AsyncMock(return_value=reply)
        self.input_msg = UserConfirmResultEvent(
            reply_id="reply",
            confirm_results=[ConfirmResult(confirmed=True, tool_call=call)],
        )
        entered = asyncio.Event()

        async def register(*_args: Any) -> None:
            entered.set()
            await asyncio.Event().wait()

        with patch.object(_chat, "register_active_chat_reply", register):
            task = asyncio.create_task(self._run())
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(task, timeout=2)

        self.assertEqual(len(self.messages), 1)
        stored = self.messages[0]
        self.assertEqual(
            (stored.id, stored.finished_reason, self.model.cnt),
            ("reply", ReplyFinishedReason.INTERRUPTED, 0),
        )
        self.assertEqual(
            [(b.type, b.id, b.state) for b in stored.content],
            [
                ("tool_call", "call", "finished"),
                ("tool_result", "call", "interrupted"),
            ],
        )
        self.assertEqual(
            [
                e["finished_reason"]
                for e in self.events
                if e["type"] == "REPLY_END"
            ],
            ["interrupted"],
        )

    async def test_cancellation_during_hint_publication_persists_batch(
        self,
    ) -> None:
        """Further Stop signals during publication cannot drop later hints."""
        for cancellations in (0, 1):
            with self.subTest(cancellations=cancellations):
                await self.asyncSetUp()
                await self._check_committed_hints(
                    "payload_cleanup",
                    cancellations,
                    True,
                )

    async def _check_committed_hints(
        self,
        boundary: str,
        cancellations: int,
        cancel_hint_publication: bool = False,
    ) -> None:
        """Cancel after queue commit and verify persisted event projection."""
        entered, release = asyncio.Event(), asyncio.Event()
        messages = [UserMsg("user", "first"), UserMsg("user", "second")]
        item = await _bus_ops.enqueue_chat_input(
            self.bus,
            "user",
            "session",
            "agent",
            messages,
        )
        register = _chat.register_active_chat_reply
        reserved = False

        async def reserve(*args: Any) -> None:
            nonlocal reserved
            await register(*args)
            await _bus_ops.steer_chat_input(
                self.bus,
                "user",
                "session",
                "agent",
                item["id"],
                args[2],
            )
            reserved = True

        target: Any
        if boundary == "commit_response":
            target, operation = self.bus, "queue_remove_if"
        elif boundary == "payload_cleanup":
            target, operation = _bus_ops, "discard_input"
        elif boundary == "queue_notification":
            target, operation = _bus_ops, "_notify_chat_queue_changed"
        else:
            target, operation = _adapter, "publish_session_event"
        original = getattr(target, operation)

        async def blocked(*args: Any, **kwargs: Any) -> Any:
            if not reserved or entered.is_set():
                return await original(*args, **kwargs)
            result = None
            if boundary == "commit_response":
                result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            if boundary == "commit_response":
                return result
            return await original(*args, **kwargs)

        with (
            patch.object(_chat, "register_active_chat_reply", reserve),
            patch.object(target, operation, blocked),
        ):
            task = asyncio.create_task(self._run(cancel_hint_publication))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                for _ in range(cancellations):
                    task.cancel()
                    await asyncio.sleep(0)
            finally:
                release.set()
                await asyncio.wait_for(task, timeout=2)

        hints = [
            HintBlock(
                id=f"{item['id']}:{index}",
                source="chat_input_steering",
                source_role=message.role,
                hint=list(message.content),
            )
            for index, message in enumerate(messages)
        ]
        self._assert_interrupted_reply(hints)
        self.assertEqual(
            [e["block_id"] for e in self.events if e["type"] == "HINT_BLOCK"],
            [hint.id for hint in hints],
        )
        self.assertEqual(
            await _bus_ops.list_chat_inputs(self.bus, "session"),
            [],
        )
        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.chat_input_payloads("session"),
            ),
            {},
        )
