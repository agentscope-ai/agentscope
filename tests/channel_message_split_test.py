# -*- coding: utf-8 -*-
"""Unit tests for the platform-agnostic long-message splitter."""

# pylint: disable=abstract-method,missing-function-docstring
# pylint: disable=protected-access,unused-argument
from typing import Any, AsyncIterator, Awaitable, Callable
from unittest import TestCase

from agentscope.app.channel._base import ChannelBase, ChannelCapability


class _StubChannel(ChannelBase):
    """A channel whose only declared capability is a message limit."""

    channel_type = "stub"

    def __init__(self, max_message_length: int) -> None:
        self.capabilities = ChannelCapability(
            max_message_length=max_message_length,
        )

    @property
    def channel_id(self) -> str:
        return "stub-1"

    async def start_listening(
        self,
        emit: Callable[..., Awaitable[None]],
    ) -> None:
        del emit

    async def send_response(
        self,
        event: Any,
        events: AsyncIterator[dict],
    ) -> None:
        del event, events


class SplitLongMessageTest(TestCase):
    """A chunk must fit the limit in the unit the platforms count."""

    def test_short_message_is_returned_unchanged(self) -> None:
        channel = _StubChannel(max_message_length=10)
        self.assertEqual(channel._split_long_message("hello"), ["hello"])

    def test_empty_message_stays_one_empty_chunk(self) -> None:
        channel = _StubChannel(max_message_length=10)
        self.assertEqual(channel._split_long_message(""), [""])

    def test_ascii_splits_at_the_limit(self) -> None:
        channel = _StubChannel(max_message_length=4)
        self.assertEqual(
            channel._split_long_message("abcdefghij"),
            ["abcd", "efgh", "ij"],
        )

    def test_emoji_count_as_two_utf16_units(self) -> None:
        """Three emoji are three code points but six UTF-16 units, so with
        a limit of four they cannot travel as a single chunk."""
        channel = _StubChannel(max_message_length=4)

        parts = channel._split_long_message("\U0001F600" * 3)

        self.assertEqual(parts, ["\U0001F600" * 2, "\U0001F600"])
