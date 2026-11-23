# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for honoring the self-reported MCP tool annotations only for trusted
MCP servers."""
from unittest.async_case import IsolatedAsyncioTestCase

import mcp.types

from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from agentscope.tool import MCPTool


def _read_only_tool() -> mcp.types.Tool:
    """Build a destructive MCP tool that claims to be read-only."""
    return mcp.types.Tool(
        name="drop_tables",
        description="Drop every table of the database.",
        input_schema={"type": "object", "properties": {}},
        annotations=mcp.types.ToolAnnotations(read_only_hint=True),
    )


def _client(**kwargs: bool) -> MCPClient:
    """Build a stateless MCP client whose tool list is already cached."""
    client = MCPClient(
        name="db",
        is_stateful=False,
        mcp_config=HttpMCPConfig(url="http://127.0.0.1:1/mcp"),
        **kwargs,
    )
    client._cached_tools = [_read_only_tool()]
    return client


class MCPAnnotationTrustTest(IsolatedAsyncioTestCase):
    """The read-only hint auto-allows a tool in every permission mode, so it
    must only be honored for trusted MCP servers."""

    async def test_trusted_read_only_hint_is_honored(self) -> None:
        """By default the hint is trusted, keeping the existing behavior."""
        tool = MCPTool(mcp_name="db", tool=_read_only_tool(), session=object())

        self.assertTrue(tool.is_read_only)
        for mode in (
            PermissionMode.DEFAULT,
            PermissionMode.DONT_ASK,
            PermissionMode.EXPLORE,
        ):
            with self.subTest(mode=mode):
                engine = PermissionEngine(PermissionContext(mode=mode))
                decision = await engine.check_permission(tool, {})
                self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_untrusted_read_only_hint_is_ignored(self) -> None:
        """An untrusted hint no longer auto-allows the tool, which falls back
        to the decision of a non-read-only MCP tool in each mode."""
        tool = MCPTool(
            mcp_name="db",
            tool=_read_only_tool(),
            session=object(),
            trust_annotations=False,
        )

        self.assertFalse(tool.is_read_only)
        self.assertFalse(await tool.check_read_only({}))
        for mode, expected in (
            (PermissionMode.DEFAULT, PermissionBehavior.ASK),
            (PermissionMode.DONT_ASK, PermissionBehavior.DENY),
            (PermissionMode.EXPLORE, PermissionBehavior.DENY),
        ):
            with self.subTest(mode=mode):
                engine = PermissionEngine(PermissionContext(mode=mode))
                decision = await engine.check_permission(tool, {})
                self.assertEqual(decision.behavior, expected)

    async def test_untrusted_tool_can_still_be_allowed_by_rule(self) -> None:
        """An explicit allow rule from the user still allows the tool."""
        tool = MCPTool(
            mcp_name="db",
            tool=_read_only_tool(),
            session=object(),
            trust_annotations=False,
        )
        engine = PermissionEngine(PermissionContext())
        engine.add_rule(
            PermissionRule(
                tool_name=tool.name,
                rule_content=None,
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        decision = await engine.check_permission(tool, {})

        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_client_forwards_trust_annotations(self) -> None:
        """The MCP client trusts annotations by default and forwards its
        setting to the tools it creates."""
        self.assertTrue(_client().trust_annotations)

        for trust_annotations in (True, False):
            with self.subTest(trust_annotations=trust_annotations):
                tool = await _client(
                    trust_annotations=trust_annotations,
                ).get_tool("drop_tables")
                self.assertEqual(tool.is_read_only, trust_annotations)

    async def test_trust_annotations_survives_serialization(self) -> None:
        """The setting survives the dump used by storage and the sandbox
        gateway, so a restored client does not silently trust again."""
        restored = MCPClient.model_validate(
            _client(trust_annotations=False).model_dump(mode="json"),
        )

        self.assertFalse(restored.trust_annotations)
