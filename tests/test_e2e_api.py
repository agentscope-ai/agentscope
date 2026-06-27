# -*- coding: utf-8 -*-
"""E2E test: per-agent MCP isolation via HTTP API (pytest).

Requires: Redis running on localhost:6379
"""
# pylint: disable=redefined-outer-name
import asyncio
import tempfile
import os
import threading

import httpx
import pytest
import uvicorn

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.mcp import MCPClient, HttpMCPConfig

WORKDIR = os.path.join(tempfile.mkdtemp(), "workspaces")
HEADERS = {"X-User-ID": "test-user"}
PORT = 8765

_app = create_app(
    storage=RedisStorage(host="localhost", port=6379),
    message_bus=RedisMessageBus(host="localhost", port=6379),
    workspace_manager=LocalWorkspaceManager(
        basedir=WORKDIR,
        default_mcps=[
            MCPClient(
                name="default-search",
                is_stateful=False,
                mcp_config=HttpMCPConfig(url="http://127.0.0.1:1/mcp"),
            ),
        ],
    ),
)
_server = None
_thread = None


def _setup_module() -> None:
    """Start uvicorn in background thread."""
    global _server, _thread
    config = uvicorn.Config(
        _app,
        host="127.0.0.1",
        port=PORT,
        log_level="warning",
    )
    _server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(_server.serve())

    _thread = threading.Thread(target=_run, daemon=True)
    _thread.start()

    # Wait for readiness
    async def _wait() -> None:
        async with httpx.AsyncClient(timeout=5.0) as c:
            for _ in range(50):
                try:
                    r = await c.get(
                        f"http://127.0.0.1:{PORT}/openapi.json",
                    )
                    if r.status_code == 200:
                        return
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            raise RuntimeError("Server did not start")

    asyncio.run(_wait())


def _teardown_module() -> None:
    """Stop uvicorn."""
    if _server is not None:
        _server.should_exit = True


_setup_module()


@pytest.fixture
def client() -> httpx.AsyncClient:
    """httpx client for each test."""

    async def _get() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{PORT}",
            headers=HEADERS,
            timeout=30.0,
        )

    return asyncio.run(_get())


class TestPerAgentMCPAPI:
    """Per-agent MCP isolation via full HTTP API stack."""

    async def _create_agent(
        self,
        client: httpx.AsyncClient,
        name: str,
    ) -> str:
        resp = await client.post(
            "/agent/",
            json={"name": name, "system_prompt": "You are helpful."},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["agent_id"]

    async def _create_session(
        self,
        client: httpx.AsyncClient,
        agent_id: str,
    ) -> str:
        resp = await client.post(
            "/sessions/",
            json={"agent_id": agent_id},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["session_id"]

    async def _list_mcps(
        self,
        client: httpx.AsyncClient,
        agent_id: str,
        session_id: str,
    ) -> list[str]:
        resp = await client.get(
            "/workspace/mcp",
            params={"agent_id": agent_id, "session_id": session_id},
        )
        assert resp.status_code == 200, resp.text
        return [m["name"] for m in resp.json()]

    @pytest.mark.asyncio
    async def test_lazy_clone_both_agents_get_default_search(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        """Leader and worker each get default-search via lazy clone."""
        leader = await self._create_agent(client, "Leader")
        worker = await self._create_agent(client, "Worker")
        lsid = await self._create_session(client, leader)
        wsid = await self._create_session(client, worker)

        l_names = await self._list_mcps(client, leader, lsid)
        assert "default-search" in l_names
        w_names = await self._list_mcps(client, worker, wsid)
        assert "default-search" in w_names

        # cleanup
        await client.delete(f"/sessions/{lsid}")
        await client.delete(f"/sessions/{wsid}")
        await client.delete(f"/agent/{leader}")
        await client.delete(f"/agent/{worker}")

    @pytest.mark.asyncio
    async def test_add_mcp_per_agent_isolation(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        """Leader adds MCP; worker does NOT see it."""
        leader = await self._create_agent(client, "LeaderAdd")
        worker = await self._create_agent(client, "WorkerAdd")
        lsid = await self._create_session(client, leader)
        wsid = await self._create_session(client, worker)

        resp = await client.post(
            "/workspace/mcp",
            json=MCPClient(
                name="leader-extra",
                is_stateful=False,
                mcp_config=HttpMCPConfig(url="http://127.0.0.1:1/mcp"),
            ).model_dump(),
            params={"agent_id": leader, "session_id": lsid},
        )
        assert resp.status_code == 201

        l_names = await self._list_mcps(client, leader, lsid)
        assert "leader-extra" in l_names
        w_names = await self._list_mcps(client, worker, wsid)
        assert "leader-extra" not in w_names

        await client.delete(f"/sessions/{lsid}")
        await client.delete(f"/sessions/{wsid}")
        await client.delete(f"/agent/{leader}")
        await client.delete(f"/agent/{worker}")

    @pytest.mark.asyncio
    async def test_duplicate_mcp_returns_409(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        """Same name within same agent returns 409."""
        leader = await self._create_agent(client, "LeaderDup")
        lsid = await self._create_session(client, leader)

        mcp = MCPClient(
            name="dup-me",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="http://127.0.0.1:1/mcp"),
        )
        resp = await client.post(
            "/workspace/mcp",
            json=mcp.model_dump(),
            params={"agent_id": leader, "session_id": lsid},
        )
        assert resp.status_code == 201

        resp = await client.post(
            "/workspace/mcp",
            json=mcp.model_dump(),
            params={"agent_id": leader, "session_id": lsid},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

        await client.delete(f"/sessions/{lsid}")
        await client.delete(f"/agent/{leader}")

    @pytest.mark.asyncio
    async def test_remove_mcp_per_agent_isolation(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        """Remove from leader; worker unaffected."""
        leader = await self._create_agent(client, "LeaderRm")
        worker = await self._create_agent(client, "WorkerRm")
        lsid = await self._create_session(client, leader)
        wsid = await self._create_session(client, worker)

        mcp = MCPClient(
            name="to-remove",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="http://127.0.0.1:1/mcp"),
        )
        await client.post(
            "/workspace/mcp",
            json=mcp.model_dump(),
            params={"agent_id": leader, "session_id": lsid},
        )

        resp = await client.delete(
            "/workspace/mcp/to-remove",
            params={"agent_id": leader, "session_id": lsid},
        )
        assert resp.status_code == 204

        l_names = await self._list_mcps(client, leader, lsid)
        assert "to-remove" not in l_names

        await client.delete(f"/sessions/{lsid}")
        await client.delete(f"/sessions/{wsid}")
        await client.delete(f"/agent/{leader}")
        await client.delete(f"/agent/{worker}")
