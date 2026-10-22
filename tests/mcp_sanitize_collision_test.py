# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Sanitized MCP tool names that would otherwise collide stay distinct."""
import hashlib
from typing import Any
from unittest import IsolatedAsyncioTestCase

import mcp.types

from agentscope.tool import FunctionTool, MCPTool, ToolGroup, Toolkit


_NAME_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"


class _RecordingSession:
    """A session that records the tool name the server is called with."""

    def __init__(self) -> None:
        """Start with no recorded call."""
        self.calls: list[str] = []

    async def call_tool(  # pylint: disable=unused-argument
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
        progress_callback: Any | None = None,
    ) -> mcp.types.CallToolResult:
        """Record the call and return an empty result.

        ``progress_callback`` is accepted because ``MCPTool`` always
        passes one after MCP progress streaming. This stub ignores it.
        """
        self.calls.append(name)
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="ok")],
        )


class _ListingClient:
    """Lists a fresh pair of colliding MCP tools on every call."""

    def __init__(
        self,
        names: tuple[str, ...],
        server: str = "knowledge",
    ) -> None:
        """Remember the raw tool names to expose."""
        self._names = names
        self._server = server
        self.is_stateful = False
        self.is_connected = False
        self.name = server
        self.listings = 0

    async def list_tools(self) -> list[MCPTool]:
        """Return new adapters, as a real client does on every listing."""
        self.listings += 1
        return [_mcp_tool(self._server, name) for name in self._names]


def _mcp_tool(
    server: str,
    raw_name: str,
    session: _RecordingSession | None = None,
) -> MCPTool:
    """Wrap one MCP tool, using the raw name as its description."""
    return MCPTool(
        mcp_name=server,
        tool=mcp.types.Tool(
            name=raw_name,
            description=raw_name,
            input_schema={"type": "object", "properties": {}},
        ),
        session=session or _RecordingSession(),
    )


def _identity_suffix(server: str, raw_name: str, length: int = 8) -> str:
    """The disambiguation suffix derived from the unsanitized identity."""
    payload = f"{server}\0{raw_name}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"_{digest[:length]}"


def _schema_names(schemas: list[dict]) -> list[str]:
    """Model-facing names from toolkit JSON schemas."""
    return [schema["function"]["name"] for schema in schemas]


