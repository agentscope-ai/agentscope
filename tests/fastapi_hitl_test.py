# -*- coding: utf-8 -*-
"""Tests for the FastAPI human-in-the-loop example."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from httpx import ASGITransport, AsyncClient

from agentscope.event import (
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    RequireExternalExecutionEvent,
    UserInterruptEvent,
)
from agentscope.message import Msg, ToolCallBlock
from agentscope.types import ReplyFinishedReason
from examples.fastapi_hitl.main import (  # pylint: disable=import-error
    AgentLike,
    create_app,
)


class FakeAgent:  # pylint: disable=too-few-public-methods
    """A deterministic agent that exposes parking and cancellation."""

    def __init__(self) -> None:
        self.inputs: list[Any] = []
        self.started = asyncio.Event()

    async def reply_stream(self, inputs: Any) -> AsyncIterator[Any]:
        """Yield events matching the kind of input received."""
        self.inputs.append(inputs)
        if isinstance(inputs, Msg) and inputs.get_text_content() == "wait":
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                yield ReplyEndEvent(
                    session_id="test",
                    reply_id="active-reply",
                    finished_reason=ReplyFinishedReason.INTERRUPTED,
                )
            return

        if isinstance(inputs, Msg) and inputs.get_text_content() == "plan":
            yield RequireExternalExecutionEvent(
                reply_id="parked-reply",
                tool_calls=[
                    ToolCallBlock(
                        id="ask-1",
                        name="AskUser",
                        input='{"questions": []}',
                    ),
                ],
            )
            return

        reason = (
            ReplyFinishedReason.INTERRUPTED
            if isinstance(inputs, UserInterruptEvent)
            else ReplyFinishedReason.COMPLETED
        )
        yield ReplyEndEvent(
            session_id="test",
            reply_id=getattr(inputs, "reply_id", "reply"),
            finished_reason=reason,
        )


class FastApiHitlTest(IsolatedAsyncioTestCase):
    """Exercise the API without calling a live model."""

    async def asyncSetUp(self) -> None:
        """Create an app with one fake agent per session."""
        self.agents: dict[str, FakeAgent] = {}

        def factory(session_id: str) -> AgentLike:
            self.agents[session_id] = FakeAgent()
            return self.agents[session_id]

        self.client = AsyncClient(
            transport=ASGITransport(app=create_app(factory)),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def test_confirm_resumes_same_agent(self) -> None:
        """An AskUser answer resumes the parked reply and same agent."""
        response = await self.client.post(
            "/chat",
            json={"session_id": "one", "message": "plan"},
        )
        self.assertEqual(response.status_code, 200)

        response = await self.client.post(
            "/confirm/one",
            json={
                "tool_call_id": "ask-1",
                "answers": [
                    {
                        "question": "Proceed?",
                        "selected": ["Approve"],
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        resumed = self.agents["one"].inputs[-1]
        self.assertIsInstance(resumed, ExternalExecutionResultEvent)
        self.assertEqual(
            resumed.execution_results[0].metadata["answers"][0]["selected"],
            ["Approve"],
        )

        response = await self.client.post(
            "/chat",
            json={"session_id": "one", "message": "new information"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.agents), 1)

    async def test_parked_reply_can_be_interrupted(self) -> None:
        """Interrupting a parked reply allows a later chat turn."""
        await self.client.post(
            "/chat",
            json={"session_id": "one", "message": "plan"},
        )

        response = await self.client.post("/interrupt/one")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "interrupted")
        self.assertIsInstance(
            self.agents["one"].inputs[-1],
            UserInterruptEvent,
        )

        response = await self.client.post(
            "/chat",
            json={"session_id": "one", "message": "changed requirements"},
        )
        self.assertEqual(response.status_code, 200)

    async def test_unknown_session_is_not_created_by_interrupt(self) -> None:
        """Control endpoints return 404 instead of creating an agent."""
        response = await self.client.post("/interrupt/missing")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("missing", self.agents)

    async def test_active_reply_is_cancelled_and_overlaps_are_rejected(
        self,
    ) -> None:
        """Only the interrupt endpoint can enter a busy session."""
        chat_task = asyncio.create_task(
            self.client.post(
                "/chat",
                json={"session_id": "one", "message": "wait"},
            ),
        )
        while "one" not in self.agents:
            await asyncio.sleep(0)
        await self.agents["one"].started.wait()

        overlap = await self.client.post(
            "/chat",
            json={"session_id": "one", "message": "overlap"},
        )
        self.assertEqual(overlap.status_code, 409)

        interrupt = await self.client.post("/interrupt/one")
        self.assertEqual(interrupt.status_code, 200)
        self.assertEqual(interrupt.json()["status"], "interrupt_requested")

        response = await chat_task
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["events"][-1]["finished_reason"],
            "interrupted",
        )
