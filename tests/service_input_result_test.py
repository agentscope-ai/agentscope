# -*- coding: utf-8 -*-
"""Durable input reconciliation must survive missing replay events."""
import asyncio
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentscope.app._chat_input_store import (
    INPUT_EXECUTION,
    finish_unbound_inputs,
)
from agentscope.app._bus_ops import (
    bind_chat_submission,
    cancel_chat_submission,
)
from agentscope.app._router._session import session_router
from agentscope.app._service import ChatService
from agentscope.app.deps import get_chat_service
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.message import AssistantMsg, ToolCallBlock, UserMsg
from agentscope.state import AgentState


class InputResultTest(IsolatedAsyncioTestCase):
    """Exercise the endpoint without relying on event history or ordering."""

    async def asyncSetUp(self) -> None:
        self.messages = {}
        self.bus = InMemoryMessageBus()
        await self.enterAsyncContext(self.bus)

        async def get_message(_user: str, _sid: str, mid: str) -> object:
            msg = self.messages.get(mid)
            return msg.model_copy(deep=True) if msg else None

        async def upsert(_user: str, _sid: str, msg: object) -> None:
            self.messages[msg.id] = msg.model_copy(deep=True)

        self.storage = SimpleNamespace(
            get_session=AsyncMock(
                return_value=SimpleNamespace(
                    state=AgentState(reply_id="B"),
                ),
            ),
            get_message=AsyncMock(side_effect=get_message),
            upsert_message=AsyncMock(side_effect=upsert),
        )
        self.service = ChatService(
            self.storage,
            Mock(),
            Mock(),
            Mock(),
            self.bus,
            Mock(),
        )

    async def test_submission_result_survives_reused_message_id(self) -> None:
        """A delayed cancellation resolves A while the same Msg ID runs B."""
        namespace = MessageBusKeys.chat_submissions("s")
        for submission, reply in (("first", "A"), ("later", "B")):
            await self.bus.registry_set(
                namespace,
                submission,
                json.dumps(
                    {
                        "user_id": "owner",
                        "agent_id": "a",
                        "queue_item_id": submission,
                        "state": "claimed",
                        "message_ids": ["shared"],
                        "reply_id": None,
                    },
                ),
            )
            await bind_chat_submission(
                self.bus,
                "owner",
                "s",
                "a",
                submission,
                reply,
            )
        self.messages["shared"] = UserMsg(
            "u",
            "later",
            id="shared",
            metadata={
                INPUT_EXECUTION: {"reply_id": "B", "finished": False},
            },
        )
        self.messages["A"] = AssistantMsg(
            name="a",
            id="A",
            content=[],
            finished_reason="completed",
        )
        self.messages["B"] = AssistantMsg(name="a", id="B", content=[])
        app = FastAPI()
        app.include_router(session_router)
        app.dependency_overrides[get_chat_service] = lambda: self.service
        async with self.bus.acquire_lock(
            MessageBusKeys.session_lock("s"),
        ), AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            self.assertEqual(
                await cancel_chat_submission(
                    self.bus,
                    "owner",
                    "s",
                    "a",
                    "first",
                ),
                {"state": "started", "queue_item_id": "first"},
            )
            for submission, expected in (
                ("first", {"state": "finished", "reply_id": "A"}),
                ("later", {"state": "reply", "reply_id": "B"}),
            ):
                response = await client.get(
                    "/sessions/s/inputs/shared/result",
                    params={"agent_id": "a", "submission_id": submission},
                    headers={"X-User-ID": "owner"},
                )
                self.assertEqual(
                    (response.status_code, response.json()),
                    (200, {**expected, "queue_item_id": submission}),
                )
            with self.assertRaises(ValueError):
                await bind_chat_submission(
                    self.bus,
                    "owner",
                    "s",
                    "a",
                    "first",
                    "B",
                )
            # Missing bindings must not fall back to shared message metadata.
            record = json.loads(
                await self.bus.registry_get(namespace, "first"),
            )
            record["reply_id"] = None
            await self.bus.registry_set(namespace, "first", json.dumps(record))
            self.assertEqual(
                await self.service.input_result(
                    "owner",
                    "s",
                    "a",
                    "shared",
                    submission_id="first",
                ),
                {
                    "state": "unknown",
                    "reply_id": None,
                    "queue_item_id": "first",
                },
            )

    async def test_http_result_does_not_guess_from_empty_queue(self) -> None:
        """Unknown inputs stay unknown; only the exact receipt can finish."""
        app = FastAPI()
        app.include_router(session_router)
        app.dependency_overrides[get_chat_service] = lambda: self.service
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            for receipt, reply, expected in (
                (None, None, {"state": "unknown", "reply_id": None}),
                (
                    {"reply_id": None, "finished": False},
                    None,
                    {"state": "unknown", "reply_id": None},
                ),
                (
                    {"reply_id": None, "finished": True},
                    None,
                    {"state": "finished", "reply_id": None},
                ),
                (
                    {"reply_id": "A", "finished": False},
                    None,
                    {"state": "reply", "reply_id": "A"},
                ),
                (
                    {"reply_id": "A", "finished": False},
                    AssistantMsg(name="a", id="A", content=[]),
                    {"state": "reply", "reply_id": "A"},
                ),
                (
                    {"reply_id": "A", "finished": False},
                    AssistantMsg(
                        name="a",
                        id="A",
                        content=[],
                        finished_reason="completed",
                    ),
                    {"state": "finished", "reply_id": "A"},
                ),
            ):
                with self.subTest(expected=expected, receipt=receipt):
                    self.messages.clear()
                    if receipt is not None:
                        self.messages["u"] = UserMsg(
                            name="u",
                            id="u",
                            content="voice",
                            metadata={INPUT_EXECUTION: receipt},
                        )
                    if reply is not None:
                        self.messages["A"] = reply
                    assert isinstance(expected, dict)
                    if expected["state"] == "reply":
                        await self.bus.try_lock(
                            MessageBusKeys.session_lock("s"),
                        )
                    response = await client.get(
                        "/sessions/s/inputs/u/result?agent_id=a",
                        headers={"X-User-ID": "owner"},
                    )
                    self.assertEqual(
                        (response.status_code, response.json()),
                        (200, expected),
                    )
                    await self.bus.unlock(MessageBusKeys.session_lock("s"))
            self.storage.get_session.return_value = None
            self.storage.get_message.reset_mock()
            response = await client.get(
                "/sessions/s/inputs/u/result?agent_id=a",
                headers={"X-User-ID": "owner"},
            )
            self.assertEqual(
                (response.status_code, response.json()),
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
            self.storage.get_message.assert_not_awaited()
            route_missing = await client.get("/not-an-api-route")
            self.assertEqual(
                (route_missing.status_code, route_missing.json()),
                (404, {"detail": "Not Found"}),
            )

    async def test_idle_result_distinguishes_parked_and_retired(self) -> None:
        """A bound input can finish without an end event only if not parked."""
        self.messages["u"] = UserMsg(
            name="u",
            id="u",
            content="voice",
            metadata={
                INPUT_EXECUTION: {"reply_id": "A", "finished": False},
            },
        )
        parked = AssistantMsg(
            name="a",
            id="A",
            content=[
                ToolCallBlock(
                    id="tool",
                    name="confirm",
                    input="{}",
                    state="asking",
                ),
            ],
        )
        for state, expected in (
            (AgentState(reply_id="A", context=[parked]), "reply"),
            (AgentState(reply_id="A"), "finished"),
            (AgentState(reply_id="B"), "finished"),
        ):
            with self.subTest(state=state):
                self.storage.get_session.return_value.state = state
                self.assertEqual(
                    await self.service.input_result("owner", "s", "a", "u"),
                    {"state": expected, "reply_id": "A"},
                )

    async def test_terminal_receipt_preserves_parked_reply(self) -> None:
        """Ending dispatch cannot mark a bound HITL reply as completed."""
        for mid, reply_id in (("failed", None), ("parked", "A")):
            self.messages[mid] = UserMsg(
                name="u",
                id=mid,
                content="voice",
                metadata={
                    INPUT_EXECUTION: {"reply_id": reply_id, "finished": False},
                },
            )
        await finish_unbound_inputs(
            self.storage,
            "owner",
            "s",
            ["failed", "parked", "missing"],
        )
        self.assertEqual(
            {mid: msg.metadata for mid, msg in self.messages.items()},
            {
                "failed": {
                    INPUT_EXECUTION: {
                        "reply_id": None,
                        "finished": True,
                    },
                },
                "parked": {
                    INPUT_EXECUTION: {
                        "reply_id": "A",
                        "finished": False,
                    },
                },
            },
        )

    async def test_result_waits_for_persistence_checkpoint(self) -> None:
        """No result is read halfway through a reply/state checkpoint."""
        async with self.bus.acquire_lock(
            MessageBusKeys.session_event_checkpoint_lock("s"),
        ):
            pending = asyncio.create_task(
                self.service.input_result("owner", "s", "a", "u"),
            )
            await asyncio.sleep(0)
            self.storage.get_message.assert_not_awaited()
        self.assertEqual(
            await asyncio.wait_for(pending, 1),
            {"state": "unknown", "reply_id": None},
        )
