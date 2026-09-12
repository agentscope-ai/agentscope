# -*- coding: utf-8 -*-
"""Hub card identity test case."""
from unittest import TestCase

from agentscope.app.hub import HubBase, MCPCard, SkillCard

HTTP_CONFIG = {"type": "http_mcp", "url": "https://example.com/sse"}


class HubIdentityTest(TestCase):
    """Hub id validation."""

    def test_accepts_plain_id(self) -> None:
        """A ``[a-zA-Z0-9_-]+`` id is accepted."""
        hub = HubBase("claw_hub-1", "ClawHub")

        self.assertEqual(hub.hub_id, "claw_hub-1")
        self.assertEqual(hub.description, "")

    def test_rejects_colon(self) -> None:
        """A colon is not usable in a route path segment."""
        with self.assertRaises(ValueError):
            HubBase("claw:hub", "ClawHub")

    def test_rejects_empty(self) -> None:
        """An empty id is rejected."""
        with self.assertRaises(ValueError):
            HubBase("", "ClawHub")


class CardIdentityTest(TestCase):
    """Card id defaulting and cross-hub disambiguation."""

    def test_mcp_id_defaults_to_name(self) -> None:
        """A hub exposing no separate id falls back to the name."""
        card = MCPCard(
            hub_id="testhub",
            name="notion",
            config_template=HTTP_CONFIG,
        )

        self.assertEqual(card.id, "notion")

    def test_mcp_id_kept_when_given(self) -> None:
        """An explicit id is preserved independently of the name."""
        card = MCPCard(
            hub_id="testhub",
            id="srv_01ab",
            name="notion",
            config_template=HTTP_CONFIG,
        )

        self.assertEqual(card.id, "srv_01ab")
        self.assertEqual(card.name, "notion")

    def test_skill_id_defaults_to_name(self) -> None:
        """The skill card follows the same defaulting rule."""
        card = SkillCard(hub_id="clawhub", name="gifgrep")

        self.assertEqual(card.id, "gifgrep")

    def test_opaque_id_with_colon(self) -> None:
        """A card id may contain a colon — ClawHub's search ids do.

        This is why the global handle stays the ``(hub_id, id)`` pair
        rather than a joined string, which could not be split back.
        """
        card = SkillCard(
            hub_id="clawhub",
            id="clawhub:kd75911hf60t9b6fr5pn61x0fx80t3xw",
            name="git",
        )

        self.assertEqual(
            card.id,
            "clawhub:kd75911hf60t9b6fr5pn61x0fx80t3xw",
        )

    def test_same_name_across_hubs_differs(self) -> None:
        """The pair is what disambiguates a shared name."""
        a = SkillCard(hub_id="clawhub", name="git")
        b = SkillCard(hub_id="otherhub", name="git")

        self.assertEqual(a.name, b.name)
        self.assertNotEqual((a.hub_id, a.id), (b.hub_id, b.id))

    def test_rename_keeps_provenance(self) -> None:
        """Renaming a card touches ``name`` only; the source survives."""
        card = MCPCard(
            hub_id="clawhub",
            name="git",
            config_template=HTTP_CONFIG,
        )

        card.name = "git-2"

        self.assertEqual((card.hub_id, card.id), ("clawhub", "git"))
