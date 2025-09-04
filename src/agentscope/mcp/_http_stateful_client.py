# -*- coding: utf-8 -*-
"""The MCP stateful HTTP client module in AgentScope."""
from typing import Any, Literal

from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from ._stateful_client_base import StatefulClientBase


class HttpStatefulClient(StatefulClientBase):
    """The stateful sse/streamable HTTP MCP client implementation in
    AgentScope.

    .. tip:: The stateful client is recommended for MCP servers that need to
     maintain session states, e.g. web browsers or other interactive
     MCP servers.

    .. note:: The stateful client will maintain one session across multiple
     tool calls, until the client is closed by explicitly calling the
     `close()` method.

    .. note:: When multiple HttpStatefulClient instances are connected,
     they should be closed following the Last In First Out (LIFO) principle
     to avoid potential errors. Always close the most recently registered
     client first, then work backwards to the first one.
     For more details, please refer to this `issue
     <https://github.com/modelcontextprotocol/python-sdk/issues/577>`_.
    """

    def __init__(
        self,
        name: str,
        transport: Literal["streamable_http", "sse"],
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
        sse_read_timeout: float = 60 * 5,
        **client_kwargs: Any,
    ) -> None:
        """Initialize the streamable HTTP MCP client.

        Args:
            name (`str`):
                The name to identify the MCP server, which should be unique
                across the MCP servers.
            transport (`Literal["streamable_http", "sse"]`):
                The transport type of MCP server. Generally, the URL of sse
                transport should end with `/sse`, while the streamable HTTP
                URL ends with `/mcp`.
            url (`str`):
                The URL to the MCP server.
            headers (`dict[str, str] | None`, optional):
                Additional headers to include in the HTTP request.
            timeout (`float`, optional):
                The timeout for the HTTP request in seconds. Defaults to 30.
            sse_read_timeout (`float`, optional):
                The timeout for reading Server-Sent Events (SSE) in seconds.
                Defaults to 300 (5 minutes).
            **client_kwargs (`Any`):
                The additional keyword arguments to pass to the streamable
                HTTP client.
        """
        super().__init__(name=name)

        assert transport in ["streamable_http", "sse"]
        self.transport = transport

        if self.transport == "streamable_http":
            self.client = streamablehttp_client(
                url=url,
                headers=headers,
                timeout=timeout,
                sse_read_timeout=sse_read_timeout,
                **client_kwargs,
            )
        else:
            self.client = sse_client(
                url=url,
                headers=headers,
                timeout=timeout,
                sse_read_timeout=sse_read_timeout,
                **client_kwargs,
            )
