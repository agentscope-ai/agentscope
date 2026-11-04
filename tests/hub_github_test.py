# -*- coding: utf-8 -*-
"""GitHub MCP registry card-building test case, without any network."""
from unittest import TestCase

from agentscope.app._service import render_mcp
from agentscope.app.hub import GitHubMCPHub


class GitHubCardTest(TestCase):
    """Turning a registry entry into an ``MCPCard``."""

    def setUp(self) -> None:
        """Build a hub; nothing here makes a request."""
        self.hub = GitHubMCPHub()

    def _card(self, server: dict, github: dict | None = None) -> object:
        """Build a card from a registry entry with sane defaults."""
        # pylint: disable=protected-access
        return self.hub._to_card(
            {
                "server": {"id": "abc", "name": "acme/thing", **server},
                "x-github": github or {},
            },
        )

    def test_remote_becomes_an_http_config(self) -> None:
        """A ``remotes`` entry is reachable over HTTP, so not stateful."""
        card = self._card(
            {"remotes": [{"transport_type": "streamable-http", "url": "u"}]},
        )

        self.assertEqual(card.config_template.type, "http_mcp")
        self.assertFalse(card.is_stateful)
        self.assertEqual(card.auth, "none")

    def test_secret_header_becomes_an_input(self) -> None:
        """A ``{VAR}`` placeholder is declared so the user must fill it."""
        card = self._card(
            {
                "remotes": [
                    {
                        "url": "u",
                        "headers": [
                            {
                                "value": "Bearer {TOKEN}",
                                "is_secret": True,
                                "description": "The token.",
                            },
                        ],
                    },
                ],
            },
        )

        self.assertEqual(card.auth, "inputs")
        prop = card.inputs_schema["properties"]["TOKEN"]
        self.assertTrue(prop["writeOnly"])
        self.assertEqual(prop["format"], "password")
        # Rewritten to the ``${...}`` form the renderer understands.
        headers = card.config_template.headers
        self.assertEqual(headers["Authorization"], "Bearer ${TOKEN}")
        client = render_mcp(card, {"TOKEN": "sk"})
        self.assertEqual(
            client.mcp_config.headers["Authorization"],
            "Bearer sk",
        )

    def test_package_becomes_a_stdio_config(self) -> None:
        """A runnable package is a local process, so stateful."""
        card = self._card(
            {
                "packages": [
                    {"name": "pkg", "version": "1.2.3", "runtime_hint": "npx"},
                ],
            },
        )

        self.assertEqual(card.config_template.type, "stdio_mcp")
        self.assertEqual(card.config_template.command, "npx")
        self.assertEqual(card.config_template.args, ["-y", "pkg@1.2.3"])
        self.assertTrue(card.is_stateful)

    def test_secret_env_var_becomes_an_input(self) -> None:
        """Package secrets are declared the same way header ones are."""
        card = self._card(
            {
                "packages": [
                    {
                        "name": "pkg",
                        "runtime_hint": "uvx",
                        "environment_variables": [
                            {
                                "name": "API_KEY",
                                "is_required": True,
                                "is_secret": True,
                            },
                        ],
                    },
                ],
            },
        )

        self.assertIn("API_KEY", card.inputs_schema["properties"])
        self.assertEqual(card.config_template.env["API_KEY"], "${API_KEY}")

    def test_required_non_secret_env_var_becomes_an_input(self) -> None:
        """Required configuration is collected even when it is not secret."""
        card = self._card(
            {
                "packages": [
                    {
                        "name": "pkg",
                        "runtime_hint": "uvx",
                        "environment_variables": [
                            {
                                "name": "DT_ENVIRONMENT",
                                "description": "Dynatrace URL",
                                "is_required": True,
                            },
                        ],
                    },
                ],
            },
        )

        prop = card.inputs_schema["properties"]["DT_ENVIRONMENT"]
        self.assertFalse(prop.get("writeOnly", False))
        self.assertIn("DT_ENVIRONMENT", card.inputs_schema["required"])
        client = render_mcp(card, {"DT_ENVIRONMENT": "https://example.com"})
        self.assertEqual(
            client.mcp_config.env["DT_ENVIRONMENT"],
            "https://example.com",
        )

    def test_default_env_input_is_rendered_when_omitted(self) -> None:
        """Registry defaults become effective values for API callers."""
        card = self._card(
            {
                "packages": [
                    {
                        "name": "pkg",
                        "runtime_hint": "uvx",
                        "environment_variables": [
                            {
                                "name": "MODE",
                                "default": "safe",
                            },
                        ],
                    },
                ],
            },
        )

        prop = card.inputs_schema["properties"]["MODE"]
        self.assertEqual(prop["default"], "safe")
        client = render_mcp(card, {})
        self.assertEqual(client.mcp_config.env["MODE"], "safe")

    def test_nested_env_template_uses_declared_input(self) -> None:
        """Registry ``value`` templates name inputs independently of env."""
        card = self._card(
            {
                "packages": [
                    {
                        "name": "firecrawl-mcp",
                        "runtime_hint": "npx",
                        "environment_variables": [
                            {
                                "name": "FIRECRAWL_API_KEY",
                                "value": "{api_key}",
                                "variables": {
                                    "api_key": {
                                        "description": "your API key",
                                        "is_required": True,
                                        "is_secret": True,
                                    },
                                },
                            },
                        ],
                    },
                ],
            },
        )

        self.assertEqual(
            card.config_template.env["FIRECRAWL_API_KEY"],
            "${api_key}",
        )
        self.assertTrue(
            card.inputs_schema["properties"]["api_key"]["writeOnly"],
        )
        client = render_mcp(card, {"api_key": "fc-secret"})
        self.assertEqual(
            client.mcp_config.env["FIRECRAWL_API_KEY"],
            "fc-secret",
        )

    def test_literal_env_value_is_preserved(self) -> None:
        """A registry-provided value is a literal, not an install input."""
        card = self._card(
            {
                "packages": [
                    {
                        "name": "sendmux-mcp",
                        "runtime_hint": "uvx",
                        "environment_variables": [
                            {
                                "name": "SENDMUX_MCP_SURFACES",
                                "value": "mailbox,management,sending",
                                "is_required": True,
                            },
                        ],
                    },
                ],
            },
        )

        self.assertEqual(
            card.config_template.env["SENDMUX_MCP_SURFACES"],
            "mailbox,management,sending",
        )

    def test_unrunnable_package_is_skipped(self) -> None:
        """Around a third of the catalog names a package without saying
        how to run it; listing it would offer an install that cannot
        work."""
        self.assertIsNone(self._card({"packages": [{"name": "pkg"}]}))
        self.assertIsNone(self._card({}))

    def test_remote_wins_over_package(self) -> None:
        """Reaching a hosted endpoint beats spawning a local process."""
        card = self._card(
            {
                "remotes": [{"url": "u"}],
                "packages": [{"name": "p", "runtime_hint": "npx"}],
            },
        )

        self.assertEqual(card.config_template.type, "http_mcp")

    def test_name_is_slugged_for_the_mcp_client(self) -> None:
        """Registry names carry ``/`` and ``.``; client names may not."""
        card = self._card(
            {"name": "io.github.upstash/context7", "remotes": [{"url": "u"}]},
        )

        self.assertEqual(card.name, "context7")
        self.assertEqual(card.id, "abc")

    def test_display_fields_come_from_the_github_block(self) -> None:
        """Author, icon and language ride along on ``x-github``."""
        card = self._card(
            {
                "remotes": [{"url": "u"}],
                "updated_at": "2026-01-21T09:35:10Z",
                "repository": {"url": "https://github.com/acme/thing"},
                "version_detail": {"version": "1.0.0"},
            },
            {
                "name_with_owner": "acme/thing",
                "preferred_image": "https://avatars/acme",
                "primary_language": "Python",
                "display_name": "Thing",
            },
        )

        self.assertEqual(card.author, "acme")
        self.assertEqual(card.icon_url, "https://avatars/acme")
        self.assertEqual(card.tags, ["Python"])
        self.assertEqual(card.display_name, "Thing")
        self.assertEqual(card.url, "https://github.com/acme/thing")
        self.assertEqual(card.version, "1.0.0")
        self.assertIsNotNone(card.updated_at)

    def test_bad_timestamp_is_dropped_not_fatal(self) -> None:
        """A malformed date must not sink the whole card."""
        card = self._card({"remotes": [{"url": "u"}], "updated_at": "soon"})

        self.assertIsNone(card.updated_at)
