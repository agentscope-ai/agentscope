# -*- coding: utf-8 -*-
"""Regression tests for message-bus key construction.

``MessageBusKeys.channel_media_buffer`` composes three externally
sourced channel ids into one Redis queue key. Those ids may contain the
``:`` key separator, so each segment must be encoded before it is
interpolated; otherwise two distinct conversations alias one buffer.
"""
from unittest import TestCase

from agentscope.app.message_bus import MessageBusKeys


class ChannelMediaBufferKeyTest(TestCase):
    """A media buffer key must be unique per (channel, chat, user)."""

    def test_plain_ids_keep_the_legacy_key(self) -> None:
        """Ids without separators produce the exact legacy key."""
        self.assertEqual(
            MessageBusKeys.channel_media_buffer("chan", "chat", "user"),
            "agentscope:channel:media:chan:chat:user",
        )

    def test_colon_in_chat_id_does_not_collide(self) -> None:
        """Two triples differing only in where a colon sits are distinct."""
        first = MessageBusKeys.channel_media_buffer("chan", "a:b", "u")
        second = MessageBusKeys.channel_media_buffer("chan", "a", "b:u")
        self.assertNotEqual(first, second)

    def test_colon_in_user_id_does_not_collide(self) -> None:
        """A colon in the user id must not alias another chat id."""
        first = MessageBusKeys.channel_media_buffer("c", "chat", "a:b")
        second = MessageBusKeys.channel_media_buffer("c", "chat:a", "b")
        self.assertNotEqual(first, second)

    def test_matrix_style_ids_are_isolated(self) -> None:
        """Matrix room and user ids carry colons by design."""
        first = MessageBusKeys.channel_media_buffer(
            "matrix",
            "!room:example.org",
            "@alice:example.org",
        )
        second = MessageBusKeys.channel_media_buffer(
            "matrix",
            "!room",
            "example.org:@alice:example.org",
        )
        self.assertNotEqual(first, second)

    def test_ids_do_not_leak_the_namespace_separator(self) -> None:
        """Only the two structural colons may appear in the key body."""
        key = MessageBusKeys.channel_media_buffer("c", "a:b", "u:v")
        self.assertTrue(key.startswith("agentscope:channel:media:"))
        body = key[len("agentscope:channel:media:") :]
        self.assertEqual(body.count(":"), 2)

    def test_percent_is_escaped_to_stay_injective(self) -> None:
        """A literal percent must not alias an encoded separator."""
        with_colon = MessageBusKeys.channel_media_buffer("c", "a:b", "u")
        with_encoding = MessageBusKeys.channel_media_buffer(
            "c",
            "a%3Ab",
            "u",
        )
        self.assertNotEqual(with_colon, with_encoding)

    def test_unicode_ids_are_isolated(self) -> None:
        """Non-ascii ids are encoded too, so they cannot collide."""
        first = MessageBusKeys.channel_media_buffer(
            "c",
            "群:组",
            "用户",
        )
        second = MessageBusKeys.channel_media_buffer(
            "c",
            "群",
            "组:用户",
        )
        self.assertNotEqual(first, second)