class MCPSanitizeCollisionTest(IsolatedAsyncioTestCase):
    """Colliding sanitized names stay addressable; other names do not move."""

    async def test_repro_colliding_names_stay_distinct(self) -> None:
        """``search.docs`` and ``searchxdocs`` are both registered and
        lookup by the first model-facing name returns the first tool."""
        raw_names = ("search.docs", "searchxdocs")
        tools = [_mcp_tool("knowledge", name) for name in raw_names]
        toolkit = Toolkit()
        for tool in tools:
            await toolkit.add_tool(tool)

        schemas = await toolkit.get_tool_schemas()
        selected = await toolkit.get_tool(tools[0].name)

        self.assertEqual(len({tool.name for tool in tools}), 2)
        self.assertEqual(len(schemas), 2)
        self.assertEqual(_schema_names(schemas), [tool.name for tool in tools])
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertIs(selected, tools[0])
        self.assertEqual(selected.description, "search.docs")
        self.assertRegex(tools[0].name, _NAME_PATTERN)
        self.assertRegex(tools[1].name, _NAME_PATTERN)

    async def test_constructor_keeps_both_colliding_tools(self) -> None:
        """``Toolkit(tools=...)`` must not drop a tool the way ``add_tool``
        used to."""
        raw_names = ("search.docs", "searchxdocs")
        tools = [_mcp_tool("knowledge", name) for name in raw_names]
        toolkit = Toolkit(tools=tools)

        schemas = await toolkit.get_tool_schemas()
        selected = await toolkit.get_tool(tools[0].name)

        self.assertEqual(len(schemas), 2)
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.description, "search.docs")
        self.assertEqual(selected._tool.name, "search.docs")

    async def test_disambiguation_is_independent_of_order(self) -> None:
        """The model-facing name is a function of the raw identity, not of
        which tool was registered first."""

        async def names_for(order: tuple[str, ...]) -> dict[str, str]:
            tools = [_mcp_tool("knowledge", name) for name in order]
            toolkit = Toolkit()
            for tool in tools:
                await toolkit.add_tool(tool)
            return {tool._tool.name: tool.name for tool in tools}

        forward = await names_for(
            ("search.docs", "search:docs", "searchxdocs"),
        )
        backward = await names_for(
            ("searchxdocs", "search:docs", "search.docs"),
        )
        self.assertEqual(forward, backward)
        self.assertEqual(
            forward["searchxdocs"],
            "mcp__knowledge__searchxdocs",
        )
        self.assertEqual(len(set(forward.values())), 3)

    async def test_suffix_comes_from_the_unsanitized_identity(self) -> None:
        """The extra spelling is the sanitized base plus a hash of the
        original server and tool name."""
        raw_names = ("search.docs", "searchxdocs")
        tools = [_mcp_tool("knowledge", name) for name in raw_names]
        await Toolkit().add_tool(tools)

        suffix = _identity_suffix("knowledge", "search.docs")
        by_raw = {tool._tool.name: tool.name for tool in tools}
        self.assertEqual(by_raw["searchxdocs"], "mcp__knowledge__searchxdocs")
        self.assertEqual(
            by_raw["search.docs"],
            f"mcp__knowledge__searchxdocs{suffix}",
        )

    async def test_both_illegal_names_get_distinct_suffixes(self) -> None:
        """``search.docs`` and ``search:docs`` neither own the sanitized
        spelling, so neither is dropped."""
        raw_names = ("search.docs", "search:docs")
        tools = [_mcp_tool("knowledge", name) for name in raw_names]
        toolkit = Toolkit(tools=tools)
        schemas = await toolkit.get_tool_schemas()

        self.assertEqual(len(schemas), 2)
        self.assertEqual(len({tool.name for tool in tools}), 2)
        bare = "mcp__knowledge__searchxdocs"
        for tool in tools:
            self.assertNotEqual(tool.name, bare)
            self.assertEqual(
                tool.name,
                bare + _identity_suffix("knowledge", tool._tool.name),
            )
            self.assertEqual(tool._tool.name, tool.description)

    async def test_non_colliding_names_are_unchanged(self) -> None:
        """Legal names, a sanitized tool on its own, and sanitized names
        that do not collide with each other keep the previous spelling."""
        alone = _mcp_tool("knowledge", "search.docs")
        weather = _mcp_tool("knowledge", "get_weather")
        dotted = _mcp_tool("knowledge", "search.docs")
        listed = _mcp_tool("knowledge", "list.files")
        alpha = _mcp_tool("alpha", "search.docs")
        beta = _mcp_tool("beta", "searchxdocs")
        long_name = "n" * 80
        long_tool = _mcp_tool("knowledge", long_name)

        toolkit = Toolkit()
        await toolkit.add_tool(alone)
        await toolkit.add_tool(
            [weather, dotted, listed, alpha, beta, long_tool],
        )

        self.assertEqual(alone.name, "mcp__knowledge__searchxdocs")
        self.assertEqual(weather.name, "mcp__knowledge__get_weather")
        self.assertEqual(dotted.name, "mcp__knowledge__searchxdocs")
        self.assertEqual(listed.name, "mcp__knowledge__listxfiles")
        self.assertEqual(alpha.name, "mcp__alpha__searchxdocs")
        self.assertEqual(beta.name, "mcp__beta__searchxdocs")
        self.assertEqual(long_tool.name, f"mcp__knowledge__{long_name}")
        self.assertGreater(len(long_tool.name), 64)

    async def test_server_call_uses_the_raw_name(self) -> None:
        """Disambiguation changes what the model sees, not the name sent
        to the MCP server."""
        sessions = {
            "search.docs": _RecordingSession(),
            "searchxdocs": _RecordingSession(),
        }
        tools = [
            _mcp_tool("knowledge", raw_name, sessions[raw_name])
            for raw_name in ("search.docs", "searchxdocs")
        ]
        toolkit = Toolkit()
        for tool in tools:
            await toolkit.add_tool(tool)

        for tool in tools:
            await tool()

        self.assertEqual(sessions["search.docs"].calls, ["search.docs"])
        self.assertEqual(sessions["searchxdocs"].calls, ["searchxdocs"])
        self.assertEqual(tools[0]._tool.name, "search.docs")
        self.assertEqual(tools[1]._tool.name, "searchxdocs")
        selected = await toolkit.get_tool(tools[0].name)
        assert selected is not None
        self.assertEqual(selected._tool.name, "search.docs")

    async def test_true_duplicate_still_overwrites(self) -> None:
        """The same raw MCP tool registered twice is still one tool."""
        tools = [_mcp_tool("knowledge", "search.docs") for _ in range(2)]
        toolkit = Toolkit()
        with self.assertLogs("as", level="WARNING") as captured:
            await toolkit.add_tool(tools)

        duplicate = any(
            "Duplicate tool name" in message for message in captured.output
        )
        self.assertTrue(duplicate)
        schemas = await toolkit.get_tool_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(tools[0].name, "mcp__knowledge__searchxdocs")
        self.assertEqual(tools[1].name, "mcp__knowledge__searchxdocs")

    async def test_duplicate_function_tool_still_overwrites(self) -> None:
        """Plain tools that really share a name are not given a suffix."""

        def noop() -> str:
            """Do nothing."""
            return "ok"

        first = FunctionTool(noop, name="noop")
        second = FunctionTool(noop, name="noop")
        toolkit = Toolkit()
        with self.assertLogs("as", level="WARNING") as captured:
            await toolkit.add_tool([first, second])

        duplicate = any(
            "Duplicate tool name" in message for message in captured.output
        )
        self.assertTrue(duplicate)
        self.assertEqual(len(await toolkit.get_tool_schemas()), 1)
        self.assertEqual(first.name, "noop")
        self.assertEqual(second.name, "noop")

    async def test_long_colliding_names_fit_provider_limit(self) -> None:
        """A suffix that would push a long name past 64 characters is
        truncated, and the two tools stay distinct."""
        raw_names = ("search." + "n" * 80, "search:" + "n" * 80)
        tools = [_mcp_tool("knowledge", name) for name in raw_names]
        toolkit = Toolkit(tools=list(tools))

        schemas = await toolkit.get_tool_schemas()
        self.assertEqual(len(schemas), 2)
        bare = "mcp__knowledge__" + "searchx" + "n" * 80
        for tool in tools:
            suffix = _identity_suffix("knowledge", tool._tool.name)
            self.assertEqual(tool.name, bare[: 64 - len(suffix)] + suffix)
            self.assertEqual(len(tool.name), 64)
            self.assertRegex(tool.name, _NAME_PATTERN)
            self.assertEqual(tool._tool.name, tool.description)
        self.assertNotEqual(tools[0].name, tools[1].name)

        session = tools[0]._session
        assert isinstance(session, _RecordingSession)
        await tools[0]()
        self.assertEqual(session.calls, [raw_names[0]])

    async def test_listing_client_collision_is_stable(self) -> None:
        """Tools discovered from an MCP client are separated on every
        listing, under the same model-facing names."""
        client = _ListingClient(("search.docs", "searchxdocs"))
        toolkit = Toolkit(mcps=[client])  # type: ignore[list-item]

        first = _schema_names(await toolkit.get_tool_schemas())
        second = _schema_names(await toolkit.get_tool_schemas())

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(client.listings, 2)
        suffix = _identity_suffix("knowledge", "search.docs")
        self.assertEqual(
            set(first),
            {
                "mcp__knowledge__searchxdocs",
                f"mcp__knowledge__searchxdocs{suffix}",
            },
        )
        selected = await toolkit.get_tool(
            f"mcp__knowledge__searchxdocs{suffix}",
        )
        assert selected is not None
        self.assertEqual(selected.description, "search.docs")
        self.assertEqual(selected._tool.name, "search.docs")

    async def test_collision_across_groups_stays_distinct(self) -> None:
        """The model sees one flat namespace, so a collision between
        groups is separated too."""
        first = _mcp_tool("knowledge", "search.docs")
        second = _mcp_tool("knowledge", "searchxdocs")
        extra = ToolGroup(
            name="extra",
            description="Extra tools.",
            tools=[second],
        )
        toolkit = Toolkit(tools=[first], tool_groups=[extra])

        self.assertNotEqual(first.name, second.name)
        schemas = await toolkit.get_tool_schemas(groups=["extra"])
        mcp_names = [
            name for name in _schema_names(schemas) if name.startswith("mcp__")
        ]
        self.assertEqual(mcp_names, [first.name, second.name])
        selected = await toolkit.get_tool(first.name)
        assert selected is not None
        self.assertEqual(selected.description, "search.docs")

    async def test_function_occupying_the_sanitized_name_is_kept(self) -> None:
        """A function explicitly named with the sanitized spelling is not
        replaced by either MCP tool."""

        def noop() -> str:
            """Do nothing."""
            return "ok"

        plain = FunctionTool(noop, name="mcp__knowledge__searchxdocs")
        dotted = _mcp_tool("knowledge", "search.docs")
        literal = _mcp_tool("knowledge", "searchxdocs")
        toolkit = Toolkit(tools=[plain, dotted, literal])
        names = _schema_names(await toolkit.get_tool_schemas())

        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(plain.name, "mcp__knowledge__searchxdocs")
        self.assertNotEqual(dotted.name, plain.name)
        self.assertNotEqual(literal.name, plain.name)
        self.assertIn(plain.name, names)
        self.assertIn(dotted.name, names)
        self.assertIn(literal.name, names)

    async def test_collision_does_not_warn_about_a_duplicate(self) -> None:
        """A sanitization collision is not reported as an overwrite."""
        raw_names = ("search.docs", "searchxdocs")
        tools = [_mcp_tool("knowledge", name) for name in raw_names]
        toolkit = Toolkit()
        with self.assertNoLogs("as", level="WARNING"):
            await toolkit.add_tool(tools)
            await toolkit.get_tool_schemas()
