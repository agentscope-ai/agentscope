# -*- coding: utf-8 -*-
"""Tests for the configurable ID and timestamp factories."""
import re
from datetime import datetime
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
from agentscope.model import ChatResponse
from agentscope.state import Task

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class IdFactoryTest(IsolatedAsyncioTestCase):
    """Tests for set_id_factory."""

    async def asyncSetUp(self) -> None:
        """Save the current factory before each test."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        self._saved_factory = common._id_factory
        # pylint: disable=protected-access
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

    async def test_timestamp_factory_covers_all_timestamp_fields(self) -> None:
        """``set_timestamp_factory`` reaches every entity that stamps itself.

        Content blocks honoured the factory while the message, event, task
        and response entities hard-coded ``datetime.now()``, so a single
        message mixed overridden block timestamps with wall-clock
        ``created_at`` values.
        """
        set_timestamp_factory(lambda: "custom-timestamp")

        block = TextBlock(text="hello")
        entities = [
            Msg(name="test", content=[block], role="user"),
            UserMsg(name="test", content="hello"),
            AssistantMsg(name="test", content=[block]),
            SystemMsg(name="test", content="hello"),
            ReplyStartEvent(
                session_id="s",
                reply_id="r",
                name="test",
            ),
            Task(subject="s", description="d", metadata={}),
            ChatResponse(content=[block], is_last=True),
        ]
        self.assertEqual(
            [block.created_at] + [entity.created_at for entity in entities],
            ["custom-timestamp"] * (len(entities) + 1),
        )

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

    async def test_default_timestamp_factory_returns_iso8601(self) -> None:
        """The default timestamp factory keeps returning ISO 8601."""
        msg = Msg(
            name="test",
            content=[TextBlock(text="hello")],
            role="user",
        )
        self.assertRegex(msg.created_at, _ISO_RE)
        self.assertIsInstance(
            datetime.fromisoformat(msg.created_at),
            datetime,
        )

    async def asyncTearDown(self) -> None:
        """Restore the original factories after each test."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        common._id_factory = self._saved_factory
        # pylint: disable=protected-access
        common._timestamp_factory = self._saved_timestamp_factory
