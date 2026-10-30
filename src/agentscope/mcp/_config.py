# -*- coding: utf-8 -*-
"""The MCP configurations."""
import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator


# Known MCP server commands that are considered safe.
# Users can extend this list via configuration if needed.
DEFAULT_ALLOWED_MCP_COMMANDS = {
    "npx",
    "python",
    "python3",
    "node",
    "npm",
    "mcp-server-filesystem",
    "mcp-server-git",
    "mcp-server-github",
    "mcp-server-postgres",
    "mcp-server-sqlite",
}

# Characters that indicate shell metacharacters / command chaining.
# If any of these are present, the command is rejected.
_SHELL_METACHARACTERS_RE = re.compile(r"[;|&<>$`\\\n\r\x00]")


class StdioMCPConfig(BaseModel):
    """The STDIO MCP server configuration."""

    type: Literal["stdio_mcp"] = "stdio_mcp"

    allowed_commands: set[str] | None = Field(
        default=None,
        title="Allowed Commands",
        description=(
            "Optional allowlist of permitted commands. When None, "
            f"the default set {DEFAULT_ALLOWED_MCP_COMMANDS!r} is used."
        ),
    )

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

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: str, info: ValidationInfo) -> str:
        """Validate that the command is safe to execute.

        Rejects commands containing shell metacharacters and enforces
        an allowlist of permitted base commands to prevent arbitrary
        code execution.
        """
        if not value or not value.strip():
            raise ValueError("MCP command cannot be empty.")

        stripped = value.strip()

        # Reject any command with shell metacharacters that could be
        # used for command injection (e.g. ;, |, &, <, >, $, `, etc.)
        if _SHELL_METACHARACTERS_RE.search(stripped):
            raise ValueError(
                "MCP command contains shell metacharacters and is not permitted. "
                "Use a simple command without pipes, redirects, or variable expansion.",
            )

        # Extract the base command (first token before any whitespace).
        base_command = stripped.split()[0]

        # Resolve the command name if it's an absolute or relative path.
        # We check the basename so that "/usr/bin/python" and "python"
        # are treated consistently.
        command_name = Path(base_command).name

        data = info.data
        allowed = data.get("allowed_commands") or DEFAULT_ALLOWED_MCP_COMMANDS
        if command_name not in allowed:
            raise ValueError(
                f"MCP command '{command_name}' is not in the allowed set "
                f"{allowed!r}. If you trust this command, add it to the "
                "allowed_commands field.",
            )

        return stripped


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
        default=30.0,
    )
