# -*- coding: utf-8 -*-
"""GitHub MCP registry card-building test case, without any network."""
from unittest import TestCase

from agentscope.app._service import MCPRenderError, render_mcp
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

    def test_url_template_variable_is_rewritten_and_declared(self) -> None:
        """A ``{var}`` in the URL becomes ``${var}`` and an input."""
        card = self._card(
            {
                "remotes": [
                    {
                        "type": "streamable-http",
                        "url": "https://{tenant_id}.analytics.example.com/mcp",
                        "variables": {
                            "tenant_id": {
                                "description": "Your tenant identifier",
                                "isRequired": True,
                            },
                        },
                    },
                ],
            },
        )
        self.assertEqual(
            card.config_template.url,
            "https://${tenant_id}.analytics.example.com/mcp",
        )
        self.assertEqual(card.auth, "inputs")
        prop = card.inputs_schema["properties"]["tenant_id"]
        self.assertEqual(prop["description"], "Your tenant identifier")
        self.assertIn("tenant_id", card.inputs_schema["required"])

    def test_url_variable_is_substituted_by_render_mcp(self) -> None:
        """The rendered client points at the resolved endpoint."""
        card = self._card(
            {
                "remotes": [
                    {
                        "type": "streamable-http",
                        "url": "https://{tenant_id}.analytics.example.com/mcp",
                        "variables": {
                            "tenant_id": {
                                "description": "Tenant",
                                "isRequired": True,
                            },
                        },
                    },
                ],
            },
        )
        client = render_mcp(card, {"tenant_id": "us-cell1"})
        self.assertEqual(
            client.mcp_config.url,
            "https://us-cell1.analytics.example.com/mcp",
        )

    def test_variable_metadata_is_preserved(self) -> None:
        """choices/default/isSecret/isRequired feed the install form."""
        card = self._card(
            {
                "remotes": [
                    {
                        "type": "streamable-http",
                        "url": "https://api.example.com/{region}/mcp",
                        "variables": {
                            "region": {
                                "description": "Deployment region",
                                "isRequired": True,
                                "choices": ["us-east-1", "eu-west-1"],
                                "default": "us-east-1",
                                "isSecret": True,
                            },
                        },
                    },
                ],
            },
        )
        prop = card.inputs_schema["properties"]["region"]
        self.assertEqual(prop["description"], "Deployment region")
        self.assertEqual(prop["enum"], ["us-east-1", "eu-west-1"])
        self.assertEqual(prop["default"], "us-east-1")
        self.assertTrue(prop["writeOnly"])
        self.assertEqual(prop["format"], "password")

    def test_shared_url_and_header_variable_metadata(self) -> None:
        """Shared inputs retain URL metadata and header requirements."""
        for header_secret, url_secret in (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        ):
            with self.subTest(
                header_secret=header_secret,
                url_secret=url_secret,
            ):
                card = self._card(
                    {
                        "remotes": [
                            {
                                "url": "https://{region}.example.com/mcp",
                                "headers": [
                                    {
                                        "name": "X-Region",
                                        "value": "{region}",
                                        "is_secret": header_secret,
                                    },
                                ],
                                "variables": {
                                    "region": {
                                        "description": "Deployment region",
                                        "isRequired": False,
                                        "isSecret": url_secret,
                                        "choices": ["us", "eu"],
                                        "default": "us",
                                    },
                                },
                            },
                        ],
                    },
                )
                expected_property: dict = {
                    "type": "string",
                    "title": "region",
                    "description": "Deployment region",
                    "enum": ["us", "eu"],
                    "default": "us",
                }
                if header_secret or url_secret:
                    expected_property.update(
                        {"writeOnly": True, "format": "password"},
                    )
                self.assertEqual(
                    card.inputs_schema,
                    {
                        "type": "object",
                        "properties": {"region": expected_property},
                        "required": ["region"],
                    },
                )
                client = render_mcp(card, {})
                self.assertEqual(
                    {
                        "url": client.mcp_config.url,
                        "headers": client.mcp_config.headers,
                    },
                    {
                        "url": "https://us.example.com/mcp",
                        "headers": {"X-Region": "us"},
                    },
                )
                with self.assertRaisesRegex(MCPRenderError, "not one of"):
                    render_mcp(card, {"region": "unsupported-region"})

    def test_placeholder_without_variables_still_declared(self) -> None:
        """A bare ``{var}`` with no ``variables`` block is still resolved."""
        card = self._card(
            {
                "remotes": [
                    {
                        "type": "streamable-http",
                        "url": "https://{host}.example.com/mcp",
                    },
                ],
            },
        )
        self.assertEqual(
            card.config_template.url,
            "https://${host}.example.com/mcp",
        )
        self.assertIn("host", card.inputs_schema["properties"])

    def test_plain_url_unchanged(self) -> None:
        """Control: a remote with no variables needs no install inputs."""
        card = self._card(
            {
                "remotes": [
                    {
                        "type": "streamable-http",
                        "url": "https://analytics.example.com/mcp",
                    },
                ],
            },
        )
        self.assertEqual(
            card.config_template.url,
            "https://analytics.example.com/mcp",
        )
        self.assertEqual(card.auth, "none")
        self.assertEqual(card.inputs_schema, {})

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
