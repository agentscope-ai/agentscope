# -*- coding: utf-8 -*-
"""MCP library rename test cases."""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import MCPRecord, RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.mcp import HttpMCPConfig, MCPClient

HEADERS = {"X-User-ID": "alice"}


def _fake_storage() -> Any:
    """Build a fakeredis-backed storage and message bus."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    class _Storage(RedisStorage):
        """Storage sharing the in-memory client."""

        async def __aenter__(self) -> Any:
            self._client = redis
            return self

        async def aclose(self) -> None:
            self._client = None

    class _Bus(RedisMessageBus):
        """Message bus sharing the in-memory client."""

        async def __aenter__(self) -> Any:
            self._client = redis
            return self

        async def aclose(self) -> None:
            self._client = None

    return _Storage(), _Bus()


class MCPLibraryRenameTest(IsolatedAsyncioTestCase):
    """``PATCH /mcp/{mcp_id}`` must not store a name the library cannot
    load back."""

    def setUp(self) -> None:
        """Start an app holding one installed MCP named ``echo``."""
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        self._storage, bus = _fake_storage()
        app = create_app(
            storage=self._storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
        )
        self._client = self.enterContext(TestClient(app))

    async def asyncSetUp(self) -> None:
        """Install the fixture MCP straight into storage."""
        record = MCPRecord(
            user_id="alice",
            client=MCPClient(
                name="echo",
                is_stateful=False,
                mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
            ),
        )
        await self._storage.upsert_mcp("alice", record)
        self._mcp_id = record.id

    def _view(self, name: str) -> dict:
        """The expected ``MCPView`` of the fixture MCP under *name*."""
        return {
            "id": self._mcp_id,
            "name": name,
            "is_stateful": False,
            "enabled": True,
            "display_name": None,
            "description": "",
            "tags": [],
            "author": None,
            "icon_url": None,
            "url": None,
            "hub_id": None,
            "card_id": None,
            "version": None,
        }

    async def test_rename_to_unloadable_name_is_rejected(self) -> None:
        """A name ``MCPClient`` would refuse on load answers 422 and leaves
        the library readable."""
        response = self._client.patch(
            f"/mcp/{self._mcp_id}",
            json={"name": "io.github.upstash/context7"},
            headers=HEADERS,
        )
        self.assertEqual(response.status_code, 422)

        listing = self._client.get("/mcp", headers=HEADERS)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json(), [self._view("echo")])

    async def test_rename_to_empty_name_is_rejected(self) -> None:
        """An empty name is rejected rather than stored."""
        response = self._client.patch(
            f"/mcp/{self._mcp_id}",
            json={"name": ""},
            headers=HEADERS,
        )
        self.assertEqual(response.status_code, 422)

    async def test_legal_rename_persists(self) -> None:
        """A name within ``[a-zA-Z0-9_-]+`` still renames the MCP."""
        response = self._client.patch(
            f"/mcp/{self._mcp_id}",
            json={"name": "echo_2"},
            headers=HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self._view("echo_2"))

        listing = self._client.get("/mcp", headers=HEADERS)
        self.assertEqual(listing.json(), [self._view("echo_2")])
