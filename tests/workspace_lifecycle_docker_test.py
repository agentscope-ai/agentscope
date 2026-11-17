# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Opt-in real Docker/Redis validation of the workspace lifecycle.

Run from the checkout root:
    AGENTSCOPE_DOCKER_LIFECYCLE_TEST=1 python -m pytest \
        tests/workspace_lifecycle_docker_test.py -v

Uses temporary Alpine and Redis containers; never touches existing ones.
OpenSandbox provisioning is replaced by Docker pause/unpause, while the
actual manager and Redis coordinator run unchanged in separate processes.
"""

import asyncio
import multiprocessing
import os
import subprocess
import unittest
import uuid
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.workspace_manager import (
    OpenSandboxWorkspaceManager,
    RedisWorkspaceLifecycle,
)

_TARGET = (
    "agentscope.app.workspace_manager."
    "_opensandbox_workspace_manager.OpenSandboxWorkspace"
)


def _docker(*args: str, check: bool = True) -> str:
    """Run a bounded Docker command."""
    result = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=check,
    )
    return result.stdout.strip()


class _DockerWorkspace:
    """Local connection adapter to a shared real container."""

    def __init__(self, **kwargs: Any) -> None:
        self.workspace_id = kwargs["workspace_id"]
        self.detached = False

    async def initialize(self) -> None:
        """Resume before exposing a usable backend."""
        state = _docker(
            "inspect",
            "-f",
            "{{.State.Paused}}",
            self.workspace_id,
        )
        if state == "true":
            _docker("unpause", self.workspace_id)

    async def release_local(self) -> None:
        """Close the local adapter only."""
        self.detached = True

    async def pause_remote(self) -> None:
        """Pause the shared container."""
        _docker("pause", self.workspace_id)

    def read(self) -> str:
        """Read from the real container; fails if paused or detached."""
        assert not self.detached, "Local connection was closed while in use"
        return _docker("exec", self.workspace_id, "cat", "/tmp/shared")


def _manager(client: Any, namespace: str) -> OpenSandboxWorkspaceManager:
    """Construct the production manager with a shared Redis coordinator."""
    return OpenSandboxWorkspaceManager(
        lifecycle=RedisWorkspaceLifecycle(client, namespace),
        ttl=0.2,
        usage_lease_seconds=2,
        sweep_interval=60,
    )


def _node_a(
    url: str,
    namespace: str,
    container: str,
    ready: Any,
    active: Any,
) -> None:
    """Run A in a separate process with its own cache and Redis client."""

    async def run() -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(url)
        try:
            with patch(_TARGET, _DockerWorkspace):
                manager = _manager(client, namespace)
                async with manager.acquire_workspace("u", "a", "s", container):
                    pass
                ready.set()
                assert await asyncio.to_thread(active.wait, 15)
                await asyncio.sleep(0.3)
                await manager._sweep_once()
                await manager.__aexit__()
                assert (
                    _docker(
                        "inspect",
                        "-f",
                        "{{.State.Paused}}",
                        container,
                    )
                    == "false"
                )
        finally:
            await client.aclose()

    asyncio.run(run())


@unittest.skipUnless(
    os.getenv("AGENTSCOPE_DOCKER_LIFECYCLE_TEST") == "1",
    "Opt-in Docker/Redis integration test",
)
class TestDockerWorkspaceLifecycle(IsolatedAsyncioTestCase):
    """Validate long turns, node exit, acquisition races, and crashes."""

    @classmethod
    def setUpClass(cls) -> None:
        """Start a dedicated Redis server on a random loopback port."""
        cls.redis_name = "agentscope-ttl-redis-" + uuid.uuid4().hex[:8]
        password = uuid.uuid4().hex
        _docker(
            "run",
            "-d",
            "--name",
            cls.redis_name,
            "-p",
            "127.0.0.1::6379",
            "--entrypoint",
            "redis-server",
            "redis/redis-stack:latest",
            "--save",
            "",
            "--appendonly",
            "no",
            "--requirepass",
            password,
        )
        port = _docker(
            "inspect",
            "-f",
            '{{(index (index .NetworkSettings.Ports "6379/tcp") 0).HostPort}}',
            cls.redis_name,
        )
        cls.url = f"redis://:{password}@127.0.0.1:{port}/0"

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the dedicated Redis server."""
        _docker("rm", "-f", cls.redis_name, check=False)

    async def asyncSetUp(self) -> None:
        """Create an isolated shared sandbox and independent clients."""
        from redis.asyncio import Redis

        self.container = "agentscope-ttl-sandbox-" + uuid.uuid4().hex[:8]
        self.namespace = uuid.uuid4().hex
        _docker(
            "run",
            "-d",
            "--name",
            self.container,
            "alpine:3.20",
            "sh",
            "-c",
            "while :; do sleep 3600; done",
        )
        self.addCleanup(self._remove_container)
        _docker(
            "exec",
            self.container,
            "sh",
            "-c",
            "echo shared-data > /tmp/shared",
        )
        self.clients = [Redis.from_url(self.url) for _ in range(2)]
        await self.clients[0].ping()
        self.patch = patch(_TARGET, _DockerWorkspace)
        self.patch.start()
        self.first = _manager(self.clients[0], self.namespace)
        self.second = _manager(self.clients[1], self.namespace)

    async def asyncTearDown(self) -> None:
        """Release handles and remove only this test's container."""
        await self.first.close_all()
        await self.second.close_all()
        for client in self.clients:
            await client.aclose()
        self.patch.stop()

    def _remove_container(self) -> None:
        """Clean up even when asyncSetUp fails."""
        _docker("unpause", self.container, check=False)
        _docker("rm", "-f", self.container, check=False)

    async def test_long_turn_survives_ttl_and_lease_periods(self) -> None:
        """Repeated sweeps cannot pause a long active turn."""
        async with self.first.acquire_workspace(
            "u",
            "a",
            "s",
            self.container,
        ) as ws:
            for _ in range(8):
                await asyncio.sleep(0.3)
                await self.first._sweep_once()
                self.assertEqual(ws.read(), "shared-data")
        await asyncio.sleep(0.25)
        await self.first._sweep_once()
        self.assertEqual(
            _docker(
                "inspect",
                "-f",
                "{{.State.Paused}}",
                self.container,
            ),
            "true",
        )
        print("PASS: long turn survives TTL/lease; pauses after idle")

    async def test_two_processes_and_node_shutdown(self) -> None:
        """A expires and exits while B continues using the same sandbox."""
        context = multiprocessing.get_context("spawn")
        ready, active = context.Event(), context.Event()
        process = context.Process(
            target=_node_a,
            args=(self.url, self.namespace, self.container, ready, active),
        )
        process.start()
        try:
            self.assertTrue(await asyncio.to_thread(ready.wait, 15))
            async with self.second.acquire_workspace(
                "u",
                "a",
                "s",
                self.container,
            ) as ws:
                active.set()
                await asyncio.to_thread(process.join, 15)
                self.assertEqual(process.exitcode, 0)
                self.assertEqual(ws.read(), "shared-data")
            await asyncio.sleep(0.25)
            await self.second._sweep_once()
            self.assertEqual(
                _docker(
                    "inspect",
                    "-f",
                    "{{.State.Paused}}",
                    self.container,
                ),
                "true",
            )
            print("PASS: node A TTL and shutdown do not interrupt node B")
        finally:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)

    async def test_new_user_waits_for_pause_then_resumes(self) -> None:
        """A cached B handle reconnects after serialized remote pause."""
        async with self.second.acquire_workspace(
            "u",
            "a",
            "s",
            self.container,
        ) as stale:
            pass
        await asyncio.sleep(0.25)
        started, proceed = asyncio.Event(), asyncio.Event()
        original = _DockerWorkspace.pause_remote

        async def delayed_pause(ws: _DockerWorkspace) -> None:
            started.set()
            await proceed.wait()
            await original(ws)

        with patch.object(_DockerWorkspace, "pause_remote", delayed_pause):
            collector = asyncio.create_task(self.first._sweep_once())
            await started.wait()
            admitted = asyncio.Event()

            async def use() -> None:
                async with self.second.acquire_workspace(
                    "u",
                    "a",
                    "s",
                    self.container,
                ) as ws:
                    admitted.set()
                    self.assertTrue(stale.detached)
                    self.assertEqual(ws.read(), "shared-data")

            borrower = asyncio.create_task(use())
            await asyncio.sleep(0.1)
            self.assertFalse(admitted.is_set())
            proceed.set()
            await asyncio.wait_for(asyncio.gather(collector, borrower), 10)
        print("PASS: acquisition waits for pause, then resumes successfully")

    async def test_orphaned_lease_is_reclaimed(self) -> None:
        """A crashed borrower cannot keep a sandbox running forever."""
        lifecycle = self.first._lifecycle
        await lifecycle.update(self.container, "acquire", "crashed", 0.2)
        await self.second._sweep_once()
        self.assertEqual(
            _docker(
                "inspect",
                "-f",
                "{{.State.Paused}}",
                self.container,
            ),
            "false",
        )
        await asyncio.sleep(0.5)
        await self.second._sweep_once()
        self.assertEqual(
            _docker(
                "inspect",
                "-f",
                "{{.State.Paused}}",
                self.container,
            ),
            "true",
        )
        print("PASS: expired orphan usage lease permits idle collection")


if __name__ == "__main__":
    unittest.main()
