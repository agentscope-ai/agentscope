# -*- coding: utf-8 -*-
"""Conditional dispatch over an existing Streamable HTTP MCP session."""
import json
from typing import Any

import httpx2
from mcp.shared._httpx_utils import (
    MCP_DEFAULT_SSE_READ_TIMEOUT,
    MCP_DEFAULT_TIMEOUT,
)

from ._config import HttpMCPConfig


class StatefulToolHTTPClient(httpx2.AsyncClient):
    """Reject stale approvals after the SDK has built its HTTP request."""

    version_key = "agentscope.io/credential-version"

    def __init__(self, config: HttpMCPConfig, version: int) -> None:
        timeout = (
            httpx2.Timeout(config.timeout)
            if config.timeout is not None
            else httpx2.Timeout(
                MCP_DEFAULT_TIMEOUT,
                read=MCP_DEFAULT_SSE_READ_TIMEOUT,
            )
        )
        super().__init__(headers=config.headers, timeout=timeout)
        self.credential_version = version

    async def send(
        self,
        request: httpx2.Request,
        **kwargs: Any,
    ) -> httpx2.Response:
        """Validate the RPC stamp against the current credential version."""
        # MCP POSTs have buffered JSON bodies. The stamp travels with the
        # RPC across the SDK's writer task and on any automatic retries.
        key = self.version_key
        if request.method == "POST" and key.encode() in request.content:
            rpc = json.loads(request.content)
            meta = rpc.get("params", {}).get("_meta") or {}
            if (
                rpc.get("method") == "tools/call"
                and key in meta
                and meta[key] != self.credential_version
            ):
                return httpx2.Response(
                    409,
                    request=request,
                    json={
                        "jsonrpc": "2.0",
                        "id": rpc["id"],
                        "error": {
                            "code": -32000,
                            "message": "MCP credentials changed; "
                            "rediscover and approve the tool again.",
                        },
                    },
                )
        return await super().send(request, **kwargs)
