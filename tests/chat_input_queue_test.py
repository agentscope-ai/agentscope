# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Chat queue lifecycle, recovery, resource limits and API regressions."""
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import fakeredis.aioredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from service_message_bus_test import _make_bus
from service_wakeup_dispatcher_test import _FakeChatService, _FakeStorage

from agentscope.app._bus_ops import (
    _count_user_chat_inputs,
    delete_chat_input,
    enqueue_chat_input,
    list_chat_inputs,
    reorder_chat_inputs,
    update_chat_input,
)
from agentscope.app._manager import (
    CancelDispatcher,
    ChatRunRegistry,
    WakeupDispatcher,
)
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app._manager._chat_run_registry import CHAT_RUN_SHUTDOWN
from agentscope.app._router._chat import chat_router
from agentscope.app._service import ChatService, SessionService
from agentscope.app._service._access import ResourceAccessService
from agentscope.app.access import ResourceKind, ResourceRef
from agentscope.app.deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_message_bus,
    get_storage,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.state import AgentState


class TestQueueStorageAndRecovery(IsolatedAsyncioTestCase):
    """Exercise real bus primitives, including Redis storage layouts."""

    async def test_failed_dequeue_keeps_claim_out_of_editable_queue(
        self,
    ) -> None:
        """Editing other turns cannot invalidate a durable input claim."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(redis.aclose)
        for backend in (InMemoryMessageBus(), _make_bus(redis)):
            with self.subTest(backend=type(backend).__name__):
                async with backend as bus:
                    original = UserMsg("user", "claimed input")
                    items = [
                        await enqueue_chat_input(bus, "u", "s", "a", msg)
                        for msg in (
                            original,
                            UserMsg("user", "second"),
                            UserMsg("user", "third"),
                        )
                    ]
                    queue = ChatInputQueue(
                        bus,
                        _FakeStorage(),
                        _FakeChatService(),
                    )
                    with patch.object(
                        bus,
                        "queue_drain",
                        AsyncMock(side_effect=ConnectionError("dequeue")),
                    ):
                        with self.assertRaises(ConnectionError):
                            await queue._claim_chat_input("s")
                    raw = await bus.registry_get(
                        MessageBusKeys.chat_input_claim("s"),
                        "claim",
                    )
                    claimed = json.loads(raw)["payload"]
                    await queue.close()
                    self.assertEqual(
                        await list_chat_inputs(bus, "s"),
                        items[1:],
                    )
                    with self.assertRaises(LookupError):
                        await update_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            items[0]["id"],
                            UserMsg("user", "must not replace claim"),
                        )
                    with self.assertRaises(LookupError):
                        await delete_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            items[0]["id"],
                        )
                    with self.assertRaises(ValueError):
                        await reorder_chat_inputs(
                            bus,
                            "u",
                            "s",
                            "a",
                            [item["id"] for item in items],
                        )
                    self.assertEqual(
                        await reorder_chat_inputs(
                            bus,
                            "u",
                            "s",
                            "a",
                            [items[2]["id"], items[1]["id"]],
                        ),
                        [items[2], items[1]],
                    )
                    replacement = UserMsg("user", "edited pending input")
                    updated = {
                        **items[1],
                        "input": replacement.model_dump(mode="json"),
                    }
                    self.assertEqual(
                        await update_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            items[1]["id"],
                            replacement,
                        ),
                        [items[2], updated],
                    )
                    self.assertEqual(
                        await delete_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            items[2]["id"],
                        ),
                        [updated],
                    )
                    self.assertEqual(
                        json.loads(
                            await bus.registry_get(
                                MessageBusKeys.chat_input_payloads("s"),
                                claimed["input_ref"],
                            ),
                        ),
                        original.model_dump(mode="json"),
                    )
                    chat = _FakeChatService()
                    restarted = ChatInputQueue(bus, _FakeStorage(), chat)
                    self.addAsyncCleanup(restarted.close)
                    for _ in range(2):
                        self.assertTrue(
                            await restarted.drain_chat_inputs("u", "s", "a"),
                        )
                    self.assertEqual(
                        [
                            call["input_msg"].model_dump(mode="json")
                            for call in chat.calls
                        ],
                        [
                            original.model_dump(mode="json"),
                            replacement.model_dump(mode="json"),
                        ],
                    )
                    self.assertEqual(await list_chat_inputs(bus, "s"), [])
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_payloads("s"),
                        ),
                        {},
                    )

    async def test_large_attachment_roundtrip_and_cleanup(self) -> None:
        """Base64 bytes survive edits and execution without global copies."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(redis.aclose)
        for backend in (InMemoryMessageBus(), _make_bus(redis)):
            async with backend as bus:
                message = UserMsg(
                    "user",
                    [
                        DataBlock(
                            source=Base64Source(
                                data=base64.b64encode(b"x" * 800_000).decode(),
                                media_type="image/png",
                            ),
                        ),
                    ],
                )
                first = await enqueue_chat_input(bus, "u", "s", "a", message)
                second = await enqueue_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    UserMsg("user", "next"),
                )
                entries = await bus.queue_read(MessageBusKeys.chat_inputs("s"))
                self.assertLess(len(json.dumps(entries)), 1_000)
                self.assertEqual(
                    await list_chat_inputs(bus, "s"),
                    [first, second],
                )
                updated = await update_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    first["id"],
                    message,
                )
                self.assertEqual(updated, [first, second])
                self.assertEqual(
                    len(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_payloads("s"),
                        ),
                    ),
                    2,
                )
                queue = ChatInputQueue(bus, _FakeStorage(), _FakeChatService())
                self.addAsyncCleanup(queue.close)
                payload, state = await queue._claim_chat_input("s")
                self.assertEqual(state, "claimed")
                self.assertEqual(
                    payload["input"],
                    message.model_dump(mode="json"),
                )
                self.assertEqual(
                    await bus.registry_getall(
                        MessageBusKeys.chat_input_inflight_registry(),
                    ),
                    {
                        "s": json.dumps(
                            {
                                "id": first["id"],
                                "user_id": "u",
                                "agent_id": "a",
                            },
                        ),
                    },
                )
                local_claim = await bus.registry_get(
                    MessageBusKeys.chat_input_claim("s"),
                    "claim",
                )
                self.assertLess(len(local_claim), 1_000)
                await queue._finish_chat_input_claim("s")
                await delete_chat_input(bus, "u", "s", "a", second["id"])
                for key in (
                    MessageBusKeys.chat_input_payloads("s"),
                    MessageBusKeys.chat_input_claim("s"),
                    MessageBusKeys.chat_input_user_sessions("u"),
                    MessageBusKeys.chat_input_pending_registry(),
                    MessageBusKeys.chat_input_inflight_registry(),
                ):
                    self.assertEqual(await bus.registry_getall(key), {})

    async def test_enqueue_and_claim_do_not_scan_global_indexes(self) -> None:
        """Quota checks read only this user's lightweight sessions."""
        async with InMemoryMessageBus() as bus:
            other = await enqueue_chat_input(
                bus,
                "other",
                "other-s",
                "a",
                UserMsg("u", "other"),
            )
            getall = bus.registry_getall
            read = bus.queue_read

            async def guarded_getall(key: str) -> dict[str, str]:
                self.assertEqual(
                    key,
                    MessageBusKeys.chat_input_user_sessions("u"),
                )
                return await getall(key)

            async def guarded_read(key: str, max_count: int = 100) -> list:
                self.assertNotEqual(key, MessageBusKeys.chat_inputs("other-s"))
                return await read(key, max_count=max_count)

            queue = ChatInputQueue(bus, _FakeStorage(), _FakeChatService())
            self.addAsyncCleanup(queue.close)
            with patch.object(
                bus,
                "registry_getall",
                guarded_getall,
            ), patch.object(bus, "queue_read", guarded_read):
                await enqueue_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    UserMsg("u", "first"),
                )
                await queue._claim_chat_input("s")
                await enqueue_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    UserMsg("u", "second"),
                )
            self.assertEqual(await list_chat_inputs(bus, "other-s"), [other])

    async def test_poison_claim_exhausts_budget_then_advances(self) -> None:
        """Persist attempt count across coordinator restarts; skip poison."""
        async with InMemoryMessageBus() as bus:
            storage = _FakeStorage()
            original = storage.upsert_message

            async def persist(
                user: str,
                session: str,
                message: UserMsg,
            ) -> None:
                if message.id == "poison":
                    raise RuntimeError("permanently invalid stored message")
                await original(user, session, message)

            storage.upsert_message = AsyncMock(side_effect=persist)
            chat = _FakeChatService()
            bad = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "bad", id="poison"),
            )
            await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "good", id="successor"),
            )
            with patch.object(MessageBusKeys, "CHAT_INPUT_RETRY_SECS", 0):
                for _ in range(MessageBusKeys.CHAT_INPUT_MAX_ATTEMPTS):
                    queue = ChatInputQueue(bus, storage, chat)
                    self.assertFalse(
                        await queue.drain_chat_inputs("u", "s", "a"),
                    )
                    await queue.close()
                queue = ChatInputQueue(bus, storage, chat)
                self.addAsyncCleanup(queue.close)
                self.assertTrue(await queue.drain_chat_inputs("u", "s", "a"))
                self.assertTrue(await queue.drain_chat_inputs("u", "s", "a"))
            self.assertEqual(storage.upsert_message.call_count, 4)
            self.assertEqual(
                [call["input_msg"].id for call in chat.calls],
                ["successor"],
            )
            events = await bus.log_read(MessageBusKeys.session_events("s"))
            failed = [
                event["value"]
                for _, event in events
                if event.get("name") == "chat_input_failed"
            ]
            self.assertEqual(
                failed,
                [
                    {
                        "queue_item_id": bad["id"],
                        "message_ids": ["poison"],
                        "message": (
                            "The queued message could not be saved after "
                            "repeated attempts. Check the server log."
                        ),
                    },
                ],
            )
            self.assertEqual(
                await bus.registry_getall(
                    MessageBusKeys.chat_input_user_sessions("u"),
                ),
                {},
            )

    async def test_committed_mutations_survive_notification_failure(
        self,
    ) -> None:
        """Edit, reorder and delete return the state they actually wrote."""
        async with InMemoryMessageBus() as bus:
            first = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "first"),
            )
            second = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "second"),
            )
            with patch.object(
                bus,
                "publish",
                side_effect=RuntimeError("offline"),
            ):
                changed = await update_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    first["id"],
                    UserMsg("u", "edited"),
                )
                self.assertEqual(await list_chat_inputs(bus, "s"), changed)
                ordered = await reorder_chat_inputs(
                    bus,
                    "u",
                    "s",
                    "a",
                    [second["id"], first["id"]],
                )
                self.assertEqual(ordered, list(reversed(changed)))
                deleted = await delete_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    first["id"],
                )
                self.assertEqual(deleted, [second])
                self.assertEqual(await list_chat_inputs(bus, "s"), deleted)

    async def test_snapshot_does_not_acquire_mutation_lock(self) -> None:
        """Polling succeeds even while a mutation lock is held."""
        async with InMemoryMessageBus() as bus:
            item = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "read"),
            )
            async with bus.acquire_lock(
                MessageBusKeys.chat_input_mutation_lock("s"),
            ):
                result = await asyncio.wait_for(
                    list_chat_inputs(bus, "s"),
                    0.1,
                )
            self.assertEqual(result, [item])

    async def test_session_deletion_clears_payloads_and_user_index(
        self,
    ) -> None:
        """A deleted session leaves neither claimed nor pending payloads."""
        async with InMemoryMessageBus() as bus:
            await enqueue_chat_input(bus, "u", "s", "a", UserMsg("u", "one"))
            await enqueue_chat_input(bus, "u", "s", "a", UserMsg("u", "two"))
            queue = ChatInputQueue(bus, _FakeStorage(), _FakeChatService())
            await queue._claim_chat_input("s")
            service = object.__new__(SessionService)
            service._bus = bus
            await service._purge_session_bus("s")
            self.assertEqual(await list_chat_inputs(bus, "s"), [])
            for key in (
                MessageBusKeys.chat_input_payloads("s"),
                MessageBusKeys.chat_input_claim("s"),
                MessageBusKeys.chat_input_user_sessions("u"),
                MessageBusKeys.chat_input_pending_registry(),
                MessageBusKeys.chat_input_inflight_registry(),
            ):
                self.assertEqual(await bus.registry_getall(key), {})

    async def test_recovery_collects_payload_from_interrupted_enqueue(
        self,
    ) -> None:
        """A crash after payload write but before queue push is collectible."""
        async with InMemoryMessageBus() as bus:
            with patch.object(
                bus,
                "queue_push",
                side_effect=RuntimeError("failed"),
            ):
                with self.assertRaises(RuntimeError):
                    await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "lost"),
                    )
            queue = ChatInputQueue(bus, _FakeStorage(), _FakeChatService())
            self.addAsyncCleanup(queue.close)
            self.assertEqual(
                await queue._recover_pending_chat_inputs_locked(),
                0,
            )
            self.assertEqual(
                await bus.registry_getall(
                    MessageBusKeys.chat_input_payloads("s"),
                ),
                {},
            )
            self.assertEqual(
                await bus.registry_getall(
                    MessageBusKeys.chat_input_user_sessions("u"),
                ),
                {},
            )


