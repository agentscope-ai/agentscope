# -*- coding: utf-8 -*-
"""Wire-level smoke test: the real JSON-RPC layer over a socket pair.

Runs the kernel through ``acp.run_agent`` and drives it with the SDK's
own client-side connection, so framing, request routing and response
serialization are exercised exactly as they are over stdio.
"""
import asyncio
import contextlib
import socket
from pathlib import Path

import pytest

from acp import connect_to_agent, text_block
from acp.schema import ClientCapabilities, FileSystemCapabilities
from acp import run_agent

import acp_example.agent as agent_mod
from acp_example.server import AgentScopeAcpAgent

from mock_client import FakeClient
from mock_model import MockModel
from test_server import make_config

from agentscope.message import TextBlock
from agentscope.model import ChatResponse


async def test_full_turn_over_socketpair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """initialize → session/new → prompt over the real RPC layer."""
    model = MockModel()
    model.set_responses(
        [
            [
                ChatResponse(content=[TextBlock(text="Hi ")], is_last=False),
                ChatResponse(content=[TextBlock(text="there")], is_last=False),
                ChatResponse(
                    content=[TextBlock(text="Hi there")],
                    is_last=True,
                ),
            ],
        ],
    )
    monkeypatch.setattr(agent_mod, "_make_model", lambda config: model)

    sock_agent, sock_client = socket.socketpair()
    agent_reader, agent_writer = await asyncio.open_connection(
        sock=sock_agent,
    )
    client_reader, client_writer = await asyncio.open_connection(
        sock=sock_client,
    )

    server_task = asyncio.create_task(
        run_agent(
            AgentScopeAcpAgent(config=make_config()),
            agent_writer,
            agent_reader,
        ),
    )
    client = FakeClient()
    conn = connect_to_agent(client, client_writer, client_reader)
    try:
        init = await asyncio.wait_for(
            conn.initialize(
                protocol_version=1,
                client_capabilities=ClientCapabilities(
                    fs=FileSystemCapabilities(
                        read_text_file=True,
                        write_text_file=True,
                    ),
                    terminal=True,
                ),
            ),
            timeout=10,
        )
        assert init.protocol_version == 1
        assert init.agent_info.name == "agentscope-acp"

        new = await asyncio.wait_for(
            conn.new_session(cwd=str(tmp_path)),
            timeout=10,
        )
        assert new.session_id

        resp = await asyncio.wait_for(
            conn.prompt(
                session_id=new.session_id,
                prompt=[text_block("hello")],
            ),
            timeout=10,
        )
        assert resp.stop_reason == "end_turn"
        chunks = client.updates_of("agent_message_chunk")
        assert "".join(c.content.text for c in chunks) == "Hi there"
    finally:
        server_task.cancel()
        with contextlib.suppress(
            asyncio.CancelledError,
            asyncio.IncompleteReadError,
        ):
            await server_task
        client_writer.close()
        agent_writer.close()
