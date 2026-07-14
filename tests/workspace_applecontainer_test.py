# -*- coding: utf-8 -*-
# pylint: disable=protected-access
# mypy: disable-error-code="misc,no-untyped-def,attr-defined"
"""Test cases for :class:`AppleContainerWorkspace`.

Runs against a real Apple Container via the ``container`` CLI.
Requires ``container`` CLI installed and ``container system start``
running.
"""

import os
import shutil
import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.workspace import AppleContainerBackend, AppleContainerWorkspace
from agentscope.workspace._applecontainer._constants import (
    CONTAINER_WORKDIR,
    DEFAULT_BASE_IMAGE,
)

_CONTAINER_CLI = shutil.which("container")
_RUN_REASON = "container CLI not found — install Apple Container first"


@unittest.skipUnless(
    sys.platform == "darwin",
    "Apple Container requires macOS",
)
class TestAppleContainerWorkspaceConstruct(unittest.TestCase):
    """Constructor and config tests — no container needed."""

    def test_default_values(self) -> None:
        """Constructor picks up all the default constants."""
        ws = AppleContainerWorkspace()
        self.assertEqual(ws.workdir, CONTAINER_WORKDIR)
        self.assertEqual(ws.base_image, DEFAULT_BASE_IMAGE)
        self.assertEqual(ws.cpus, 2)
        self.assertEqual(ws.memory, "2G")
        self.assertFalse(ws.is_alive)

    def test_custom_values(self) -> None:
        """All config knobs are forwarded."""
        ws = AppleContainerWorkspace(
            workspace_id="my-ws",
            base_image="ubuntu:latest",
            gateway_port=9999,
            cpus=4,
            memory="8G",
            env={"FOO": "bar"},
            extra_pip=["requests"],
        )
        self.assertEqual(ws.workspace_id, "my-ws")
        self.assertEqual(ws.base_image, "ubuntu:latest")
        self.assertEqual(ws.gateway_port, 9999)
        self.assertEqual(ws.cpus, 4)
        self.assertEqual(ws.memory, "8G")
        self.assertEqual(ws.env, {"FOO": "bar"})
        self.assertEqual(ws.extra_pip, ["requests"])

    def test_instructions_substitution(self) -> None:
        """System prompt contains the container workdir."""
        import asyncio

        ws = AppleContainerWorkspace()
        loop = asyncio.get_event_loop()
        text = loop.run_until_complete(ws.get_instructions())
        self.assertIn(CONTAINER_WORKDIR, text)


@unittest.skipUnless(_CONTAINER_CLI, _RUN_REASON)
@unittest.skipUnless(
    sys.platform == "darwin",
    "Apple Container requires macOS",
)
class TestAppleContainerWorkspaceLifecycle(IsolatedAsyncioTestCase):
    """Lifecycle tests against a real Apple Container."""

    async def test_initialize_and_close(self) -> None:
        """Workspace can be initialized and closed."""
        ws = AppleContainerWorkspace()
        self.assertFalse(ws.is_alive)

        async with ws:
            self.assertTrue(ws.is_alive)
            self.assertIsNotNone(ws._backend)
            self.assertIsInstance(ws._backend, AppleContainerBackend)

        self.assertFalse(ws.is_alive)

    async def test_exec_shell_inside_workspace(self) -> None:
        """Commands run inside the container via the workspace."""
        ws = AppleContainerWorkspace()
        async with ws:
            backend = ws.get_backend()
            result = await backend.exec_shell(["echo", "hello from ws"])
            self.assertTrue(result.ok())
            self.assertEqual(
                result.stdout.strip(),
                b"hello from ws",
            )

    async def test_read_write_inside_workspace(self) -> None:
        """Files can be written and read inside the workspace container."""
        ws = AppleContainerWorkspace()
        async with ws:
            backend = ws.get_backend()
            path = f"{CONTAINER_WORKDIR}/ws_test.txt"
            await backend.write_file(path, b"workspace data")
            data = await backend.read_file(path)
            self.assertEqual(data, b"workspace data")

    async def test_list_tools(self) -> None:
        """``list_tools`` returns the 6 builtin tools bound to backend."""
        ws = AppleContainerWorkspace()
        async with ws:
            tools = await ws.list_tools()
            tool_names = [t.name for t in tools]
            for name in ("Bash", "Read", "Write", "Edit", "Glob", "Grep"):
                self.assertIn(name, tool_names)

    async def test_initialize_idempotent(self) -> None:
        """Second initialize is a no-op."""
        ws = AppleContainerWorkspace()
        await ws.initialize()
        self.assertTrue(ws.is_alive)
        backend_before = ws._backend

        # Second call should be a no-op.
        await ws.initialize()
        self.assertIs(ws._backend, backend_before)

        await ws.close()


_APPLE_CONTAINER_LIVE = os.getenv("APPLE_CONTAINER_LIVE", "")


@unittest.skipUnless(_APPLE_CONTAINER_LIVE, "APPLE_CONTAINER_LIVE not set")
@unittest.skipUnless(_CONTAINER_CLI, _RUN_REASON)
@unittest.skipUnless(
    sys.platform == "darwin",
    "Apple Container requires macOS",
)
class TestAppleContainerWorkspaceLive(IsolatedAsyncioTestCase):
    """Live integration tests — full bootstrap + gateway lifecycle.

    Set ``APPLE_CONTAINER_LIVE=1`` to run. This test pulls an image,
    bootstraps the gateway venv, and verifies the full lifecycle.
    """

    async def test_full_lifecycle(self) -> None:
        """Create, use, and close a real Apple Container workspace."""
        ws = AppleContainerWorkspace()
        async with ws:
            self.assertTrue(ws.is_alive)
            backend = ws.get_backend()
            self.assertIsInstance(backend, AppleContainerBackend)

            result = await backend.exec_shell(["echo", "hello from e2e"])
            self.assertTrue(result.ok())
            self.assertEqual(
                result.stdout.strip(),
                b"hello from e2e",
            )

            path = f"{CONTAINER_WORKDIR}/e2e_test.txt"
            await backend.write_file(path, b"e2e test data")
            data = await backend.read_file(path)
            self.assertEqual(data, b"e2e test data")

        self.assertFalse(ws.is_alive)
