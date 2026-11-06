# -*- coding: utf-8 -*-
"""Bounds validation for middleware and MCP config fields.

Several tunable numeric fields accepted non-positive values that then
behaved silently (or reversed a slice). These cases pin the lower bounds
down.
"""
from unittest import TestCase

from pydantic import ValidationError

from agentscope.mcp import HttpMCPConfig
from agentscope.middleware import AgenticMemoryMiddleware, ReMeMiddleware


class AgenticMemoryBoundsTest(TestCase):
    """The four token/file caps must be non-negative."""

    def test_defaults_are_non_negative(self) -> None:
        """The defaults already satisfy the bounds."""
        params = AgenticMemoryMiddleware.Parameters()
        self.assertEqual(params.memory_max_tokens, 4000)
        self.assertEqual(params.retrieval_max_tokens_per_md, 2000)
        self.assertEqual(params.retrieval_max_files, 200)
        self.assertEqual(params.retrieval_max_tokens_per_frontmatter, 256)

    def test_negative_is_rejected(self) -> None:
        """A negative cap would silently drop or reverse the selection."""
        for field in (
            "memory_max_tokens",
            "retrieval_max_tokens_per_md",
            "retrieval_max_files",
            "retrieval_max_tokens_per_frontmatter",
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    AgenticMemoryMiddleware.Parameters(**{field: -1})

    def test_zero_is_accepted(self) -> None:
        """Zero means "disabled" and is a valid non-negative value."""
        params = AgenticMemoryMiddleware.Parameters(
            memory_max_tokens=0,
            retrieval_max_tokens_per_md=0,
            retrieval_max_files=0,
            retrieval_max_tokens_per_frontmatter=0,
        )
        self.assertEqual(params.retrieval_max_files, 0)


class ReMeTopKBoundsTest(TestCase):
    """``top_k`` must be at least 1."""

    def test_default_is_positive(self) -> None:
        """The default already satisfies the bound."""
        self.assertEqual(ReMeMiddleware.Parameters().top_k, 5)

    def test_zero_is_rejected(self) -> None:
        """A zero top-k would silently return no memories."""
        with self.assertRaises(ValidationError):
            ReMeMiddleware.Parameters(top_k=0)

    def test_negative_is_rejected(self) -> None:
        """A negative top-k is nonsensical."""
        with self.assertRaises(ValidationError):
            ReMeMiddleware.Parameters(top_k=-1)

    def test_positive_is_accepted(self) -> None:
        """A positive top-k passes."""
        self.assertEqual(ReMeMiddleware.Parameters(top_k=1).top_k, 1)


class HttpMCPTimeoutBoundsTest(TestCase):
    """``timeout`` must be positive when set."""

    def test_default_is_positive(self) -> None:
        """The default already satisfies the bound."""
        self.assertEqual(HttpMCPConfig(url="http://x").timeout, 30.0)

    def test_none_is_accepted(self) -> None:
        """An unset timeout falls back to the transport default."""
        self.assertIsNone(HttpMCPConfig(url="http://x", timeout=None).timeout)

    def test_zero_is_rejected(self) -> None:
        """A zero timeout is invalid for an HTTP request."""
        with self.assertRaises(ValidationError):
            HttpMCPConfig(url="http://x", timeout=0)

    def test_negative_is_rejected(self) -> None:
        """A negative timeout is invalid."""
        with self.assertRaises(ValidationError):
            HttpMCPConfig(url="http://x", timeout=-1)

    def test_positive_is_accepted(self) -> None:
        """A positive timeout passes."""
        self.assertEqual(
            HttpMCPConfig(url="http://x", timeout=1.5).timeout,
            1.5,
        )
