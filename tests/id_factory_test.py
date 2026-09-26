# -*- coding: utf-8 -*-
"""Tests for the configurable ID and timestamp factories."""
import os
import re
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope import set_id_factory, set_timestamp_factory
from agentscope.event import ReplyStartEvent
from agentscope.message import (
    AssistantMsg,
    Msg,
    SystemMsg,
    TextBlock,
    UserMsg,
)
from agentscope.model import ChatResponse, StructuredResponse
from agentscope.state import Task

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


class IdFactoryTest(IsolatedAsyncioTestCase):
    """Tests for set_id_factory and set_timestamp_factory."""

    async def asyncSetUp(self) -> None:
        """Save the current factories before each test."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        self._saved_factory = common._id_factory
        self._saved_timestamp_factory = common._timestamp_factory

    async def test_default_id_factory_returns_hex32(self) -> None:
        """The default ID factory returns uuid.uuid4().hex."""
        msg = Msg(
            name="test",
            content=[TextBlock(text="hello")],
            role="user",
        )
        self.assertRegex(msg.id, _HEX32_RE)
        self.assertRegex(msg.content[0].id, _HEX32_RE)

    async def test_custom_factory_affects_entities(self) -> None:
        """After ``set_id_factory``, entities use the custom factory."""
        set_id_factory(lambda: "custom-entity-id")

        msg = Msg(
            name="test",
            content=[TextBlock(text="hello")],
            role="user",
        )
        self.assertEqual(msg.id, "custom-entity-id")
        self.assertEqual(msg.content[0].id, "custom-entity-id")

    async def test_custom_timestamp_factory_affects_entities(self) -> None:
        """After ``set_timestamp_factory``, entities use the custom factory."""
        set_timestamp_factory(lambda: "custom-timestamp")

        msg = Msg(name="test", content=[TextBlock(text="hello")], role="user")
        self.assertDictEqual(
            {
                "block": msg.content[0].created_at,
                "msg": msg.created_at,
                "user_msg": UserMsg(name="test", content="hello").created_at,
                "assistant_msg": AssistantMsg(
                    name="test",
                    content="hello",
                ).created_at,
                "system_msg": SystemMsg(
                    name="test",
                    content="hello",
                ).created_at,
                "event": ReplyStartEvent(
                    session_id="s",
                    reply_id="r",
                    name="test",
                ).created_at,
                "task": Task(
                    subject="s",
                    description="d",
                    metadata={},
                ).created_at,
                "chat_response": ChatResponse(
                    content=[],
                    is_last=True,
                ).created_at,
                "structured_response": StructuredResponse(
                    content={},
                ).created_at,
            },
            {
                "block": "custom-timestamp",
                "msg": "custom-timestamp",
                "user_msg": "custom-timestamp",
                "assistant_msg": "custom-timestamp",
                "system_msg": "custom-timestamp",
                "event": "custom-timestamp",
                "task": "custom-timestamp",
                "chat_response": "custom-timestamp",
                "structured_response": "custom-timestamp",
            },
        )

    @unittest.skipIf(
        not hasattr(time, "tzset"),
        "time.tzset is not available on Windows",
    )
    async def test_default_timestamp_factory_is_utc_aware(self) -> None:
        """The default timestamp factory must not emit naive, machine-local
        timestamps: they cause payload-vs-DB skew on non-UTC hosts, because
        code elsewhere anchors naive datetimes to UTC (see
        ``agentscope.app.storage._sql._storage._to_naive_utc``), and a
        different naive string built on a non-UTC host takes on the wrong
        moment in time when compared or sorted against it.
        """
        original_tz = os.environ.get("TZ")
        try:
            # Pick a host timezone far from UTC so a naive-local bug shows
            # up as a multi-hour skew rather than being masked by luck.
            os.environ["TZ"] = "Asia/Shanghai"  # UTC+8
            time.tzset()

            before = datetime.now(timezone.utc)
            msg = Msg(
                name="test",
                content=[TextBlock(text="hello")],
                role="user",
            )
            after = datetime.now(timezone.utc)

            parsed = datetime.fromisoformat(msg.created_at)

            self.assertIsNotNone(
                parsed.tzinfo,
                "Msg.created_at must be timezone-aware, not a naive "
                "machine-local timestamp",
            )
            # The timestamp should represent "now" in UTC, not the
            # UTC+8 wall-clock reading reinterpreted as if it were UTC.
            self.assertLessEqual(before - timedelta(seconds=1), parsed)
            self.assertLessEqual(parsed, after + timedelta(seconds=1))
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

    async def asyncTearDown(self) -> None:
        """Restore the original factories after each test."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        common._id_factory = self._saved_factory
        common._timestamp_factory = self._saved_timestamp_factory
