# -*- coding: utf-8 -*-
"""Tests for the configurable timestamp factory."""
import re
from datetime import datetime, timezone
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope import set_timestamp_factory
from agentscope.event import ReplyStartEvent, EventType
from agentscope.message import Msg, TextBlock, ThinkingBlock

_UTC_Z_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
)


class TimestampFactoryTest(IsolatedAsyncioTestCase):
    """Tests for UTC timestamp generation."""

    async def asyncSetUp(self) -> None:
        """Save the current factory before each test."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        self._saved_factory = common._timestamp_factory

    async def test_default_timestamp_is_utc_z(self) -> None:
        """Default timestamps end with Z and parse as UTC."""
        block = ThinkingBlock(thinking="hello")
        self.assertRegex(block.created_at, _UTC_Z_RE)

        msg = Msg(name="test", content=[TextBlock(text="hi")], role="user")
        self.assertRegex(msg.created_at, _UTC_Z_RE)

        event = ReplyStartEvent(
            session_id="s1",
            reply_id="r1",
            name="agent",
        )
        self.assertRegex(event.created_at, _UTC_Z_RE)
        self.assertEqual(event.type, EventType.REPLY_START)

    async def test_custom_factory(self) -> None:
        """``set_timestamp_factory`` overrides entity timestamps."""
        set_timestamp_factory(lambda: "2026-01-01T00:00:00Z")

        block = ThinkingBlock(thinking="hello")
        self.assertEqual(block.created_at, "2026-01-01T00:00:00Z")

    async def test_default_timestamp_near_utc_now(self) -> None:
        """Default factory emits a time close to current UTC."""
        before = datetime.now(timezone.utc)
        block = ThinkingBlock(thinking="tick")
        after = datetime.now(timezone.utc)
        parsed = datetime.fromisoformat(block.created_at.replace("Z", "+00:00"))
        self.assertGreaterEqual(parsed, before.replace(microsecond=0))
        self.assertLessEqual(parsed, after)

    async def asyncTearDown(self) -> None:
        """Restore the original factory after each test."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        common._timestamp_factory = self._saved_factory
