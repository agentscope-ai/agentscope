# -*- coding: utf-8 -*-
"""Event test"""
from unittest.async_case import IsolatedAsyncioTestCase

from pydantic import ValidationError

from utils import AnyString
from agentscope.event import ReplyStartEvent, ToolResultDataDeltaEvent


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
                "metadata": {},
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

    async def test_tool_result_data_delta_source_validation(self) -> None:
        """Test exactly one data source is required."""
        common = {
            "reply_id": "test_reply",
            "tool_call_id": "test_tool_call",
            "media_type": "image/png",
        }

        data_event = ToolResultDataDeltaEvent(
            **common,
            data="iVBOR==",
        )
        self.assertEqual(data_event.data, "iVBOR==")
        self.assertIsNone(data_event.url)

        url_event = ToolResultDataDeltaEvent(
            **common,
            url="https://example.com/image.png",
        )
        self.assertIsNone(url_event.data)
        self.assertEqual(url_event.url, "https://example.com/image.png")

        for invalid_source in (
            {},
            {
                "data": "iVBOR==",
                "url": "https://example.com/image.png",
            },
        ):
            with self.subTest(source=invalid_source):
                with self.assertRaisesRegex(
                    ValidationError,
                    "Exactly one of",
                ):
                    ToolResultDataDeltaEvent(
                        **common,
                        **invalid_source,
                    )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
