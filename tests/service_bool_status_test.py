# -*- coding: utf-8 -*-
"""Regression tests for HTTP status extraction in the error classifier."""
import unittest

from agentscope.app._service._errors import _classify_type, _extract_status
from agentscope.types import ErrorType


class _BoolStatusError(Exception):
    """An exception whose ``status`` is a boolean success flag."""

    def __init__(self) -> None:
        super().__init__("flag")
        self.status = True


class _IntStatusError(Exception):
    """An exception carrying a real HTTP status code."""

    status_code = 429


class BoolStatusTest(unittest.TestCase):
    """Booleans are not HTTP status codes."""

    def test_boolean_status_is_ignored(self) -> None:
        """A boolean ``status`` must not be read as status code 1."""
        error = _BoolStatusError()

        self.assertIsNone(_extract_status(error))
        self.assertEqual(_classify_type(error), ErrorType.UNKNOWN)

    def test_integer_status_is_still_classified(self) -> None:
        """A genuine integer status keeps its classification."""
        self.assertEqual(
            _classify_type(_IntStatusError()),
            ErrorType.RATE_LIMIT,
        )
