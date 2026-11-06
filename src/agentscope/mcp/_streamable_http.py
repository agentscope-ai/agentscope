# -*- coding: utf-8 -*-
"""Streamable HTTP transport for the supported MCP 1.x SDKs."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
from mcp.client import streamable_http
from mcp.shared._httpx_utils import (
    McpHttpClientFactory,
    create_mcp_http_client,
)

if not hasattr(streamable_http, "streamable_http_client"):
    # Earlier supported SDKs (including 1.13) configure HTTP on the transport.
    open_streamable_http = streamable_http.streamablehttp_client
else:

    @asynccontextmanager
    async def open_streamable_http(
        url: str,
        headers: dict[str, str] | None,
        timeout: float | timedelta,
        sse_read_timeout: float | timedelta,
        terminate_on_close: bool = True,
        httpx_client_factory: McpHttpClientFactory = create_mcp_http_client,
        auth: httpx.Auth | None = None,
    ) -> AsyncIterator[Any]:
        """Keep the configured HTTP client open for the transport lifetime."""
        if isinstance(timeout, timedelta):
            timeout = timeout.total_seconds()
        if isinstance(sse_read_timeout, timedelta):
            sse_read_timeout = sse_read_timeout.total_seconds()

        async with httpx_client_factory(
            headers=headers,
            timeout=httpx.Timeout(timeout, read=sse_read_timeout),
            auth=auth,
        ) as client:
            async with streamable_http.streamable_http_client(
                url,
                http_client=client,
                terminate_on_close=terminate_on_close,
            ) as streams:
                yield streams
