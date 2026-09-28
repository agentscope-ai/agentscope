# -*- coding: utf-8 -*-
"""The web search tool in agentscope."""

from typing import Any, List

from .._base import ToolBase, ToolMiddlewareBase
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from ...message import TextBlock, ToolResultState


class WebSearch(ToolBase):
    """The web search tool using DuckDuckGo."""

    name: str = "WebSearch"
    """The tool name presented to the agent."""

    description: str = """A web search tool using DuckDuckGo.
    
  Usage:
- Use this tool to search the internet for up-to-date information.
- Provide a clear search query.
- You can optionally set max_results to limit the number of search results (default is 5)."""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to look up on the web.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of search results to return.",
                "default": 5,
                "minimum": 1,
            },
        },
        "required": ["query"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the web search tool."""
        super().__init__(middlewares=middlewares)

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for web search."""
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Web search is read-only.",
        )

    async def call(  # type: ignore[override]
        self,
        query: str,
        max_results: int = 5,
        **kwargs: Any,
    ) -> ToolChunk:
        """Execute the web search.

        Args:
            query: The search query.
            max_results: Maximum number of results to return.
            **kwargs: Additional parameters.
        """
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="Error: duckduckgo-search is not installed. Please install it using `pip install duckduckgo-search`.",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))

            if not results:
                return ToolChunk(
                    content=[
                        TextBlock(text=f"No results found for query: {query}"),
                    ],
                    state=ToolResultState.SUCCESS,
                    is_last=True,
                )

            formatted_results = []
            for i, res in enumerate(results):
                title = res.get("title", "No Title")
                href = res.get("href", "")
                body = res.get("body", "No description")
                formatted_results.append(
                    f"{i + 1}. [{title}]({href})\n   {body}\n",
                )

            return ToolChunk(
                content=[TextBlock(text="\n".join(formatted_results))],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Web search failed: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
