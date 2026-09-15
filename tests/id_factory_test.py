# -*- coding: utf-8 -*-
"""Tests for the configurable ID factory."""
import re
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope import set_id_factory
from agentscope.message import Msg, TextBlock

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


class IdFactoryTest(IsolatedAsyncioTestCase):
    """Tests for set_id_factory."""

    async def asyncSetUp(self) -> None:
        """Save the current factory before each test."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        self._saved_factory = common._id_factory

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

    async def asyncTearDown(self) -> None:
        """Restore the original factory after each test."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        common._id_factory = self._saved_factory
