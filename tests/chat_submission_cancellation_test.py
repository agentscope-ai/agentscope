# -*- coding: utf-8 -*-
# pylint: disable=protected-access,cell-var-from-loop
"""Submission fences distinguish unseen requests from claimed inputs."""
import asyncio
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import fakeredis.aioredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from service_message_bus_test import _make_bus
from service_wakeup_dispatcher_test import _FakeChatService, _FakeStorage

from agentscope.app._bus_ops import (
    ChatSubmissionConflictError,
    cancel_chat_submission,
    create_chat_submission,
    enqueue_chat_input,
    list_chat_inputs,
)
from agentscope.app._chat_input_store import cleanup_session
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app._router._chat import chat_router
from agentscope.app._service import SessionService
from agentscope.app.deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_message_bus,
    get_storage,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import UserMsg


async def _reserve(
    bus: object,
    submission_id: str,
    user: str = "u",
    sid: str = "s",
    agent: str = "a",
) -> dict:
    """Issue a server lease with a deterministic identity for assertions."""
    with patch(
        "agentscope.app._bus_ops.uuid",
        SimpleNamespace(uuid4=lambda: submission_id),
    ):
        return await create_chat_submission(bus, user, sid, agent)


class ChatSubmissionCancellationTest(IsolatedAsyncioTestCase):
    """Callbacks finish within each iteration on both message buses."""

    async def asyncSetUp(self) -> None:
        self.enterContext(
            patch("agentscope.app._bus_ops.time", return_value=1000),
        )
        self.enterContext(
            patch("agentscope.app._chat_receipts.time", return_value=1000),
        )
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(redis.aclose)
        self.backends = [InMemoryMessageBus(), _make_bus(redis)]

    async def test_http_cancel_before_delayed_post(self) -> None:
        """Cancellation fences a POST paused before its queue mutation."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    submission = str(uuid4())
                    entered, release = asyncio.Event(), asyncio.Event()

                    async def snapshot(*_args: object) -> tuple:
                        entered.set()
                        await release.wait()
                        return None, None

                    storage = _FakeStorage()
                    storage.get_session = AsyncMock(return_value=object())
                    app = FastAPI()
                    app.include_router(chat_router)
                    app.dependency_overrides[get_storage] = lambda: storage
                    app.dependency_overrides[get_message_bus] = lambda: bus
                    app.dependency_overrides[get_current_user_id] = lambda: "u"
                    service = SimpleNamespace(pipeline_snapshot=snapshot)
                    app.dependency_overrides[
                        get_chat_service
                    ] = lambda: service
                    app.dependency_overrides[get_chat_run_registry] = object
                    async with AsyncClient(
                        transport=ASGITransport(app=app),
                        base_url="http://test",
                    ) as client:
                        lease = await client.post(
                            "/chat/submissions",
                            params={"agent_id": "a", "session_id": "s"},
                        )
                        submission = lease.json()["submission_id"]
                        self.assertEqual(
                            (lease.status_code, lease.json()),
                            (
                                200,
                                {
                                    "submission_id": submission,
                                    "expires_at": 1600,
                                },
                            ),
                        )
                        body = {
                            "agent_id": "a",
                            "session_id": "s",
                            "mode": "pipeline_voice",
                            "submission_id": submission,
                            "input": UserMsg("u", "voice").model_dump(
                                mode="json",
                            ),
                        }
                        task = asyncio.create_task(
                            client.post("/chat/", json=body),
                        )
                        await asyncio.wait_for(entered.wait(), 1)
                        path = f"/chat/submissions/{submission}/cancel"
                        params = {"agent_id": "a", "session_id": "s"}
                        first = await client.post(path, params=params)
                        await cleanup_session(bus, "s")
                        second = await client.post(path, params=params)
                        release.set()
                        delayed = await asyncio.wait_for(task, 1)
                        self.assertEqual(
                            [
                                (r.status_code, r.json())
                                for r in (first, second)
                            ],
                            [
                                (
                                    200,
                                    {
                                        "state": "cancelled",
                                        "queue_item_id": None,
                                    },
                                ),
                            ]
                            * 2,
                        )
                        self.assertEqual(delayed.status_code, 409)
                        self.assertEqual(await list_chat_inputs(bus, "s"), [])
                        body["submission_id"] = str(uuid4())
                        await _reserve(bus, body["submission_id"])
                        fresh = await client.post("/chat/", json=body)
                        self.assertEqual(fresh.status_code, 200)
                        duplicate = await client.post("/chat/", json=body)
                        self.assertEqual(duplicate.status_code, 409)
                        self.assertEqual(
                            len(await list_chat_inputs(bus, "s")),
                            1,
                        )
                        body["mode"] = "chat"
                        invalid = await client.post("/chat/", json=body)
                        self.assertEqual(invalid.status_code, 422)
                        storage.get_session.return_value = None
                        missing = await client.post(path, params=params)
                        self.assertEqual(
                            (missing.status_code, missing.json()),
                            (
                                404,
                                {
                                    "detail": {
                                        "code": "session_not_found",
                                        "message": "Session 's' not found.",
                                    },
                                },
                            ),
                        )

    async def test_queued_response_loss_and_message_id_reuse(self) -> None:
        """Only the target submission is removed, even with reused Msg.id."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    msg = UserMsg("u", "same message", id="shared")
                    await _reserve(bus, "first")
                    await _reserve(bus, "later")
                    first = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        msg,
                        submission_id="first",
                    )
                    later = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        msg,
                        submission_id="later",
                    )
                    expected = {
                        "state": "cancelled",
                        "queue_item_id": first["id"],
                    }
                    for _ in range(2):
                        self.assertEqual(
                            await cancel_chat_submission(
                                bus,
                                "u",
                                "s",
                                "a",
                                "first",
                            ),
                            expected,
                        )
                    self.assertEqual(await list_chat_inputs(bus, "s"), [later])
                    service = _FakeChatService()
                    service.run = AsyncMock(wraps=service.run)
                    queue = ChatInputQueue(bus, _FakeStorage(), service)
                    self.addAsyncCleanup(queue.close)
                    await queue.drain_chat_inputs("u", "s", "a")
                    self.assertEqual(
                        [
                            call.kwargs["queue_item_id"]
                            for call in service.run.await_args_list
                        ],
                        [later["id"]],
                    )
                    self.assertEqual(
                        await cancel_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                            "later",
                        ),
                        {"state": "started", "queue_item_id": later["id"]},
                    )

    async def test_partial_enqueue_can_be_cancelled(self) -> None:
        """A reserved identity is safe to fence before any durable claim."""
        for bus in self.backends:
            async with bus:
                for boundary in ("reserve", "before_push", "after_push"):
                    with self.subTest(
                        backend=type(bus).__name__,
                        boundary=boundary,
                    ):
                        write, push = bus.registry_set, bus.queue_push
                        await _reserve(bus, boundary)

                        async def fail_write(*args: object) -> None:
                            await write(*args)
                            if args[0] == MessageBusKeys.chat_submissions("s"):
                                raise ConnectionError("reserve response lost")

                        async def fail_push(*args: object) -> None:
                            if boundary == "after_push":
                                await push(*args)
                            raise ConnectionError("push failed")

                        target = (
                            "registry_set"
                            if boundary == "reserve"
                            else "queue_push"
                        )
                        failure = (
                            fail_write if boundary == "reserve" else fail_push
                        )
                        with patch.object(bus, target, side_effect=failure):
                            with self.assertRaises(ConnectionError):
                                await enqueue_chat_input(
                                    bus,
                                    "u",
                                    "s",
                                    "a",
                                    UserMsg("u", "voice"),
                                    submission_id=boundary,
                                )
                        result = await cancel_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                            boundary,
                        )
                        self.assertEqual(result["state"], "cancelled")
                        self.assertEqual(await list_chat_inputs(bus, "s"), [])
                        with self.assertRaises(ChatSubmissionConflictError):
                            await enqueue_chat_input(
                                bus,
                                "u",
                                "s",
                                "a",
                                UserMsg("u", "late"),
                                submission_id=boundary,
                            )

    async def test_partial_cancel_never_executes(self) -> None:
        """A queue receipt remains authoritative if the fence write fails."""
        for bus in self.backends:
            async with bus:
                for boundary in ("receipt", "fence", "delete"):
                    with self.subTest(
                        backend=type(bus).__name__,
                        boundary=boundary,
                    ):
                        await _reserve(bus, boundary)
                        item = await enqueue_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            UserMsg("u", "cancel"),
                            submission_id=boundary,
                        )
                        write = bus.registry_set
                        key = (
                            MessageBusKeys.chat_input_cancellations("s")
                            if boundary == "receipt"
                            else MessageBusKeys.chat_submissions("s")
                        )

                        async def fail_write(*args: object) -> None:
                            await write(*args)
                            if args[0] == key:
                                raise ConnectionError("write response lost")

                        target = (
                            "queue_replace"
                            if boundary == "delete"
                            else "registry_set"
                        )
                        failure = (
                            ConnectionError
                            if boundary == "delete"
                            else fail_write
                        )
                        with patch.object(bus, target, side_effect=failure):
                            with self.assertRaises(ConnectionError):
                                await cancel_chat_submission(
                                    bus,
                                    "u",
                                    "s",
                                    "a",
                                    boundary,
                                )
                        service = _FakeChatService()
                        queue = ChatInputQueue(bus, _FakeStorage(), service)
                        self.addAsyncCleanup(queue.close)
                        await queue.drain_chat_inputs("u", "s", "a")
                        self.assertEqual(service.calls, [])
                        self.assertEqual(
                            await cancel_chat_submission(
                                bus,
                                "u",
                                "s",
                                "a",
                                boundary,
                            ),
                            {
                                "state": "cancelled",
                                "queue_item_id": item["id"],
                            },
                        )

    async def test_durable_claim_wins_before_claim_marker(self) -> None:
        """An ambiguous claim write must not be mistaken for an unseen POST."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    await _reserve(bus, "first")
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "claimed"),
                        submission_id="first",
                    )
                    queue = ChatInputQueue(
                        bus,
                        _FakeStorage(),
                        _FakeChatService(),
                    )
                    self.addAsyncCleanup(queue.close)
                    write = bus.registry_set

                    async def fail_claim(*args: object) -> None:
                        await write(*args)
                        if args[0] == MessageBusKeys.chat_input_claim("s"):
                            raise ConnectionError("claim response lost")

                    with patch.object(
                        bus,
                        "registry_set",
                        side_effect=fail_claim,
                    ):
                        with self.assertRaises(ConnectionError):
                            await queue._claim_chat_input("s")
                    expected = {
                        "state": "started",
                        "queue_item_id": item["id"],
                    }
                    self.assertEqual(
                        await cancel_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                            "first",
                        ),
                        expected,
                    )
                    with self.assertRaises(asyncio.CancelledError):
                        await queue.drain_chat_inputs("u", "s", "a")
                    expected["state"] = "cancelled"
                    self.assertEqual(
                        await cancel_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                            "first",
                        ),
                        expected,
                    )
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_cancellations("s"),
                        ),
                        {},
                    )

    async def test_cancel_serializes_with_inflight_enqueue(self) -> None:
        """A cancellation waits for the enqueue's mutation to commit."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    await _reserve(bus, "first")
                    entered, release = asyncio.Event(), asyncio.Event()
                    push = bus.queue_push

                    async def slow_push(*args: object) -> object:
                        if args[0] == MessageBusKeys.chat_inputs("s"):
                            entered.set()
                            await release.wait()
                        return await push(*args)

                    with patch.object(
                        bus,
                        "queue_push",
                        side_effect=slow_push,
                    ):
                        enqueue = asyncio.create_task(
                            enqueue_chat_input(
                                bus,
                                "u",
                                "s",
                                "a",
                                UserMsg("u", "voice"),
                                submission_id="first",
                            ),
                        )
                        await asyncio.wait_for(entered.wait(), 1)
                        cancel = asyncio.create_task(
                            cancel_chat_submission(
                                bus,
                                "u",
                                "s",
                                "a",
                                "first",
                            ),
                        )
                        await asyncio.sleep(0)
                        self.assertFalse(cancel.done())
                        release.set()
                        item, result = await asyncio.wait_for(
                            asyncio.gather(enqueue, cancel),
                            2,
                        )
                    self.assertEqual(
                        result,
                        {"state": "cancelled", "queue_item_id": item["id"]},
                    )
                    self.assertEqual(await list_chat_inputs(bus, "s"), [])

    async def test_unseen_cancellation_survives_lost_response_and_cleanup(
        self,
    ) -> None:
        """Persist fences across normal cleanup, scoped to the exact owner."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    await _reserve(bus, "first")
                    write = bus.registry_set

                    async def lose_response(*args: object) -> None:
                        await write(*args)
                        raise ConnectionError("cancel response lost")

                    with patch.object(
                        bus,
                        "registry_set",
                        side_effect=lose_response,
                    ):
                        with self.assertRaises(ConnectionError):
                            await cancel_chat_submission(
                                bus,
                                "u",
                                "s",
                                "a",
                                "first",
                            )
                    await cleanup_session(bus, "s")
                    self.assertEqual(
                        await cancel_chat_submission(
                            bus,
                            "u",
                            "s",
                            "a",
                            "first",
                        ),
                        {"state": "cancelled", "queue_item_id": None},
                    )
                    for user, agent in (("other", "a"), ("u", "other")):
                        with self.assertRaises(LookupError):
                            await cancel_chat_submission(
                                bus,
                                user,
                                "s",
                                agent,
                                "first",
                            )
                    self.assertEqual(
                        json.loads(
                            await bus.registry_get(
                                MessageBusKeys.chat_submissions("s"),
                                "first",
                            ),
                        ),
                        {
                            "user_id": "u",
                            "agent_id": "a",
                            "state": "cancelled",
                            "queue_item_id": None,
                            "expires_at": 1600,
                            "retire_at": 4600,
                        },
                    )
                    await SessionService(
                        _FakeStorage(),
                        bus,
                    )._purge_session_bus("s")
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_submissions("s"),
                        ),
                        {},
                    )
