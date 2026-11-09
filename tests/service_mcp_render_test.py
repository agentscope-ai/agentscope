# -*- coding: utf-8 -*-
"""MCP card rendering test case."""
from unittest import TestCase

from agentscope.app._service import MCPRenderError, render_mcp
from agentscope.app.hub import MCPCard

API_KEY_SCHEMA = {
    "type": "object",
    "properties": {
        "api_key": {
            "type": "string",
            "writeOnly": True,
            "format": "password",
        },
    },
    "required": ["api_key"],
}


def _http_card(**config: object) -> MCPCard:
    """Build an HTTP card around the given config overrides."""
    return MCPCard(
        hub_id="testhub",
        name="notion",
        inputs_schema=API_KEY_SCHEMA,
        config_template={
            "type": "http_mcp",
            "url": "https://mcp.example.com/sse",
            **config,
        },
    )


class MCPRenderTest(TestCase):
    """The MCP card rendering test case."""

    def test_substitutes_url_and_headers(self) -> None:
        """Placeholders are filled in both the URL and header values."""
        card = _http_card(
            url="https://mcp.example.com/sse?key=${api_key}",
            headers={"Authorization": "Bearer ${api_key}"},
        )

        client = render_mcp(card, {"api_key": "sk-secret"})

        self.assertEqual(
            client.mcp_config.url,
            "https://mcp.example.com/sse?key=sk-secret",
        )
        self.assertEqual(
            client.mcp_config.headers,
            {"Authorization": "Bearer sk-secret"},
        )
        self.assertEqual(client.name, "notion")

    def test_substitutes_stdio_args_and_env(self) -> None:
        """Placeholders are filled inside list items and env values."""
        card = MCPCard(
            hub_id="testhub",
            name="local",
            inputs_schema=API_KEY_SCHEMA,
            config_template={
                "type": "stdio_mcp",
                "command": "uvx",
                "args": ["server", "--token", "${api_key}"],
                "env": {"API_KEY": "${api_key}"},
            },
        )

        client = render_mcp(card, {"api_key": "sk-secret"})

        self.assertEqual(
            client.mcp_config.args,
            ["server", "--token", "sk-secret"],
        )
        self.assertEqual(client.mcp_config.env, {"API_KEY": "sk-secret"})

    def test_literal_braces_survive(self) -> None:
        """A literal ``{}`` in the config is not treated as a placeholder.

        This is the regression that motivated ``string.Template`` over
        ``str.format``.
        """
        card = _http_card(
            url="https://mcp.example.com/{tenant}/sse",
            headers={
                "X-Filter": '{"kind": "all"}',
                "Authorization": "Bearer ${api_key}",
            },
        )

        client = render_mcp(card, {"api_key": "sk-secret"})

        self.assertEqual(
            client.mcp_config.url,
            "https://mcp.example.com/{tenant}/sse",
        )
        self.assertEqual(
            client.mcp_config.headers["X-Filter"],
            '{"kind": "all"}',
        )
        self.assertEqual(
            client.mcp_config.headers["Authorization"],
            "Bearer sk-secret",
        )

    def test_undeclared_dollar_survives(self) -> None:
        """A ``$VAR`` the card does not declare is left untouched."""
        card = MCPCard(
            hub_id="testhub",
            name="local",
            inputs_schema=API_KEY_SCHEMA,
            config_template={
                "type": "stdio_mcp",
                "command": "uvx",
                "args": ["--config", "$HOME/.config", "${api_key}"],
            },
        )

        client = render_mcp(card, {"api_key": "sk-secret"})

        self.assertEqual(
            client.mcp_config.args,
            ["--config", "$HOME/.config", "sk-secret"],
        )

    def test_missing_required_value(self) -> None:
        """A missing required input is rejected by the schema."""
        card = _http_card(headers={"Authorization": "Bearer ${api_key}"})

        with self.assertRaises(MCPRenderError) as ctx:
            render_mcp(card, {})

        self.assertIn("api_key", str(ctx.exception))

    def test_wrong_value_type(self) -> None:
        """A value violating the schema type is rejected."""
        card = _http_card(headers={"Authorization": "Bearer ${api_key}"})

        with self.assertRaises(MCPRenderError):
            render_mcp(card, {"api_key": 12345})

    def test_missing_optional_value_is_reported(self) -> None:
        """An optional input still referenced by the template errors.

        ``safe_substitute`` would silently leave ``${region}`` in the
        URL, so the unfilled placeholder is reported instead.
        """
        card = MCPCard(
            hub_id="testhub",
            name="notion",
            inputs_schema={
                "type": "object",
                "properties": {"region": {"type": "string"}},
            },
            config_template={
                "type": "http_mcp",
                "url": "https://${region}.example.com/sse",
            },
        )

        with self.assertRaises(MCPRenderError) as ctx:
            render_mcp(card, {})

        self.assertIn("region", str(ctx.exception))

    def test_missing_optional_env_is_omitted(self) -> None:
        """An unfilled optional env input is not launched as a placeholder."""
        card = MCPCard(
            hub_id="testhub",
            name="local",
            inputs_schema={
                "type": "object",
                "properties": {"region": {"type": "string"}},
            },
            config_template={
                "type": "stdio_mcp",
                "command": "uvx",
                "args": ["server"],
                "env": {"REGION": "${region}"},
            },
        )

        client = render_mcp(card, {})

        self.assertEqual(client.mcp_config.env, {})

    def test_schema_default_fills_omitted_env_input(self) -> None:
        """API callers inherit schema defaults without submitting them."""
        card = MCPCard(
            hub_id="testhub",
            name="local",
            inputs_schema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "default": "safe",
                    },
                },
            },
            config_template={
                "type": "stdio_mcp",
                "command": "uvx",
                "args": ["server"],
                "env": {"MODE": "${mode}"},
            },
        )
        submitted: dict = {}

        client = render_mcp(card, submitted)

        self.assertEqual(client.mcp_config.env, {"MODE": "safe"})
        self.assertEqual(submitted, {})

    def test_no_inputs_card(self) -> None:
        """A card with no inputs renders as-is."""
        card = MCPCard(
            hub_id="testhub",
            name="public",
            auth="none",
            config_template={
                "type": "http_mcp",
                "url": "https://public.example.com/sse",
            },
        )

        client = render_mcp(card, {})

        self.assertEqual(
            client.mcp_config.url,
            "https://public.example.com/sse",
        )

    def test_name_override(self) -> None:
        """The caller can rename the installed client."""
        card = _http_card()

        client = render_mcp(card, {"api_key": "sk"}, name="my-notion")

        self.assertEqual(client.name, "my-notion")

    def test_invalid_client_name(self) -> None:
        """A slug the MCP client rejects surfaces as a render error."""
        card = MCPCard(
            hub_id="testhub",
            name="not.a.valid.name",
            config_template={
                "type": "http_mcp",
                "url": "https://public.example.com/sse",
            },
        )

        with self.assertRaises(MCPRenderError) as ctx:
            render_mcp(card, {})

        self.assertIn("invalid client", str(ctx.exception))
