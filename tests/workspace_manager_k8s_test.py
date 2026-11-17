# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for :class:`K8sWorkspaceManager`'s shared ApiClient."""

import asyncio
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.app.workspace_manager import K8sWorkspaceManager
from agentscope.workspace import K8sWorkspace

_LOAD_CLIENT_TARGET = (
    "agentscope.app.workspace_manager._k8s_workspace_manager."
    "_load_api_client"
)


def _make_client() -> MagicMock:
    """A fake ApiClient whose ``close`` is awaitable."""
    client = MagicMock()
    client.close = AsyncMock()
    return client


class _FakeWorkspace:
    """Workspace double recording the kwargs it was built with."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")

    async def initialize(self) -> None:
        """No-op."""

    async def close(self) -> None:
        """No-op."""


class TestK8sManagerSharedClient(IsolatedAsyncioTestCase):
    """The manager owns one ApiClient shared by every workspace."""

    async def test_one_shared_client_for_all_workspaces(self) -> None:
        """Every built workspace receives the same manager-wide client."""
        manager = K8sWorkspaceManager()
        client = _make_client()
        load = AsyncMock(return_value=client)
        with (
            patch(_LOAD_CLIENT_TARGET, load),
            patch(
                "agentscope.app.workspace_manager."
                "_k8s_workspace_manager.K8sWorkspace",
                _FakeWorkspace,
            ),
        ):
            ws1 = await manager.get_workspace(
                "u1",
                "a1",
                "s1",
                workspace_id="w1",
            )
            ws2 = await manager.get_workspace(
                "u2",
                "a2",
                "s2",
                workspace_id="w2",
            )
        self.assertIs(ws1.kwargs["api_client"], client)
        self.assertIs(ws2.kwargs["api_client"], client)
        load.assert_awaited_once()

    async def test_concurrent_first_use_loads_config_once(self) -> None:
        """Concurrent first uses serialize on the shared client build."""
        manager = K8sWorkspaceManager()
        load = AsyncMock(return_value=_make_client())
        with patch(_LOAD_CLIENT_TARGET, load):
            results = await asyncio.gather(
                manager._ensure_api_client(),
                manager._ensure_api_client(),
            )
        load.assert_awaited_once()
        self.assertIs(results[0], results[1])
        self.assertIs(manager._api_client, results[0])

    async def test_aexit_closes_shared_client(self) -> None:
        """__aexit__ closes the shared client once and clears the slot."""
        manager = K8sWorkspaceManager()
        client = _make_client()
        manager._api_client = client
        await manager.__aexit__(None, None, None)
        client.close.assert_awaited_once()
        self.assertIsNone(manager._api_client)

    async def test_ensure_rebuilds_client_after_aexit(self) -> None:
        """A later __aenter__ cycle builds a fresh client."""
        manager = K8sWorkspaceManager()
        load = AsyncMock(side_effect=[_make_client(), _make_client()])
        with patch(_LOAD_CLIENT_TARGET, load):
            first = await manager._ensure_api_client()
            await manager.__aexit__(None, None, None)
            second = await manager._ensure_api_client()
        self.assertIsNot(first, second)
        self.assertEqual(load.await_count, 2)


class TestK8sWorkspaceClientOwnership(IsolatedAsyncioTestCase):
    """A workspace only closes an ApiClient it created itself."""

    async def test_owned_client_is_closed_on_teardown(self) -> None:
        """A standalone workspace builds and closes its own client."""
        ws = K8sWorkspace(workspace_id="w1")
        client = _make_client()
        ws._api_client = client
        ws._v1 = AsyncMock()
        ws._pod_name = "as-ws-w1"
        await ws._teardown_backend()
        client.close.assert_awaited_once()
        self.assertIsNone(ws._api_client)

    async def test_external_client_is_not_closed_on_teardown(self) -> None:
        """A manager-supplied client outlives the workspace teardown."""
        client = _make_client()
        ws = K8sWorkspace(workspace_id="w1", api_client=client)
        ws._api_client = client
        ws._v1 = AsyncMock()
        ws._pod_name = "as-ws-w1"
        await ws._teardown_backend()
        client.close.assert_not_called()
        self.assertIsNone(ws._api_client)
