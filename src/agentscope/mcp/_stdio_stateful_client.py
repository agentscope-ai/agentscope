# -*- coding: utf-8 -*-
"""The StdIO MCP server implementation in AgentScope, which provides
function-level fine-grained control over the MCP servers using standard IO."""
from typing import Literal

from mcp import stdio_client, StdioServerParameters

from ._stateful_client_base import StatefulClientBase


class StdIOStatefulClient(StatefulClientBase):
    """A client class that sets up and manage StdIO MCP server connections, and
    provides function-level fine-grained control over the MCP servers.

    .. tip:: The stateful client is recommended for MCP servers that need to
     maintain session states, e.g. web browsers or other interactive
     MCP servers.

    .. note:: The stateful client will maintain one session across multiple
     tool calls, until the client is closed by explicitly calling the
     `close()` method.

    .. note:: When multiple StdIOStatefulClient instances are connected,
     they should be closed following the Last In First Out (LIFO) principle
     to avoid potential errors. Always close the most recently registered
     client first, then work backwards to the first one.
     For more details, please refer to this `issue
     <https://github.com/modelcontextprotocol/python-sdk/issues/577>`_.
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        encoding: str = "utf-8",
        encoding_error_handler: Literal[
            "strict",
            "ignore",
            "replace",
        ] = "strict",
    ) -> None:
        """Initialize the MCP server with std IO.

        Args:
            name (`str`):
                The name to identify the MCP server, which should be unique
                across the MCP servers.
            command (`str`):
                The executable to run to start the server.
            args (`list[str] | None`, optional):
                Command line arguments to pass to the executable.
            env (`dict[str, str] | None`, optional):
                The environment to use when spawning the process.
            cwd (`str | None`, optional):
                The working directory to use when spawning the process.
            encoding (`str`, optional):
                The text encoding used when sending/receiving messages to the
                server. Defaults to "utf-8".
            encoding_error_handler (`Literal["strict", "ignore", "replace"]`, \
             defaults to "strict"):
                The text encoding error handler.
        """
        super().__init__(name=name)

        self.client = stdio_client(
            StdioServerParameters(
                command=command,
                args=args or [],
                env=env,
                cwd=cwd,
                encoding=encoding,
                encoding_error_handler=encoding_error_handler,
            ),
        )
