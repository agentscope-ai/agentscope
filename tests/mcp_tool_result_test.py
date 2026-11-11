# -*- coding: utf-8 -*-
"""Tests for keeping the MCP tool result content that carries the payload."""
import base64
import json
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

import mcp.types
from utils import AnyString

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.state import AgentState
from agentscope.tool import MCPTool, Toolkit, ToolResponse


_PNG_BASE64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


class _FakeSession:
    """A ClientSession stand-in that replays one prepared result."""

    def __init__(self, result: mcp.types.CallToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(  # pylint: disable=unused-argument
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
        progress_callback: Any | None = None,
    ) -> mcp.types.CallToolResult:
        """Record the call and return the prepared result."""
        self.calls.append((name, arguments or {}))
        return self.result


def _tool(
    result: mcp.types.CallToolResult,
) -> MCPTool:
    """Build a stateful MCP tool backed by a fake session."""
    tool_definition = mcp.types.Tool(
        name="get_weather",
        description="Read the current weather of a city.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "temperature_c": {"type": "number"},
            },
        },
    )
    return MCPTool(
        mcp_name="weather",
        tool=tool_definition,
        session=_FakeSession(result),
    )


class MCPStructuredResultTest(IsolatedAsyncioTestCase):
    """The model must not lose a result that only lives in
    ``structuredContent``."""

    async def test_embedded_resource_preserves_json_field_names(self) -> None:
        """Embedded resource text retains snake-case fields and metadata."""
        resource = {
            "uri": "https://example.com/report.txt",
            "mime_type": "text/plain",
            "meta": {"source": "report"},
            "text": "Report content",
        }
        chunk = await _tool(
            mcp.types.CallToolResult(
                content=[
                    mcp.types.EmbeddedResource(
                        type="resource",
                        resource=mcp.types.TextResourceContents(**resource),
                    ),
                ],
            ),
        )()
        self.assertEqual(
            chunk.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(resource, indent=2),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_structured_result_is_kept_when_content_is_empty(
        self,
    ) -> None:
        """A structured-only result reaches the model as JSON text."""
        structured = {"city": "Paris", "temperature_c": 21.5}
        chunk = await _tool(
            mcp.types.CallToolResult(
                content=[],
                structured_content=structured,
            ),
        )(city="Paris")

        self.assertEqual(
            chunk.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": '{"city": "Paris", "temperature_c": 21.5}',
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_text_summary_keeps_structured_result(self) -> None:
        """Keep the summary and fields available only in structured JSON."""
        chunk = await _tool(
            mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(type="text", text="21.5C in Paris"),
                ],
                structured_content={"city": "Paris", "temperature_c": 21.5},
            ),
        ).call(city="Paris")

        self.assertEqual(
            chunk.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": "21.5C in Paris",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "text",
                        "text": '{"city": "Paris", "temperature_c": 21.5}',
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_text_results_keep_structured_payload_and_state(
        self,
    ) -> None:
        """Empty text, errors and empty objects must retain structured data."""
        for text, structured, expected_json, is_error in (
            ("", {"city": "Paris"}, '{"city": "Paris"}', False),
            ("Lookup failed", {"code": 404}, '{"code": 404}', True),
            ("No rows", {}, "{}", False),
        ):
            with self.subTest(text=text, is_error=is_error):
                chunk = await _tool(
                    mcp.types.CallToolResult(
                        content=[
                            mcp.types.TextContent(type="text", text=text),
                        ],
                        structured_content=structured,
                        is_error=is_error,
                    ),
                ).call(city="Paris")

                self.assertEqual(
                    [
                        block.text
                        for block in chunk.content
                        if isinstance(block, TextBlock)
                    ],
                    [text, expected_json],
                )
                self.assertEqual(
                    chunk.state,
                    ToolResultState.ERROR
                    if is_error
                    else ToolResultState.RUNNING,
                )

    async def test_streamed_result_keeps_summary_and_structured_payload(
        self,
    ) -> None:
        """The final progress-stream chunk retains the complete result."""
        tool = _tool(
            mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(type="text", text="Query completed"),
                ],
                structured_content={"city": "Paris", "temperature_c": 21.5},
            ),
        )
        tool.stream_progress = True
        stream = await tool.call(city="Paris")
        assert isinstance(stream, AsyncGenerator)
        chunks = [chunk async for chunk in stream]

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].is_last)
        self.assertEqual(
            [block.text for block in chunks[0].content],
            [
                "Query completed",
                '{"city": "Paris", "temperature_c": 21.5}',
            ],
        )

    async def test_serialized_structured_result_is_not_duplicated(
        self,
    ) -> None:
        """Find an existing JSON payload even after an ordinary summary."""
        texts = ["Query completed", '{"city": "Paris", "temperature_c": 21.5}']
        chunk = await _tool(
            mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(type="text", text=text)
                    for text in texts
                ],
                structured_content={"city": "Paris", "temperature_c": 21.5},
            ),
        ).call(city="Paris")

        self.assertEqual(
            [
                block.text
                for block in chunk.content
                if isinstance(block, TextBlock)
            ],
            texts,
        )

    async def test_text_without_structured_content_stays_unchanged(
        self,
    ) -> None:
        """Legacy text-only results must not gain a JSON null block."""
        chunk = await _tool(
            mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(type="text", text="Legacy result"),
                ],
            ),
        ).call(city="Paris")

        self.assertEqual(
            [
                block.text
                for block in chunk.content
                if isinstance(block, TextBlock)
            ],
            ["Legacy result"],
        )

    async def test_mixed_result_reaches_model_through_toolkit(self) -> None:
        """The model request must contain both the summary and JSON fields."""
        tool = _tool(
            mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(type="text", text="Query completed"),
                ],
                structured_content={"city": "北京", "temperature_c": 21.5},
            ),
        )
        tool_call = ToolCallBlock(
            id="call_weather",
            name=tool.name,
            input='{"city": "北京"}',
        )
        toolkit = Toolkit(tools=[tool])
        results = [
            chunk async for chunk in toolkit.call_tool(tool_call, AgentState())
        ]
        response = next(
            item for item in results if isinstance(item, ToolResponse)
        )
        self.assertEqual(response.state, ToolResultState.SUCCESS)

        messages = await OpenAIChatFormatter().format(
            [
                AssistantMsg(
                    name="assistant",
                    content=[
                        tool_call,
                        ToolResultBlock(
                            id=tool_call.id,
                            name=tool_call.name,
                            output=response.content,
                            state=response.state,
                        ),
                    ],
                ),
            ],
        )
        tool_message = next(msg for msg in messages if msg["role"] == "tool")
        self.assertIn("Query completed", tool_message["content"])
        self.assertIn(
            '{"city": "北京", "temperature_c": 21.5}',
            tool_message["content"],
        )

    async def test_structured_result_is_kept_next_to_media(self) -> None:
        """Media-only content keeps the structured payload as well."""
        structured = {"city": "Paris", "temperature_c": 21.5}
        chunk = await _tool(
            mcp.types.CallToolResult(
                content=[
                    mcp.types.ImageContent(
                        type="image",
                        data=_PNG_BASE64,
                        mime_type="image/png",
                    ),
                ],
                structured_content=structured,
            ),
        ).call(city="Paris")

        self.assertEqual(
            chunk.model_dump(),
            {
                "content": [
                    {
                        "type": "data",
                        "source": {
                            "type": "base64",
                            "data": _PNG_BASE64,
                            "media_type": "image/png",
                        },
                        "name": None,
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "text",
                        "text": '{"city": "Paris", "temperature_c": 21.5}',
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_empty_result_without_structured_content_stays_empty(
        self,
    ) -> None:
        """Nothing is invented when the server returned no result at all."""
        chunk = await _tool(
            mcp.types.CallToolResult(content=[]),
        ).call(city="Paris")

        self.assertEqual(
            chunk.model_dump(),
            {
                "content": [],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_error_result_keeps_state_and_payload(self) -> None:
        """An error result keeps its state and still carries the payload."""
        structured = {"error": "unknown city"}
        chunk = await _tool(
            mcp.types.CallToolResult(
                content=[],
                structured_content=structured,
                is_error=True,
            ),
        ).call(city="Atlantis")

        self.assertEqual(
            chunk.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": '{"error": "unknown city"}',
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )
