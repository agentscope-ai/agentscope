# -*- coding: utf-8 -*-
"""Tests for channel routing — the pure ``resolve`` function that maps an
inbound event to ``(agent_id, session_id)``.

Covers: catch-all default, first-match-wins ordering, per_chat vs
per_chat_user session grouping, agent id embedded in the session id, and
determinism across calls.
"""
from unittest import TestCase

from agentscope.app.channel._base import ChannelEvent
from agentscope.app.channel._routing import resolve
from agentscope.app.storage import (
    ChannelBinding,
    ChannelRecord,
    RoutingConfig,
    SessionScope,
    SessionSettings,
)


def _record(bindings: list[ChannelBinding]) -> ChannelRecord:
    return ChannelRecord(
        id="chan-1",
        channel_type="feishu",
        user_id="owner-1",
        routing=RoutingConfig(bindings=bindings),
        session=SessionSettings(chat_model_config={"type": "x"}),
        created_at="t",
        updated_at="t",
    )


def _event(
    chat_id: str = "oc_group",
    user_id: str = "ou_alice",
    metadata: dict | None = None,
) -> ChannelEvent:
    return ChannelEvent(
        channel_id="chan-1",
        channel_user_id=user_id,
        chat_id=chat_id,
        metadata=metadata or {},
    )


class ChannelRoutingTest(TestCase):
    """Unit tests for :func:`resolve`."""

    def test_catch_all_default(self) -> None:
        """A lone catch-all routes everything to its agent."""
        rec = _record(
            [ChannelBinding(match_value="*", agent_id="general")],
        )
        agent_id, session_id, _ = resolve(_event(), rec)
        self.assertEqual(agent_id, "general")
        self.assertTrue(session_id)

    def test_first_match_wins(self) -> None:
        """Earlier rules take precedence over later ones."""
        rec = _record(
            [
                ChannelBinding(
                    match_key="chat_id",
                    match_value="oc_vip",
                    agent_id="vip",
                ),
                ChannelBinding(match_value="*", agent_id="general"),
            ],
        )
        self.assertEqual(resolve(_event(chat_id="oc_vip"), rec)[0], "vip")
        self.assertEqual(resolve(_event(chat_id="oc_x"), rec)[0], "general")

    def test_match_on_metadata_key(self) -> None:
        """match_key may reference an event.metadata field."""
        rec = _record(
            [
                ChannelBinding(
                    match_key="chat_type",
                    match_value="p2p",
                    agent_id="assistant",
                ),
                ChannelBinding(match_value="*", agent_id="general"),
            ],
        )
        agent_id, _, _ = resolve(
            _event(metadata={"chat_type": "p2p"}),
            rec,
        )
        self.assertEqual(agent_id, "assistant")

    def test_per_chat_shares_across_users(self) -> None:
        """PER_CHAT: different users in the same chat share a session."""
        rec = _record(
            [
                ChannelBinding(
                    match_value="*",
                    agent_id="a",
                    session_scope=SessionScope.PER_CHAT,
                ),
            ],
        )
        s_alice = resolve(_event(user_id="ou_alice"), rec)[1]
        s_bob = resolve(_event(user_id="ou_bob"), rec)[1]
        self.assertEqual(s_alice, s_bob)

    def test_per_chat_user_isolates_users(self) -> None:
        """PER_CHAT_USER: users in the same chat get distinct sessions."""
        rec = _record(
            [
                ChannelBinding(
                    match_value="*",
                    agent_id="a",
                    session_scope=SessionScope.PER_CHAT_USER,
                ),
            ],
        )
        s_alice = resolve(_event(user_id="ou_alice"), rec)[1]
        s_bob = resolve(_event(user_id="ou_bob"), rec)[1]
        self.assertNotEqual(s_alice, s_bob)

    def test_different_chats_distinct_sessions(self) -> None:
        """PER_CHAT: different chats never collide."""
        rec = _record(
            [ChannelBinding(match_value="*", agent_id="a")],
        )
        s1 = resolve(_event(chat_id="oc_1"), rec)[1]
        s2 = resolve(_event(chat_id="oc_2"), rec)[1]
        self.assertNotEqual(s1, s2)

    def test_agent_embedded_in_session_id(self) -> None:
        """Same scope key but different agents → different sessions."""
        rec_a = _record([ChannelBinding(match_value="*", agent_id="a")])
        rec_b = _record([ChannelBinding(match_value="*", agent_id="b")])
        s_a = resolve(_event(), rec_a)[1]
        s_b = resolve(_event(), rec_b)[1]
        self.assertNotEqual(s_a, s_b)

    def test_deterministic(self) -> None:
        """resolve is a pure function — repeated calls agree."""
        rec = _record([ChannelBinding(match_value="*", agent_id="a")])
        self.assertEqual(resolve(_event(), rec), resolve(_event(), rec))

    def test_routing_requires_catch_all(self) -> None:
        """RoutingConfig rejects a rule set without a catch-all."""
        with self.assertRaises(ValueError):
            RoutingConfig(
                bindings=[
                    ChannelBinding(match_value="oc_1", agent_id="a"),
                ],
            )

    def test_routing_catch_all_must_be_last(self) -> None:
        """A catch-all before other rules is rejected (unreachable)."""
        with self.assertRaises(ValueError):
            RoutingConfig(
                bindings=[
                    ChannelBinding(match_value="*", agent_id="a"),
                    ChannelBinding(match_value="oc_1", agent_id="b"),
                ],
            )

    def test_routing_rejects_duplicates(self) -> None:
        """Duplicate (match_key, match_value) pairs are rejected."""
        with self.assertRaises(ValueError):
            RoutingConfig(
                bindings=[
                    ChannelBinding(match_value="oc_1", agent_id="a"),
                    ChannelBinding(match_value="oc_1", agent_id="b"),
                    ChannelBinding(match_value="*", agent_id="c"),
                ],
            )