class TestQueueFailureScope(IsolatedAsyncioTestCase):
    """Match existing agent-state and queue-event contracts."""

    async def test_shared_agent_waits_before_claiming_input(self) -> None:
        """Shared agents retain queued input until pending tools finish."""
        for state in ("asking", "submitted", None):
            with self.subTest(state=state):
                async with InMemoryMessageBus() as bus:
                    storage = _FakeStorage()
                    record = SimpleNamespace(
                        source="user",
                        data=SimpleNamespace(name="shared agent"),
                    )
                    storage.get_agent = AsyncMock(
                        side_effect=lambda user_id, _agent_id, owned=record: (
                            owned if user_id == "owner" else None
                        ),
                    )
                    session = SimpleNamespace(
                        state=AgentState(
                            context=[
                                AssistantMsg(
                                    name="shared agent",
                                    content=[
                                        ToolCallBlock(
                                            id="tool",
                                            name="external",
                                            input="{}",
                                            state=state,
                                        ),
                                    ]
                                    if state
                                    else "done",
                                ),
                            ],
                        ),
                    )
                    storage.get_session = AsyncMock(return_value=session)
                    policy = SimpleNamespace(
                        list_accessible=AsyncMock(
                            return_value=[
                                ResourceRef(
                                    kind=ResourceKind.AGENT,
                                    owner_id="owner",
                                    resource_id="a",
                                ),
                            ],
                        ),
                    )
                    service = object.__new__(ChatService)
                    service._access = ResourceAccessService(storage, policy)
                    service.run = AsyncMock()
                    queue = ChatInputQueue(bus, storage, service)
                    self.addAsyncCleanup(queue.close)
                    message = UserMsg("viewer", "next turn")
                    item = await enqueue_chat_input(
                        bus,
                        "viewer",
                        "s",
                        "a",
                        message,
                    )
                    self.assertEqual(
                        await queue.drain_chat_inputs("viewer", "s", "a"),
                        state is None,
                    )
                    self.assertEqual(
                        await list_chat_inputs(bus, "s"),
                        [item] if state else [],
                    )
                    self.assertEqual(
                        storage.persisted_messages,
                        [] if state else [("viewer", "s", message)],
                    )
                    if state:
                        service.run.assert_not_awaited()
                        session.state.context = []
                        self.assertTrue(
                            await queue.drain_chat_inputs("viewer", "s", "a"),
                        )
                    service.run.assert_awaited_once_with(
                        user_id="viewer",
                        session_id="s",
                        agent_id="a",
                        input_msg=message,
                        queue_item_id=item["id"],
                        input_persisted=True,
                    )

    async def test_parked_detection_uses_owning_agent_and_results(
        self,
    ) -> None:
        """Other agents and completed submitted tools do not park a queue."""
        queue = ChatInputQueue(
            InMemoryMessageBus(),
            _FakeStorage(),
            _FakeChatService(),
        )
        for name, state, with_result, expected in (
            ("agent", "asking", False, True),
            ("other", "asking", False, False),
            ("agent", "submitted", False, True),
            ("agent", "submitted", True, False),
        ):
            blocks = [
                ToolCallBlock(
                    id="tool",
                    name="external",
                    input="{}",
                    state=state,
                ),
            ]
            if with_result:
                blocks.append(
                    ToolResultBlock(id="tool", name="external", output="done"),
                )
            session = SimpleNamespace(
                state=AgentState(
                    context=[AssistantMsg(name=name, content=blocks)],
                ),
            )
            self.assertEqual(
                await queue._is_session_parked(session, "u", "a"),
                expected,
            )

    async def test_only_queued_run_failures_emit_queue_event(self) -> None:
        """Channel messages keep their normal error reporting semantics."""
        bus = InMemoryMessageBus()
        service = object.__new__(ChatService)
        service._message_bus = bus
        message = UserMsg("u", "test", id="message")
        with patch.object(
            service,
            "_run_impl",
            side_effect=RuntimeError("failed"),
        ):
            await service.run("u", "s", "a", input_msg=message)
            self.assertEqual(
                await bus.log_read(MessageBusKeys.session_events("s")),
                [],
            )
            await service.run(
                "u",
                "s",
                "a",
                input_msg=message,
                queue_item_id="item",
            )
        events = await bus.log_read(MessageBusKeys.session_events("s"))
        self.assertEqual(
            [(event["name"], event["value"]) for _, event in events],
            [
                (
                    "chat_input_failed",
                    {
                        "queue_item_id": "item",
                        "message_ids": ["message"],
                        "message": (
                            "The queued message could not be processed. "
                            "Check the server log for details."
                        ),
                    },
                ),
            ],
        )


