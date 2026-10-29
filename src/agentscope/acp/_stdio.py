# -*- coding: utf-8 -*-
"""Run the ACP adapter over cross-platform SDK stdio streams."""

import io
import sys
from contextlib import redirect_stdout
from typing import BinaryIO, TextIO, cast

from acp.agent import AgentSideConnection
from acp.stdio import stdio_streams

from ._server import ACPAgentServer, AgentFactory


class _ProtocolStdout(io.TextIOBase):
    """Route diagnostics to stderr; retain binary stdout only on Windows.

    The SDK's Windows writer looks up sys.stdout.buffer for each write,
    whereas its POSIX writer captures the original descriptor at startup.
    """

    def __init__(self, buffer: BinaryIO) -> None:
        self.buffer = buffer if sys.platform == "win32" else sys.stderr.buffer

    def write(self, text: str) -> int:
        """Keep ordinary Python output off the protocol channel."""
        return sys.stderr.write(text)

    def flush(self) -> None:
        """Flush diagnostic output."""
        sys.stderr.flush()


async def serve_acp(agent_factory: AgentFactory) -> None:
    """Serve a session factory until stdin closes, then release resources.

    This function owns process stdio. Do not run another console or stdio
    server alongside it. The SDK's session/close extension is enabled for
    clients such as QwenPaw; no other unstable features are implemented.
    """
    reader, writer = await stdio_streams(limit=50 * 1024 * 1024)
    server = ACPAgentServer(agent_factory)
    connection = AgentSideConnection(
        server,
        writer,
        reader,
        listening=False,
        use_unstable_protocol=True,
    )
    stdout = cast(TextIO, _ProtocolStdout(sys.stdout.buffer))
    with redirect_stdout(stdout):
        try:
            await connection.listen()
        finally:
            try:
                await server.aclose()
            finally:
                await connection.close()
