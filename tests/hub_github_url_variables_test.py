# -*- coding: utf-8 -*-
"""Regression tests for URL template variables in GitHub MCP remotes."""
from unittest import TestCase

from agentscope.app._service import render_mcp
from agentscope.app.hub import GitHubMCPHub


class GitHubRemoteURLVariablesTest(TestCase):
    """The MCP registry declares ``{var}`` URL template variables under
    ``remotes[].variables``. The card must rewrite them to ``${var}`` and
    declare them as install inputs, just like header placeholders."""

    def setUp(self) -> None:
        self.hub = GitHubMCPHub()

    def _card(self, remote: dict) -> object:
        # pylint: disable=protected-access
        return self.hub._to_card(
            {
                "server": {
                    "id": "abc",
                    "name": "acme/thing",
                    "remotes": [remote],
                },
                "x-github": {},
            },
        )

    def test_url_template_variable_is_rewritten_and_declared(self) -> None:
        """A ``{var}`` in the URL becomes ``${var}`` and an input."""
        card = self._card(
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
                "type": "streamable-http",
                "url": "https://{tenant_id}.analytics.example.com/mcp",
                "variables": {
                    "tenant_id": {
                        "description": "Tenant",
                        "isRequired": True,
                    },
                },
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
        )
        prop = card.inputs_schema["properties"]["region"]
        self.assertEqual(prop["description"], "Deployment region")
        self.assertEqual(prop["enum"], ["us-east-1", "eu-west-1"])
        self.assertEqual(prop["default"], "us-east-1")
        self.assertTrue(prop["writeOnly"])
        self.assertEqual(prop["format"], "password")

    def test_placeholder_without_variables_still_declared(self) -> None:
        """A bare ``{var}`` with no ``variables`` block is still resolved."""
        card = self._card(
            {
                "type": "streamable-http",
                "url": "https://{host}.example.com/mcp",
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
                "type": "streamable-http",
                "url": "https://analytics.example.com/mcp",
            },
        )
        self.assertEqual(
            card.config_template.url,
            "https://analytics.example.com/mcp",
        )
        self.assertEqual(card.auth, "none")
        self.assertEqual(card.inputs_schema, {})