class TestTerminalChatClaims(IsolatedAsyncioTestCase):
    """Execution outcome is independent from queue acknowledgement."""

    async def asyncSetUp(self) -> None:
        self.bus = await self.enterAsyncContext(InMemoryMessageBus())
        self.storage = _FakeStorage()
        self.chat = _FakeChatService()
        self.queue = ChatInputQueue(self.bus, self.storage, self.chat)
        self.addAsyncCleanup(self.queue.close)
        self.item = await enqueue_chat_input(
            self.bus,
            "u",
            "s",
            "a",
            UserMsg("u", "run once", id="message"),
        )

    async def _event_names(self) -> list[str]:
        return [
            event.get("name")
            for _, event in await self.bus.log_read(
                MessageBusKeys.session_events("s"),
            )
        ]

    async def test_finish_failure_retries_without_replaying_or_failing(
        self,
    ) -> None:
        """A completed run survives a transient failure before deletion."""
        original = self.queue._finish_chat_input_claim
        calls = 0

        async def fail_once(session_id: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raw = await self.bus.registry_get(
                    MessageBusKeys.chat_input_claim("s"),
                    "claim",
                )
                assert raw is not None
                self.assertEqual(json.loads(raw)["state"], "completed")
                raise RuntimeError("Redis unavailable")
            await original(session_id)

        with patch.object(
            self.queue,
            "_finish_chat_input_claim",
            fail_once,
        ), patch.object(MessageBusKeys, "CHAT_INPUT_RETRY_SECS", 0):
            self.assertTrue(await self.queue.drain_chat_inputs("u", "s", "a"))
        self.assertEqual(calls, 2)
        self.assertEqual(len(self.chat.calls), 1)
        self.assertEqual(await self._event_names(), ["chat_input_started"])
        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.chat_input_claim("s"),
            ),
            {},
        )

    async def test_completed_marker_write_retries_under_dispatch_lock(
        self,
    ) -> None:
        """A failed marker write does not re-enter the input-persisted path."""
        original = self.queue._set_chat_input_claim_state
        completed_attempts = 0

        async def fail_once(
            session_id: str,
            payload: dict,
            state: str,
        ) -> None:
            nonlocal completed_attempts
            if state == "completed":
                completed_attempts += 1
                self.assertTrue(
                    await self.bus.is_locked(
                        MessageBusKeys.chat_input_dispatch_lock("s"),
                    ),
                )
                if completed_attempts == 1:
                    raise RuntimeError("marker write failed")
            await original(session_id, payload, state)

        with patch.object(
            self.queue,
            "_set_chat_input_claim_state",
            fail_once,
        ), patch.object(MessageBusKeys, "CHAT_INPUT_RETRY_SECS", 0):
            self.assertTrue(await self.queue.drain_chat_inputs("u", "s", "a"))
        self.assertEqual(completed_attempts, 2)
        self.assertEqual(len(self.chat.calls), 1)
        self.assertEqual(await self._event_names(), ["chat_input_started"])

    async def test_restart_silently_acknowledges_completed_without_payload(
        self,
    ) -> None:
        """Completed recovery does not need the attachment blob or execute."""
        payload, _ = await self.queue._claim_chat_input("s")
        await self.queue._set_chat_input_claim_state("s", payload, "completed")
        await self.bus.registry_drop(MessageBusKeys.chat_input_payloads("s"))
        restarted = ChatInputQueue(self.bus, self.storage, self.chat)
        self.addAsyncCleanup(restarted.close)
        self.assertTrue(await restarted.drain_chat_inputs("u", "s", "a"))
        self.assertEqual(self.chat.calls, [])
        self.assertEqual(await self._event_names(), [])
        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.chat_input_inflight_registry(),
            ),
            {},
        )

    async def test_shutdown_during_finish_keeps_completed_outcome(
        self,
    ) -> None:
        """Cancellation after run does not emit an interrupted outcome."""
        entered = asyncio.Event()

        async def blocked_finish(_session_id: str) -> None:
            entered.set()
            await asyncio.Event().wait()

        with patch.object(
            self.queue,
            "_finish_chat_input_claim",
            blocked_finish,
        ):
            task = asyncio.create_task(
                self.queue.drain_chat_inputs("u", "s", "a"),
            )
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel(CHAT_RUN_SHUTDOWN)
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(await self.queue.drain_chat_inputs("u", "s", "a"))
        self.assertEqual(len(self.chat.calls), 1)
        self.assertEqual(await self._event_names(), ["chat_input_started"])

    async def test_shutdown_preserves_claim_before_execution(self) -> None:
        """Startup recovery retains attempts and dispatches preserved input."""
        entered = asyncio.Event()

        async def blocked_persist(*_args: object) -> None:
            entered.set()
            await asyncio.Event().wait()

        registry = ChatRunRegistry()
        with patch.object(self.storage, "upsert_message", blocked_persist):
            registry.spawn(
                self.queue.drain_chat_inputs("u", "s", "a"),
                session_id="s",
            )
            await asyncio.wait_for(entered.wait(), 1)
            before = await self.bus.registry_get(
                MessageBusKeys.chat_input_claim("s"),
                "claim",
            )
            assert before is not None
            expected_claim = json.loads(before)
            self.assertEqual(expected_claim["payload"]["attempts"], 1)
            await registry.__aexit__()
        raw = await self.bus.registry_get(
            MessageBusKeys.chat_input_claim("s"),
            "claim",
        )
        assert raw is not None
        self.assertEqual(json.loads(raw), expected_claim)
        self.assertEqual(expected_claim["state"], "claimed")
        self.assertEqual(self.chat.calls, [])
        self.assertEqual(await self._event_names(), [])

        # Remove the original enqueue hint: only startup recovery can wake it.
        await self.bus.queue_drain(MessageBusKeys.wakeup_queue())
        resumed_claims = []
        persist = self.storage.upsert_message

        async def record_recovery_attempt(*args: object) -> None:
            resumed = await self.bus.registry_get(
                MessageBusKeys.chat_input_claim("s"),
                "claim",
            )
            assert resumed is not None
            resumed_claims.append(json.loads(resumed))
            await persist(*args)

        async with ChatRunRegistry() as resumed_registry:
            dispatcher = WakeupDispatcher(
                message_bus=self.bus,
                storage=self.storage,
                chat_service=self.chat,
                chat_run_registry=resumed_registry,
            )
            recover_pending = (
                dispatcher._chat_queue._recover_pending_chat_inputs_locked
            )
            with patch.object(
                self.storage,
                "upsert_message",
                record_recovery_attempt,
            ), patch.object(
                dispatcher._chat_queue,
                "_recover_pending_chat_inputs_locked",
                wraps=recover_pending,
            ) as recovery:
                async with dispatcher:
                    await asyncio.wait_for(self.chat.notify.wait(), 1)
                    task = resumed_registry.get("s")
                    if task is not None:
                        await asyncio.wait_for(task, 1)
                    recovery.assert_awaited_once()
        expected_claim["payload"]["attempts"] = 2
        self.assertEqual(resumed_claims, [expected_claim])
        self.assertEqual(
            [call["input_msg"].id for call in self.chat.calls],
            ["message"],
        )
        self.assertEqual(await self._event_names(), ["chat_input_started"])
        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.chat_input_claim("s"),
            ),
            {},
        )

    async def test_explicit_stop_is_not_overwritten_by_shutdown(self) -> None:
        """A pending user Stop remains terminal even before run starts."""
        entered = asyncio.Event()

        async def blocked_persist(*_args: object) -> None:
            entered.set()
            await asyncio.Event().wait()

        registry = ChatRunRegistry()
        cancel = CancelDispatcher(self.bus, registry, Mock())
        with patch.object(self.storage, "upsert_message", blocked_persist):
            registry.spawn(
                self.queue.drain_chat_inputs("u", "s", "a"),
                session_id="s",
            )
            await asyncio.wait_for(entered.wait(), 1)
            cancel._interrupt_session("s")
            await registry.__aexit__()
        self.assertEqual(self.chat.calls, [])
        self.assertEqual(await self._event_names(), ["chat_input_cancelled"])
        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.chat_input_claim("s"),
            ),
            {},
        )
        self.assertFalse(await self.queue.drain_chat_inputs("u", "s", "a"))


