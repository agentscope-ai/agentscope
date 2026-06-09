# -*- coding: utf-8 -*-
"""Tests for the configurable ID factory."""
import re
import unittest

from agentscope._utils._common import (
    _generate_id,
    set_id_factory,
)
from agentscope import set_id_factory as public_set_id_factory


_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


class IdFactoryTest(unittest.TestCase):
    """Tests for set_id_factory and _generate_id."""

    def setUp(self) -> None:
        """Save the current factory before each test."""
        import agentscope._utils._common as common

        self._saved_factory = common._id_factory

    def tearDown(self) -> None:
        """Restore the original factory after each test."""
        import agentscope._utils._common as common

        common._id_factory = self._saved_factory

    def test_default_factory_returns_hex32(self) -> None:
        """Default _generate_id() returns a 32-char hex string."""
        id1 = _generate_id()
        id2 = _generate_id()
        self.assertRegex(id1, _HEX32_RE)
        self.assertRegex(id2, _HEX32_RE)
        self.assertNotEqual(id1, id2, "Default IDs should be unique")

    def test_custom_factory_takes_effect(self) -> None:
        """Custom factory is used by _generate_id()."""
        counter = [0]

        def my_factory() -> str:
            counter[0] += 1
            return f"custom-{counter[0]}"

        set_id_factory(my_factory)
        self.assertEqual(_generate_id(), "custom-1")
        self.assertEqual(_generate_id(), "custom-2")

    def test_set_id_factory_rejects_non_callable(self) -> None:
        """set_id_factory raises TypeError for non-callable."""
        with self.assertRaises(TypeError):
            set_id_factory("not a callable")  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            set_id_factory(None)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            set_id_factory(42)  # type: ignore[arg-type]

    def test_public_api_matches_internal(self) -> None:
        """Public export from __init__.py is the same function."""
        self.assertIs(public_set_id_factory, set_id_factory)

    def test_custom_factory_affects_entities(self) -> None:
        """Custom factory affects Msg, TextBlock, and other entities."""
        from agentscope.message import Msg, TextBlock

        set_id_factory(lambda: "test-entity-id")

        msg = Msg(
            name="test",
            content=[TextBlock(text="hello")],
            role="user",
        )
        self.assertEqual(msg.id, "test-entity-id")
        self.assertEqual(msg.content[0].id, "test-entity-id")
