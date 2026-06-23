# -*- coding: utf-8 -*-
"""Actor view filesystem isolation tests."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access

import os
import tempfile
from unittest import IsolatedAsyncioTestCase

from agentscope.workspace import LocalWorkspace, WorkspaceActor, WorkspaceView


class TestWorkspaceViewIsolation(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = LocalWorkspace(workdir=self.tmp.name)
        await self.workspace.initialize()
        self.actor = WorkspaceActor(
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            role="worker",
            capabilities={"workspace.publish"},
        )
        self.view = WorkspaceView(self.workspace, self.actor)

    async def asyncTearDown(self) -> None:
        await self.workspace.close()
        self.tmp.cleanup()

    async def test_worker_file_tools_are_private_and_have_no_bash(
        self,
    ) -> None:
        tools = await self.view.list_tools()
        self.assertNotIn("Bash", [tool.name for tool in tools])
        write = next(tool for tool in tools if tool.name == "Write")
        private_file = os.path.join(self.view.workdir, "note.txt")
        await write._backend.write_file(private_file, b"private")
        self.assertEqual(
            await write._backend.read_file(private_file), b"private"
        )

        with self.assertRaises(PermissionError):
            await write._backend.read_file(
                os.path.join(self.tmp.name, "other-agent.txt"),
            )
        with self.assertRaises(PermissionError):
            await write._backend.write_file(
                os.path.join(self.tmp.name, "shared", "direct.txt"),
                b"not published",
            )

    async def test_publish_is_atomic_and_rejects_conflict(self) -> None:
        os.makedirs(self.view.workdir, exist_ok=True)
        source = os.path.join(self.view.workdir, "result.txt")
        with open(source, "wb") as stream:
            stream.write(b"result")

        published = await self.view.publish_file(source, "reports/result.txt")
        with open(published, "rb") as stream:
            self.assertEqual(stream.read(), b"result")
        with self.assertRaises(FileExistsError):
            await self.view.publish_file(source, "reports/result.txt")

    async def test_cannot_offload_for_another_session(self) -> None:
        with self.assertRaises(PermissionError):
            await self.view.offload_context("session-2", [])

    async def test_private_symlink_cannot_escape_view(self) -> None:
        tools = await self.view.list_tools()
        read = next(tool for tool in tools if tool.name == "Read")
        outside = os.path.join(self.tmp.name, "outside.txt")
        with open(outside, "wb") as stream:
            stream.write(b"secret")
        link = os.path.join(self.view.workdir, "escape.txt")
        os.symlink(outside, link)

        with self.assertRaises(PermissionError):
            await read._backend.read_file(link)
