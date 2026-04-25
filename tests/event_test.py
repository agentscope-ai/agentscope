# -*- coding: utf-8 -*-
"""Event test"""
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString
from agentscope.event import ReplyStartEvent


class EventTest(IsolatedAsyncioTestCase):
    """The event test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""

    async def test_model_dump(self) -> None:
        """Test model dump."""
        event = ReplyStartEvent(
            session_id="test_session",
            reply_id="test_reply",
            name="Friday",
        ).model_dump()
        self.assertDictEqual(
            event,
            {
                "type": "REPLY_START",
                "id": AnyString(),
                "created_at": AnyString(),
                "session_id": "test_session",
                "reply_id": "test_reply",
                "name": "Friday",
                "role": "assistant",
            },
        )
        self.assertIsInstance(event["type"], str)

    async def test_model_validate(self) -> None:
        """Test model validate."""
        data = {
            "type": "REPLY_START",
            "id": "test_id",
            "created_at": "2024-01-01T00:00:00",
            "session_id": "test_session",
            "reply_id": "test_reply",
            "name": "Friday",
            "role": "assistant",
        }
        ReplyStartEvent.model_validate(data)

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
