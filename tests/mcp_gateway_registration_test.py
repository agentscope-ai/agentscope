# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Gateway registration retains cleanup ownership through tool discovery."""

import asyncio
from unittest.mock import AsyncMock, patch

from unittest import IsolatedAsyncioTestCase

from agentscope.workspace._mcp_gateway import _mcp_gateway_app as gateway


async def _assert_lifecycle(
    outcome: str,
) -> None:
    """Publish successful clients; reclaim failed and cancelled discovery."""
    client = AsyncMock()
    client.is_stateful = True
    client.is_connected = False
    entered = asyncio.Event()
    release = asyncio.Event()
    failure = RuntimeError("discovery failed")

    async def connect() -> None:
        """Model acquisition of a stateful connection."""
        client.is_connected = True

    async def discover() -> list[object]:
        """Pause at discovery after the connection is established."""
        entered.set()
        await release.wait()
        if outcome == "error":
            raise failure
        return []

    client.connect.side_effect = connect
    client.list_raw_tools.side_effect = discover
    with patch.object(
        gateway.MCPClient, "model_validate", return_value=client
    ):
        task = asyncio.create_task(gateway._build_client({}))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if outcome == "cancel":
                task.cancel()
            release.set()
            if outcome == "success":
                assert await task is client
                client.close.assert_not_awaited()
            else:
                result = (await asyncio.gather(task, return_exceptions=True))[
                    0
                ]
                if outcome == "error":
                    assert result is failure
                else:
                    assert isinstance(result, asyncio.CancelledError)
                client.close.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


class TestLifecycle(IsolatedAsyncioTestCase):
    """Exercise the production lifecycle at controlled handoff points."""

    async def test_handoff_outcomes(self) -> None:
        """Preserve successful work and reclaim interrupted acquisition."""
        for outcome in ["success", "error", "cancel"]:
            with self.subTest(outcome=outcome):
                await _assert_lifecycle(outcome)
