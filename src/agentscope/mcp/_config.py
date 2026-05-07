# -*- coding: utf-8 -*-
"""The MCP configurations."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class StdioMCPConfig(BaseModel):
    """The STDIO MCP server configuration."""

    type: Literal["stdio_mcp"] = "stdio_mcp"

    command: str = Field(
        title="Command",
        description="The command to start the MCP server.",
    )

    args: list[str] | None = Field(
        title="Args",
        description="The command line arguments to pass to the MCP server.",
        default=None,
    )

    env: dict[str, str] | None = Field(
        title="Environment Variables",
        default=None,
        description="The environment variables to pass to the MCP server.",
    )

    cwd: str | Path | None = Field(
        default=None,
        title="CWD",
        description="The working directory to use when spawning the process.",
    )

    encoding_error_handler: Literal["strict", "ignore", "replace"] = Field(
        default="strict",
        title="Encoding Error Handler",
        description="The text encoding error handler.",
    )


class HttpMCPConfig(BaseModel):
    """The HTTP MCP server configuration."""

    type: Literal["http_mcp"] = "http_mcp"

    url: str = Field(
        title="URL",
        description="The URL of the MCP server.",
    )

    headers: dict[str, str] | None = Field(
        title="Headers",
        description="The additional headers to include in the HTTP request.",
        default=None,
    )

    timeout: float | None = Field(
        title="Timeout",
        description="The HTTP request timeout in seconds.",
        default=None,
    )
