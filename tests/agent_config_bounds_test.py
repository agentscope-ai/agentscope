# -*- coding: utf-8 -*-
"""Bounds validation for the agent config fields.

``ReActConfig.max_iters`` and ``ContextConfig.tool_result_limit`` are
positive-semantic counts. A non-positive value must fail validation
instead of silently corrupting the reasoning loop or the tool-result
truncation, so these cases pin the lower bounds down.
"""
from unittest import TestCase

from pydantic import ValidationError

from agentscope.agent._config import ContextConfig, ReActConfig


class MaxItersBoundsTest(TestCase):
    """``max_iters`` must be at least 1."""

    def test_default_is_positive(self) -> None:
        """The default already satisfies the bound."""
        self.assertEqual(ReActConfig().max_iters, 50)

    def test_zero_is_rejected(self) -> None:
        """A zero iteration budget would end the reply before reasoning."""
        with self.assertRaises(ValidationError):
            ReActConfig(max_iters=0)

    def test_negative_is_rejected(self) -> None:
        """A negative budget is nonsensical."""
        with self.assertRaises(ValidationError):
            ReActConfig(max_iters=-1)

    def test_positive_is_accepted(self) -> None:
        """A positive budget passes."""
        self.assertEqual(ReActConfig(max_iters=1).max_iters, 1)


class ToolResultLimitBoundsTest(TestCase):
    """``tool_result_limit`` must be at least 1."""

    def test_default_is_positive(self) -> None:
        """The default already satisfies the bound."""
        self.assertEqual(ContextConfig().tool_result_limit, 50000)

    def test_zero_is_rejected(self) -> None:
        """A zero limit would truncate every tool result."""
        with self.assertRaises(ValidationError):
            ContextConfig(tool_result_limit=0)

    def test_negative_is_rejected(self) -> None:
        """A negative limit routes every result into truncation."""
        with self.assertRaises(ValidationError):
            ContextConfig(tool_result_limit=-1)

    def test_positive_is_accepted(self) -> None:
        """A positive limit passes."""
        self.assertEqual(
            ContextConfig(tool_result_limit=1).tool_result_limit,
            1,
        )
