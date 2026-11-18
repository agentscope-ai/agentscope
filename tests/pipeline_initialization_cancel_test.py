# -*- coding: utf-8 -*-
# pylint: disable=protected-access,cell-var-from-loop
"""Cancel initializing voice inputs without targeting successor replies."""
import asyncio
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import fakeredis.aioredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from service_message_bus_test import _make_bus
from chat_submission_cancellation_test import _reserve
from service_wakeup_dispatcher_test import _FakeChatService, _FakeStorage

from agentscope.app._bus_ops import (
    cancel_chat_submission,
    enqueue_chat_input,
    list_chat_inputs,
)
from agentscope.app._manager import CancelDispatcher, ChatRunRegistry
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app._router._chat import chat_router
from agentscope.app.deps import (
    get_current_user_id,
    get_message_bus,
    get_storage,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import TTSModelConfig
from agentscope.message import UserMsg


class PipelineInitializationCancelTest(IsolatedAsyncioTestCase):
    """Exercise durable intent and completion on both supported buses."""

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

    async def test_cancel_before_first_reply(self) -> None:
        """Blocked persistence and initialization both become cancellable."""
        for bus in self.backends:
            async with bus:
                for boundary in ("persist", "initialize", "claim_gap"):
                    with self.subTest(
                        backend=type(bus).__name__,
                        boundary=boundary,
                    ):
                        storage = _FakeStorage()
                        service = _FakeChatService()
                        registry = ChatRunRegistry()
                        queue = ChatInputQueue(bus, storage, service, registry)
                        self.addAsyncCleanup(queue.close)
                        dispatcher = CancelDispatcher(bus, registry, Mock())
                        entered, release = asyncio.Event(), asyncio.Event()
                        await _reserve(bus, boundary)
                        item = await enqueue_chat_input(
                            bus,
                            "u",
                            "s",
                            "a",
                            UserMsg("u", "voice"),
                            submission_id=boundary,
                        )
                        claim = queue._claim_chat_input

                        async def block(*_args: object, **_kw: object) -> None:
                            entered.set()
                            await release.wait()

                        async def delay_claim(sid: str) -> object:
                            result = await claim(sid)
                            await block()
                            return result

                        if boundary == "persist":
                            storage.upsert_message = AsyncMock(
                                side_effect=block,
                            )
                        else:
                            service.run = AsyncMock(side_effect=block)
                        if boundary == "claim_gap":
                            queue._claim_chat_input = delay_claim
                        async with registry, dispatcher:
                            task = registry.spawn(
                                queue.drain_chat_inputs("u", "s", "a"),
                                session_id="s",
                            )
                            await asyncio.wait_for(entered.wait(), 1)
                            if boundary == "claim_gap":
                                self.assertIsNone(
                                    registry.get(
                                        "s",
                                        queue_item_id=item["id"],
                                    ),
                                )
                                with patch.object(
                                    bus,
                                    "publish",
                                    side_effect=ConnectionError("offline"),
                                ):
                                    with self.assertRaises(ConnectionError):
                                        await cancel_chat_submission(
                                            bus,
                                            "u",
                                            "s",
                                            "a",
                                            boundary,
                                        )
                                release.set()
                            else:
                                self.assertEqual(
                                    await cancel_chat_submission(
                                        bus,
                                        "u",
                                        "s",
                                        "a",
                                        boundary,
                                    ),
                                    {
                                        "state": "started",
                                        "queue_item_id": item["id"],
                                    },
                                )
                            with self.assertRaises(asyncio.CancelledError):
                                await asyncio.wait_for(task, 2)
                        if boundary == "claim_gap":
                            service.run.assert_not_awaited()
                        self.assertEqual(
                            json.loads(
                                await bus.registry_get(
                                    MessageBusKeys.chat_submissions("s"),
                                    boundary,
                                ),
                            ),
                            {
                                "user_id": "u",
                                "agent_id": "a",
                                "queue_item_id": item["id"],
                                "reply_id": None,
                                "state": "finished",
                                "cancel_requested": True,
                                "expires_at": 1600,
                                "message_ids": [item["input"]["id"]],
                            },
                        )
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
                        self.assertEqual(await list_chat_inputs(bus, "s"), [])
                        self.assertEqual(
                            await bus.registry_getall(
                                MessageBusKeys.chat_input_claim("s"),
                            ),
                            {},
                        )

    async def test_terminal_receipt_write_can_recover(self) -> None:
        """A failed completion write keeps the claim available for recovery."""
        for bus in self.backends:
            with self.subTest(backend=type(bus).__name__):
                async with bus:
                    await _reserve(bus, "first")
                    item = await enqueue_chat_input(
                        bus,
                        "u",
                        "s",
                        "a",
                        UserMsg("u", "voice"),
                        submission_id="first",
                    )
                    service = _FakeChatService()
                    queue = ChatInputQueue(bus, _FakeStorage(), service)
                    self.addAsyncCleanup(queue.close)
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
                    write = bus.registry_set

                    async def fail_receipt(*args: str) -> None:
                        if args[0] == MessageBusKeys.chat_submissions("s"):
                            if json.loads(args[2])["state"] == "finished":
                                raise ConnectionError("receipt failed")
                        await write(*args)

                    with patch.object(
                        bus,
                        "registry_set",
                        side_effect=fail_receipt,
                    ):
                        with self.assertRaises(asyncio.CancelledError):
                            await queue.drain_chat_inputs("u", "s", "a")
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
                    self.assertTrue(
                        await queue.drain_chat_inputs("u", "s", "a"),
                    )
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
                    self.assertEqual(service.calls, [])

    async def test_queue_target_expires_at_first_reply(self) -> None:
        """Late initialization interrupts never stop subsequent replies."""
        registry = ChatRunRegistry()
        dispatcher = CancelDispatcher(Mock(), registry, Mock())
        ready, advance = asyncio.Event(), asyncio.Event()

        async def run() -> None:
            registry.set_queue_input("s", "A")
            registry.set_reply("s", "reply-A")
            registry.set_reply("s", None)
            ready.set()
            await advance.wait()
            registry.set_reply("s", "reply-B")
            advance.clear()
            ready.set()
            await advance.wait()

        async with registry:
            task = registry.spawn(run(), session_id="s")
            for _ in range(2):
                await asyncio.wait_for(ready.wait(), 1)
                ready.clear()
                dispatcher._interrupt_session("s", queue_item_id="A")
                self.assertEqual(task.cancelling(), 0)
                advance.set()
            await asyncio.wait_for(task, 1)
            ready.clear()

            async def initialize() -> None:
                registry.set_queue_input("s", "B")
                ready.set()
                await asyncio.Event().wait()

            successor = registry.spawn(initialize(), session_id="s")
            await asyncio.wait_for(ready.wait(), 1)
            dispatcher._interrupt_session("s", queue_item_id="A")
            self.assertEqual(successor.cancelling(), 0)
            dispatcher._interrupt_session("s", queue_item_id="B")
            with self.assertRaises(asyncio.CancelledError):
                await successor

    async def test_voice_queue_patch_preserves_message_ids(self) -> None:
        """HTTP edits preserve voice identities, while normal edits work."""
        for bus in self.backends:
            async with bus:
                app = FastAPI()
                app.include_router(chat_router)
                app.dependency_overrides[get_storage] = _FakeStorage
                app.dependency_overrides[get_message_bus] = lambda: bus
                app.dependency_overrides[get_current_user_id] = lambda: "u"
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    for mode in ("submission", "legacy", "chat"):
                        with self.subTest(
                            backend=type(bus).__name__,
                            mode=mode,
                        ):
                            messages = [
                                UserMsg("u", "one"),
                                UserMsg("u", "two"),
                            ]
                            if mode == "submission":
                                await _reserve(bus, mode)
                            item = await enqueue_chat_input(
                                bus,
                                "u",
                                "s",
                                "a",
                                messages,
                                submission_id=(
                                    mode if mode == "submission" else None
                                ),
                                pipeline_tts_config=(
                                    TTSModelConfig(
                                        type="openai_credential",
                                        credential_id="c",
                                        model="tts",
                                        parameters={},
                                    )
                                    if mode == "legacy"
                                    else None
                                ),
                            )
                            path = f"/chat/queue/{item['id']}"
                            for replacements in (
                                [UserMsg("u", "other"), messages[1]],
                                list(reversed(messages)),
                                messages[:1],
                                messages + [UserMsg("u", "three")],
                            ):
                                result = await client.patch(
                                    path,
                                    json={
                                        "agent_id": "a",
                                        "session_id": "s",
                                        "input": [
                                            m.model_dump(mode="json")
                                            for m in replacements
                                        ],
                                    },
                                )
                                self.assertEqual(
                                    result.status_code,
                                    200 if mode == "chat" else 409,
                                )
                                if mode != "chat":
                                    self.assertEqual(
                                        result.json(),
                                        {
                                            "detail": "Voice queue edits must "
                                            "preserve message identities.",
                                        },
                                    )
                            edited = [
                                UserMsg("u", "edited", id=m.id)
                                for m in messages
                            ]
                            result = await client.patch(
                                path,
                                json={
                                    "agent_id": "a",
                                    "session_id": "s",
                                    "input": [
                                        m.model_dump(mode="json")
                                        for m in edited
                                    ],
                                },
                            )
                            self.assertEqual(result.status_code, 200)
                            queued = await list_chat_inputs(bus, "s")
                            self.assertEqual(
                                queued[-1]["input"],
                                [m.model_dump(mode="json") for m in edited],
                            )
