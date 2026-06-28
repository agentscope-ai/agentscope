# -*- coding: utf-8 -*-
"""Shared live contracts for remote workspace backends.

E2B and OpenSandbox both implement the same remote workspace shape:
command execution, sandbox-side file persistence, builtin tool binding,
and manager-level cache/close behavior. These mixins keep the live
assertions identical across backends so new remote implementations prove
the same capabilities instead of drifting into backend-specific test
coverage.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from agentscope.tool import ExecResult


class RemoteBackendContractMixin:
    """Backend primitives every remote sandbox backend must satisfy."""

    backend = None
    workdir = ""

    async def test_contract_exec_returns_stdout(self) -> None:
        result = await self.backend.exec_shell(["echo", "hello world"])
        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), "hello world")

    async def test_contract_exec_nonzero_exit(self) -> None:
        result = await self.backend.exec_shell(
            ["sh", "-c", "echo oops >&2; exit 4"],
        )
        self.assertEqual(result.exit_code, 4)
        self.assertIn("oops", result.stderr.decode())

    async def test_contract_exec_argv_quoting_preserved(self) -> None:
        tricky = "a b $(echo x) | ;"
        result = await self.backend.exec_shell(["echo", tricky])
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().rstrip("\n"), tricky)

    async def test_contract_write_read_binary_roundtrip(self) -> None:
        path = f"{self.workdir}/contract-{uuid.uuid4().hex}.bin"
        payload = b"a\r\nb\x00\xffc"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_contract_helpers(self) -> None:
        base = f"{self.workdir}/contract-{uuid.uuid4().hex}"
        await self.backend.write_file(f"{base}/a.txt", b"x")
        await self.backend.write_file(f"{base}/b.txt", b"y")
        self.assertTrue(await self.backend.file_exists(f"{base}/a.txt"))
        self.assertTrue(await self.backend.is_dir(base))
        self.assertEqual(sorted(await self.backend.list_dir(base)), ["a.txt", "b.txt"])
        self.assertIsInstance(await self.backend.stat_mtime(f"{base}/a.txt"), float)
        await self.backend.delete_path(base)
        self.assertFalse(await self.backend.file_exists(base))


class RemoteWorkspaceContractMixin:
    """Workspace-level contracts shared by remote sandbox workspaces."""

    workspace = None
    workspace_cls = None
    workspace_reopen_kwargs = None

    async def test_contract_list_tools(self) -> None:
        tools = await self.workspace.list_tools()
        self.assertEqual(
            [tool.name for tool in tools],
            ["Bash", "Edit", "Glob", "Grep", "Read", "Write"],
        )

    async def test_contract_close_and_reattach_preserves_file(self) -> None:
        path = f"{self.workspace.workdir}/reattach.txt"
        backend = self.workspace._backend
        await backend.write_file(path, b"persisted")
        workspace_id = self.workspace.workspace_id
        await self.workspace.close()
        resumed = self.workspace_cls(
            workspace_id=workspace_id,
            **(self.workspace_reopen_kwargs or {}),
        )
        await resumed.initialize()
        try:
            self.assertEqual(await resumed._backend.read_file(path), b"persisted")
        finally:
            await resumed.close()


class RemoteWorkspaceManagerContractMixin:
    """Manager-level cache and shutdown contracts for remote backends."""

    manager = None

    async def test_contract_create_workspace_initializes_and_caches(self) -> None:
        created = AsyncMock()
        created.workspace_id = "created-id"
        self.manager._build_and_start = AsyncMock(return_value=created)

        got = await self.manager.create_workspace("u", "a", "s")

        self.assertIs(got, created)
        self.manager._build_and_start.assert_awaited_once_with(
            workspace_id=None,
            user_id="u",
            agent_id="a",
        )
        self.assertIn("created-id", self.manager._cache)
        self.assertIs(self.manager._cache["created-id"][0], created)

    async def test_contract_get_workspace_reattaches_once_and_caches(self) -> None:
        resumed = AsyncMock()
        resumed.workspace_id = "requested-id"
        self.manager._build_and_start = AsyncMock(return_value=resumed)

        first = await self.manager.get_workspace("u", "a", "s", "requested-id")
        second = await self.manager.get_workspace("u", "a", "s", "requested-id")

        self.assertIs(first, resumed)
        self.assertIs(second, resumed)
        self.manager._build_and_start.assert_awaited_once_with(
            workspace_id="requested-id",
            user_id="u",
            agent_id="a",
        )
        self.assertIs(self.manager._cache["requested-id"][0], resumed)

    async def test_contract_close_all_closes_cached_workspaces(self) -> None:
        ws1 = AsyncMock()
        ws1.workspace_id = "w1"
        ws2 = AsyncMock()
        ws2.workspace_id = "w2"
        self.manager._cache = {
            "w1": (ws1, 0.0),
            "w2": (ws2, 0.0),
        }

        await self.manager.close_all()

        ws1.close.assert_awaited_once()
        ws2.close.assert_awaited_once()
        self.assertEqual(self.manager._cache, {})
