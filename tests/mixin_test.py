# -*- coding: utf-8 -*-
"""Unit tests for dictionary-backed response attribute access."""
from copy import deepcopy
from unittest import TestCase

from agentscope.message import TextBlock
from agentscope.model import ChatResponse


class DictMixinTest(TestCase):
    """Test response behavior provided by ``DictMixin``."""

    def test_chat_response_can_be_deep_copied(self) -> None:
        """A chat response can be copied without sharing mutable fields."""
        response = ChatResponse(
            content=[TextBlock(text="hello")],
            is_last=True,
        )

        copied = deepcopy(response)

        self.assertEqual(copied, response)
        self.assertIsNot(copied, response)
        self.assertIsNot(copied.content, response.content)
        self.assertIsNot(copied.content[0], response.content[0])

    def test_missing_attributes_follow_attribute_protocol(self) -> None:
        """Missing attributes use normal Python attribute semantics."""
        response = ChatResponse(content=[], is_last=True)

        self.assertFalse(hasattr(response, "missing"))
        self.assertEqual(
            getattr(response, "missing", "fallback"),
            "fallback",
        )
        with self.assertRaises(AttributeError):
            getattr(response, "missing")

        with self.assertRaises(KeyError):
            _ = response["missing"]
