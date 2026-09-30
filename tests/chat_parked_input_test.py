# -*- coding: utf-8 -*-
"""A parked reply must be resolved before a new user turn is accepted."""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from agentscope.app._router._chat import chat
from agentscope.app._router._schema import ChatRequest
from agentscope.app._service._projectors import SubagentHitlProjector
from agentscope.app._service._session import SessionStatus
from agentscope.event import ConfirmResult, UserConfirmResultEvent
from agentscope.message import ToolCallBlock, UserMsg


class _Registry:
    def __init__(self) -> None:
        self.spawned = False

    def spawn(self, coroutine, *, session_id: str) -> None:
        self.spawned = True
        coroutine.close()


class ChatParkedInputTest(IsolatedAsyncioTestCase):
    async def test_new_message_is_rejected_while_awaiting_confirmation(
        self,
    ) -> None:
        registry = _Registry()
        session_service = AsyncMock()
        session_service.get_session_status.return_value = (
            SessionStatus.AWAITING_PERMISSION
        )

        with self.assertRaises(HTTPException) as caught:
            await chat(
                ChatRequest(
                    agent_id="agent",
                    session_id="session",
                    input=UserMsg(name="user", content="在吗"),
                ),
                user_id="user",
                chat_service=AsyncMock(),
                chat_run_registry=registry,
                message_bus=AsyncMock(),
                session_service=session_service,
            )

        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("confirmation", caught.exception.detail)
        self.assertFalse(registry.spawned)

    async def test_new_message_starts_when_session_is_idle(self) -> None:
        registry = _Registry()
        session_service = AsyncMock()
        session_service.get_session_status.return_value = SessionStatus.IDLE

        response = await chat(
            ChatRequest(
                agent_id="agent",
                session_id="session",
                input=UserMsg(name="user", content="在吗"),
            ),
            user_id="user",
            chat_service=AsyncMock(),
            chat_run_registry=registry,
            message_bus=AsyncMock(),
            session_service=session_service,
        )

        self.assertEqual(response.status, "started")
        self.assertTrue(registry.spawned)

    async def test_stale_confirmation_is_rejected_before_enqueue(self) -> None:
        session_service = AsyncMock()
        session_service.get_session_status.return_value = SessionStatus.IDLE
        bus = AsyncMock()
        event = UserConfirmResultEvent(
            reply_id="completed-reply",
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id="finished-call",
                        name="Bash",
                        input="{}",
                    ),
                ),
            ],
        )

        with patch.object(
            SubagentHitlProjector,
            "resolve",
            new=AsyncMock(return_value=None),
        ), self.assertRaises(HTTPException) as caught:
            await chat(
                ChatRequest(
                    agent_id="agent",
                    session_id="session",
                    input=event,
                ),
                user_id="user",
                chat_service=AsyncMock(),
                chat_run_registry=_Registry(),
                message_bus=bus,
                session_service=session_service,
            )

        self.assertEqual(caught.exception.status_code, 409)
        bus.queue_push.assert_not_awaited()

    async def test_confirmation_is_enqueued_while_awaiting_permission(
        self,
    ) -> None:
        session_service = AsyncMock()
        session_service.get_session_status.return_value = (
            SessionStatus.AWAITING_PERMISSION
        )
        event = UserConfirmResultEvent(
            reply_id="parked-reply",
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id="pending-call",
                        name="Bash",
                        input="{}",
                    ),
                ),
            ],
        )

        with patch.object(
            SubagentHitlProjector,
            "resolve",
            new=AsyncMock(return_value=None),
        ), patch(
            "agentscope.app._router._chat.enqueue_run_trigger",
            new_callable=AsyncMock,
        ) as enqueue:
            response = await chat(
                ChatRequest(
                    agent_id="agent",
                    session_id="session",
                    input=event,
                ),
                user_id="user",
                chat_service=AsyncMock(),
                chat_run_registry=_Registry(),
                message_bus=AsyncMock(),
                session_service=session_service,
            )

        self.assertEqual(response.status, "started")
        enqueue.assert_awaited_once()
