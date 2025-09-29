# -*- coding: utf-8 -*-
"""The MCP tool function class in AgentScope."""
from contextlib import _AsyncGeneratorContextManager
from typing import Any, Callable

import mcp
from mcp import ClientSession

from ._client_base import MCPClientBase
from .._utils._common import _extract_json_schema_from_mcp_tool
from ..tool import ToolResponse


class MCPToolFunction:
    """An MCP tool function class that can be called directly."""

    name: str
    """The name of the tool function."""

    description: str
    """The description of the tool function."""

    json_schema: dict[str, Any]
    """JSON schema of the tool function"""

    def __init__(
        self,
        mcp_name: str,
        tool: mcp.types.Tool,
        wrap_tool_result: bool,
        client_gen: Callable[..., _AsyncGeneratorContextManager[Any]]
        | None = None,
        session: ClientSession | None = None,
    ) -> None:
        """Initialize the MCP function."""
        self.mcp_name = mcp_name
        self.name = tool.name
        self.description = tool.description
        self.json_schema = _extract_json_schema_from_mcp_tool(tool)
        self.wrap_tool_result = wrap_tool_result

        # Cannot be None at the same time
        if (
            client_gen is None
            and session is None
            or (client_gen is not None and session is not None)
        ):
            raise ValueError(
                "Either client or session must be provided, but not both.",
            )

        self.client_gen = client_gen
        self.session = session

    async def __call__(
        self,
        **kwargs: Any,
    ) -> mcp.types.CallToolResult | ToolResponse:
        """Call the MCP tool function with the given arguments, and return
        the result."""
        if self.client_gen:
            async with self.client_gen() as cli:
                read_stream, write_stream = cli[0], cli[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    res = await session.call_tool(
                        self.name,
                        arguments=kwargs,
                    )

        else:
            res = await self.session.call_tool(
                self.name,
                arguments=kwargs,
            )

        if self.wrap_tool_result:
            as_content = MCPClientBase._convert_mcp_content_to_as_blocks(
                res.content,
            )
            return ToolResponse(
                content=as_content,
                metadata=res.meta,
            )

        return res
