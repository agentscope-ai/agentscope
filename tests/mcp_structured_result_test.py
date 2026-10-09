# -*- coding: utf-8 -*-
"""MCP structured results through a real stdio server and Toolkit."""
import json
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.message import ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolResponse


SERVER = """
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

server = FastMCP("structured-result-test")

@server.tool()
def result(response: dict) -> CallToolResult:
    return CallToolResult(**response)

server.run(transport="stdio")
"""


class MCPStructuredResultTest(IsolatedAsyncioTestCase):
    """Exercise result conversion without mocking the transport or session."""

    async def test_structured_results(self) -> None:
        """Keep nonempty and empty JSON objects, including error details."""
        cases: list[dict] = [
            {"content": [], "structuredContent": {"city": "杭州"}},
            {"content": [], "structuredContent": {}},
            {
                "content": [],
                "structuredContent": {"error": "unavailable"},
                "isError": True,
            },
            {"content": [], "structuredContent": {"recovered": True}},
        ]
        results = await self._call_results(cases)
        for case, result in zip(cases, results):
            with self.subTest(response=case):
                self.assertEqual(1, len(result.content))
                self.assertEqual(
                    case["structuredContent"],
                    json.loads(result.content[0].text),
                )
                self.assertEqual(
                    "error" if case.get("isError") else "success",
                    result.state.value,
                )
                self.assertEqual({}, result.metadata)

    async def test_existing_content(self) -> None:
        """Do not append structured JSON to text or multimodal results."""
        cases: list[dict] = [
            {"content": [{"type": "text", "text": "existing text"}]},
            {
                "content": [{"type": "text", "text": "compatibility copy"}],
                "structuredContent": {"answer": 42},
            },
            {
                "content": [
                    {"type": "image", "mimeType": "image/png", "data": "AA=="},
                ],
                "structuredContent": {"answer": 42},
            },
        ]
        results = await self._call_results(cases)
        for result in results:
            self.assertEqual(1, len(result.content))
            self.assertEqual("success", result.state.value)
        self.assertEqual("existing text", results[0].content[0].text)
        self.assertEqual("compatibility copy", results[1].content[0].text)
        self.assertEqual("AA==", results[2].content[0].source.data)
        self.assertEqual("image/png", results[2].content[0].source.media_type)

    async def test_empty_without_structured_content(self) -> None:
        """An empty result without structured output stays empty."""
        results = await self._call_results([{"content": []}])
        self.assertEqual([], results[0].content)
        self.assertEqual("success", results[0].state.value)

    async def _call_results(self, cases: list[dict]) -> list[ToolResponse]:
        """Call each case on one connection, closing it in the same task."""
        results = []
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "server.py"
            script.write_text(SERVER, encoding="utf-8")
            client = MCPClient(
                name="structured",
                is_stateful=True,
                mcp_config=StdioMCPConfig(
                    command=sys.executable,
                    args=[str(script)],
                ),
            )
            await client.connect()
            try:
                toolkit = Toolkit(mcps=[client])
                state = AgentState()
                for index, case in enumerate(cases):
                    call = ToolCallBlock(
                        id=str(index),
                        name="mcp__structured__result",
                        input=json.dumps({"response": case}),
                    )
                    async for result in toolkit.call_tool(call, state):
                        if isinstance(result, ToolResponse):
                            results.append(result)
            finally:
                await client.close(ignore_errors=False)
        self.assertEqual(len(cases), len(results))
        return results