class TestChatInputPayloads(IsolatedAsyncioTestCase):
    """Preserve large input compatibility and read only referenced payloads."""

    async def test_large_attachment_can_be_enqueued_and_edited(self) -> None:
        """Inputs exceeding the removed attachment budget remain accepted."""
        message = UserMsg(
            "u",
            [
                DataBlock(
                    source=Base64Source(
                        data=base64.b64encode(
                            b"x" * (24 * 1024 * 1024 + 1),
                        ).decode(),
                        media_type="image/png",
                    ),
                ),
            ],
        )
        async with InMemoryMessageBus() as bus:
            item = await enqueue_chat_input(bus, "u", "s", "a", message)
            self.assertEqual(item["input"], message.model_dump(mode="json"))
            self.assertEqual(
                await update_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    item["id"],
                    message,
                ),
                [item],
            )
            self.assertEqual(await list_chat_inputs(bus, "s"), [item])

    async def test_large_text_can_be_sent_and_edited_over_http(self) -> None:
        """The HTTP queue accepts text beyond the removed one MB budget."""
        async with InMemoryMessageBus() as bus:
            storage, chat, registry = (
                _FakeStorage(),
                _FakeChatService(),
                ChatRunRegistry(),
            )
            app = FastAPI()
            app.include_router(chat_router)
            app.dependency_overrides = {
                get_current_user_id: lambda: "u",
                get_storage: lambda: storage,
                get_message_bus: lambda: bus,
                get_chat_service: lambda: chat,
                get_chat_run_registry: lambda: registry,
            }
            body = {
                "agent_id": "a",
                "session_id": "s",
                "input": UserMsg("u", "x" * 1_000_001).model_dump(mode="json"),
            }
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                sent = await client.post("/chat/", json=body)
                self.assertEqual(sent.status_code, 200)
                items = await list_chat_inputs(bus, "s")
                self.assertEqual(len(items), 1)
                self.assertEqual(
                    sent.json(),
                    {
                        "status": "queued",
                        "session_id": "s",
                        "queue_item_id": items[0]["id"],
                    },
                )
                self.assertEqual(items[0]["input"], body["input"])
                body["input"] = UserMsg("u", "y" * 1_000_002).model_dump(
                    mode="json",
                )
                edited = await client.patch(
                    f"/chat/queue/{items[0]['id']}",
                    json=body,
                )
                items[0]["input"] = body["input"]
                self.assertEqual(
                    (edited.status_code, edited.json()),
                    (200, {"items": items}),
                )
                self.assertEqual(await list_chat_inputs(bus, "s"), items)
            self.assertEqual(chat.calls, [])

    async def test_list_uses_one_hmget_for_only_pending_references(
        self,
    ) -> None:
        """Reading a queue does not fetch unrelated or inflight attachments."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(redis.aclose)
        async with _make_bus(redis) as bus:
            first = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "first"),
            )
            second = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "second"),
            )
            await bus.registry_set(
                MessageBusKeys.chat_input_payloads("s"),
                "unreferenced",
                "do not read",
            )
            entries = await bus.queue_read(MessageBusKeys.chat_inputs("s"))
            refs = [entry["input_ref"] for _, entry in entries]
            with patch.object(
                redis,
                "hmget",
                wraps=redis.hmget,
            ) as many, patch.object(
                redis,
                "hget",
                wraps=redis.hget,
            ) as single, patch.object(
                redis,
                "hgetall",
                side_effect=AssertionError("whole hash"),
            ):
                self.assertEqual(
                    await list_chat_inputs(bus, "s"),
                    [first, second],
                )
                many.assert_called_once_with(
                    MessageBusKeys.chat_input_payloads("s"),
                    refs,
                )
                single.assert_called_once_with(
                    MessageBusKeys.chat_input_claim("s"),
                    "claim",
                )


class TestCorruptQueueMetadata(IsolatedAsyncioTestCase):
    """Malformed data cannot repeatedly break quotas or terminal cleanup."""

    async def test_corrupt_claim_counts_without_blocking_enqueue(
        self,
    ) -> None:
        """Bad claim metadata uses one quota slot without causing a 500."""
        async with InMemoryMessageBus() as bus:
            await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "pending"),
            )
            for raw in ("{broken", "[]", "{}", '{"id": []}', '{"id": null}'):
                with self.subTest(raw=raw):
                    await bus.registry_set(
                        MessageBusKeys.chat_input_inflight_registry(),
                        "s",
                        raw,
                    )
                    self.assertEqual(
                        await _count_user_chat_inputs(bus, "u"),
                        2,
                    )
            await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "accepted"),
            )
            self.assertEqual(len(await list_chat_inputs(bus, "s")), 2)

    async def test_corrupt_terminal_claim_does_not_prevent_cleanup(
        self,
    ) -> None:
        """Invalid claim data does not prevent removal of empty indexes."""
        for raw in ("{broken", "[]", "{}", '{"payload": []}'):
            with self.subTest(raw=raw):
                async with InMemoryMessageBus() as bus:
                    await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "pending"),
                    )
                    queue = ChatInputQueue(
                        bus,
                        _FakeStorage(),
                        _FakeChatService(),
                    )
                    await queue._claim_chat_input("s")
                    await bus.registry_set(
                        MessageBusKeys.chat_input_claim("s"),
                        "claim",
                        raw,
                    )
                    await queue._finish_chat_input_claim("s")
                    for key in (
                        MessageBusKeys.chat_input_claim("s"),
                        MessageBusKeys.chat_input_payloads("s"),
                        MessageBusKeys.chat_input_inflight_registry(),
                        MessageBusKeys.chat_input_pending_registry(),
                        MessageBusKeys.chat_input_user_sessions("u"),
                    ):
                        self.assertEqual(await bus.registry_getall(key), {})

    async def test_corrupt_payload_is_failed_and_successor_runs(self) -> None:
        """A broken referenced payload cannot block subsequent inputs."""
        async with InMemoryMessageBus() as bus:
            bad = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "bad"),
            )
            await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("u", "next", id="next"),
            )
            await bus.registry_set(
                MessageBusKeys.chat_input_payloads("s"),
                bad["id"],
                "{broken",
            )
            chat = _FakeChatService()
            queue = ChatInputQueue(bus, _FakeStorage(), chat)
            self.assertTrue(await queue.drain_chat_inputs("u", "s", "a"))
            self.assertEqual(
                [call["input_msg"].id for call in chat.calls],
                ["next"],
            )
            events = await bus.log_read(MessageBusKeys.session_events("s"))
            self.assertEqual(
                [
                    (event["name"], event["value"]["queue_item_id"])
                    for _, event in events
                    if event["name"] == "chat_input_failed"
                ],
                [("chat_input_failed", bad["id"])],
            )
