# -*- coding: utf-8 -*-
"""Tests for actor-scoped MCP connection ownership."""

# pylint: disable=missing-class-docstring,missing-function-docstring

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.mcp import (
    HttpMCPConfig,
    MCPClient,
    MCPConnectionKey,
    MCPConnectionPool,
    MCPDefinition,
    MCPResourcePolicy,
)


class _FakeClient:
    def __init__(self) -> None:
        self.is_connected = False
        self.connect_count = 0
        self.close_count = 0

    async def connect(self) -> None:
        self.connect_count += 1
        await asyncio.sleep(0)
        self.is_connected = True

    async def close(self) -> None:
        self.close_count += 1
        self.is_connected = False


def _definition() -> MCPDefinition:
    return MCPDefinition.from_client(
        workspace_id="workspace-1",
        client=MCPClient(
            name="stateful",
            is_stateful=True,
            mcp_config=HttpMCPConfig(url="http://mcp.invalid/mcp"),
        ),
    )


class TestMCPConnectionPool(IsolatedAsyncioTestCase):
    async def test_single_flight_and_actor_session_isolation(self) -> None:
        definition = _definition()
        pool = MCPConnectionPool()
        created: list[_FakeClient] = []

        def factory(_definition: MCPDefinition) -> _FakeClient:
            client = _FakeClient()
            created.append(client)
            return client

        key_a = MCPConnectionKey.for_actor(
            definition,
            agent_id="agent-1",
            session_id="session-1",
        )
        key_b = MCPConnectionKey.for_actor(
            definition,
            agent_id="agent-1",
            session_id="session-2",
        )

        async def use(key: MCPConnectionKey) -> _FakeClient:
            async with pool.operation(definition, key) as client:
                await asyncio.sleep(0)
                return client

        with patch.object(MCPDefinition, "create_client", factory):
            first, second = await asyncio.gather(use(key_a), use(key_a))
            third = await use(key_b)

        self.assertIs(first, second)
        self.assertIsNot(first, third)
        self.assertEqual(len(created), 2)
        self.assertEqual([client.connect_count for client in created], [1, 1])
        await pool.close()

    async def test_active_operation_is_retired_after_release(self) -> None:
        definition = _definition()
        pool = MCPConnectionPool(MCPResourcePolicy(idle_ttl=0.01))
        client = _FakeClient()
        key = MCPConnectionKey.for_actor(
            definition,
            agent_id="agent-1",
            session_id="session-1",
        )

        with patch.object(
            MCPDefinition,
            "create_client",
            lambda _definition: client,
        ):
            async with pool.operation(definition, key):
                closed = await pool.close_scope(
                    workspace_id="workspace-1",
                    session_id="session-1",
                )
                self.assertEqual(closed, 0)
                self.assertEqual(client.close_count, 0)

        self.assertEqual(client.close_count, 1)

    async def test_session_quota_rejects_second_definition(self) -> None:
        pool = MCPConnectionPool(
            MCPResourcePolicy(max_stateful_per_session=1),
        )
        first = _definition()
        second = first.model_copy(update={"id": "other"})
        key_first = MCPConnectionKey.for_actor(
            first,
            agent_id="agent-1",
            session_id="session-1",
        )
        key_second = MCPConnectionKey.for_actor(
            second,
            agent_id="agent-1",
            session_id="session-1",
        )

        with patch.object(
            MCPDefinition,
            "create_client",
            lambda _definition: _FakeClient(),
        ):
            async with pool.operation(first, key_first):
                pass
            with self.assertRaisesRegex(RuntimeError, "session.*quota"):
                async with pool.operation(second, key_second):
                    pass
        await pool.close()
