# -*- coding: utf-8 -*-
"""The utility module for unit tests in agentscope."""


class AnyString:
    """A helper class for asserting any string value in unit tests."""

    def __eq__(self, other: object) -> bool:
        """Override equality check to match any string."""
        return isinstance(other, str)

    def __repr__(self) -> str:
        """Return a string representation for debugging purposes."""
        return "<AnyString>"
