# -*- coding: utf-8 -*-
"""Unit tests for workspace artifacts — declaration and forwards."""
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import ToolResultState
from agentscope.workspace import (
    ArtifactAdd,
    ArtifactRemove,
    LocalWorkspace,
    Upstream,
    WorkspaceBase,
)


class _CountingWorkspace(LocalWorkspace):
    """Counts how often a forward is actually opened and closed.

    Lets the tests distinguish "returned an upstream" from "opened a new
    one", which is the whole point of the declare/ensure split.
    """

    def __init__(self, **kwargs: object) -> None:
        """Start both counters at zero."""
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.opened = 0
        self.closed = 0

    async def _open_upstream(self, port: int) -> Upstream:
        """Record the open, then defer to the loopback implementation."""
        self.opened += 1
        return await super()._open_upstream(port)

    async def _close_upstream(self, port: int, upstream: Upstream) -> None:
        """Record the close."""
        self.closed += 1


class TestArtifactDeclaration(IsolatedAsyncioTestCase):
    """Declaring is bookkeeping only — it must not connect anything."""

    async def asyncSetUp(self) -> None:
        """Bring up a workspace over a throwaway directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _CountingWorkspace(workdir=self._tmp.name)
        await self.ws.initialize()

    async def asyncTearDown(self) -> None:
        """Close the workspace and drop the directory."""
        await self.ws.close()
        self._tmp.cleanup()

    async def test_declaring_opens_no_forward(self) -> None:
        """A declaration on its own must cost nothing."""
        self.ws.declare_artifact(3001)

        self.assertEqual(self.ws.opened, 0)
        self.assertFalse(self.ws.has_open_upstream())
        self.assertEqual([a.port for a in self.ws.list_artifacts()], [3001])

    async def test_redeclaring_keeps_the_id(self) -> None:
        """A viewer's URL embeds the id, so restating must not change it."""
        first = self.ws.declare_artifact(3001, title="Landing")
        again = self.ws.declare_artifact(3001, title="Landing v2")

        self.assertEqual(first.id, again.id)
        self.assertEqual(again.title, "Landing v2")
        self.assertEqual(len(self.ws.list_artifacts()), 1)

    async def test_redeclaring_without_a_title_keeps_the_old_one(self) -> None:
        """Omitting the label must not silently blank it."""
        self.ws.declare_artifact(3001, title="Landing")
        again = self.ws.declare_artifact(3001)

        self.assertEqual(again.title, "Landing")

    async def test_privileged_and_out_of_range_ports_are_refused(
        self,
    ) -> None:
        """Ports outside 1024-65535 are not usable by a sandboxed service."""
        for port in (80, 1023, 65536):
            with self.assertRaises(ValueError):
                self.ws.declare_artifact(port)

    async def test_reserved_ports_are_refused(self) -> None:
        """A workspace must not hand out a port it uses itself."""

        class _Reserving(_CountingWorkspace):
            @property
            def _reserved_ports(self) -> set[int]:
                return {49999}

        with tempfile.TemporaryDirectory() as workdir:
            ws = _Reserving(workdir=workdir)
            await ws.initialize()
            try:
                with self.assertRaises(ValueError):
                    ws.declare_artifact(49999)
                self.assertEqual(ws.declare_artifact(49998).port, 49998)
            finally:
                await ws.close()


class TestArtifactForwards(IsolatedAsyncioTestCase):
    """Opening, sharing and releasing the forward behind a declaration."""

    async def asyncSetUp(self) -> None:
        """Bring up a workspace with one port already declared."""
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _CountingWorkspace(workdir=self._tmp.name)
        await self.ws.initialize()
        self.ws.declare_artifact(3001)

    async def asyncTearDown(self) -> None:
        """Close the workspace and drop the directory."""
        await self.ws.close()
        self._tmp.cleanup()

    async def test_ensure_opens_once_and_is_shared(self) -> None:
        """Concurrent viewers of one port must share a single forward."""
        first = await self.ws.ensure_upstream(3001)
        second = await self.ws.ensure_upstream(3001)

        self.assertIs(first, second)
        self.assertEqual(self.ws.opened, 1)
        self.assertTrue(self.ws.has_open_upstream())

    async def test_local_upstream_is_reachable_by_the_browser(self) -> None:
        """A same-host viewer loads the address itself; no proxy hop."""
        upstream = await self.ws.ensure_upstream(3001)

        self.assertEqual(upstream.kind, "loopback")
        self.assertEqual(upstream.url, "http://127.0.0.1:3001")
        self.assertTrue(upstream.browser_reachable)

    async def test_ensure_rejects_an_undeclared_port(self) -> None:
        """Only what the agent declared may be forwarded."""
        with self.assertRaises(ValueError):
            await self.ws.ensure_upstream(9999)

    async def test_release_keeps_the_declaration(self) -> None:
        """Closing the viewer must not withdraw the artifact."""
        await self.ws.ensure_upstream(3001)
        await self.ws.release_upstream(3001)

        self.assertEqual(self.ws.closed, 1)
        self.assertFalse(self.ws.has_open_upstream())
        self.assertEqual(len(self.ws.list_artifacts()), 1)

        await self.ws.ensure_upstream(3001)
        self.assertEqual(self.ws.opened, 2)

    async def test_release_of_an_unopened_port_is_a_no_op(self) -> None:
        """Releasing twice, or before opening, must not raise."""
        await self.ws.release_upstream(3001)
        await self.ws.release_upstream(9999)

        self.assertEqual(self.ws.closed, 0)

    async def test_a_failing_teardown_does_not_propagate(self) -> None:
        """The caller is shutting down and cannot act on the error."""

        class _Failing(_CountingWorkspace):
            async def _close_upstream(
                self,
                port: int,
                upstream: Upstream,
            ) -> None:
                raise RuntimeError("teardown exploded")

        with tempfile.TemporaryDirectory() as workdir:
            ws = _Failing(workdir=workdir)
            await ws.initialize()
            ws.declare_artifact(3001)
            await ws.ensure_upstream(3001)

            await ws.release_upstream(3001)

            self.assertFalse(ws.has_open_upstream())
            await ws.close()

    async def test_undeclare_releases_the_forward(self) -> None:
        """Withdrawing must not leave a forward behind."""
        await self.ws.ensure_upstream(3001)

        self.assertTrue(await self.ws.undeclare_artifact(3001))

        self.assertEqual(self.ws.closed, 1)
        self.assertFalse(self.ws.has_open_upstream())
        self.assertEqual(self.ws.list_artifacts(), [])
        self.assertFalse(await self.ws.undeclare_artifact(3001))

    async def test_close_clears_everything(self) -> None:
        """A workspace must never outlive its forwards."""
        await self.ws.ensure_upstream(3001)

        await self.ws.close()

        self.assertEqual(self.ws.closed, 1)
        self.assertFalse(self.ws.has_open_upstream())
        self.assertEqual(self.ws.list_artifacts(), [])


class TestUnsupportedBackend(IsolatedAsyncioTestCase):
    """A backend with no port support must fail loudly, not silently."""

    async def test_ensure_raises_not_implemented(self) -> None:
        """The message has to name the class so the cause is obvious."""

        class _NoForwarding(WorkspaceBase):
            async def initialize(self) -> None:
                """Nothing to provision."""

            async def close(self) -> None:
                """Nothing to release."""

            async def get_instructions(self) -> str:
                """No prompt fragment."""
                return ""

            async def add_mcp(self, mcp_client: object) -> None:
                """Not exercised here."""

            async def remove_mcp(self, name: str) -> None:
                """Not exercised here."""

        ws = _NoForwarding()
        ws.declare_artifact(3001)

        with self.assertRaises(NotImplementedError) as ctx:
            await ws.ensure_upstream(3001)
        self.assertIn("_NoForwarding", str(ctx.exception))


class TestArtifactTools(IsolatedAsyncioTestCase):
    """The agent-facing surface over the same declarations."""

    async def asyncSetUp(self) -> None:
        """Bring up a workspace and bind the two tools to it."""
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _CountingWorkspace(workdir=self._tmp.name)
        await self.ws.initialize()
        self.add = ArtifactAdd(workspace=self.ws)
        self.remove = ArtifactRemove(workspace=self.ws)

    async def asyncTearDown(self) -> None:
        """Close the workspace and drop the directory."""
        await self.ws.close()
        self._tmp.cleanup()

    async def test_add_declares_without_connecting(self) -> None:
        """The tool must be usable before the service is listening."""
        chunk = await self.add.call(port=5173, title="Vite")

        self.assertEqual(chunk.state, ToolResultState.RUNNING)
        self.assertEqual(self.ws.opened, 0)
        self.assertEqual([a.title for a in self.ws.list_artifacts()], ["Vite"])

    async def test_add_reports_a_rejected_port_to_the_agent(self) -> None:
        """A bad port comes back as a tool error, not an exception."""
        chunk = await self.add.call(port=80)

        self.assertEqual(chunk.state, ToolResultState.ERROR)
        self.assertEqual(self.ws.list_artifacts(), [])

    async def test_remove_withdraws(self) -> None:
        """Removing a declared port succeeds and is not repeatable."""
        await self.add.call(port=5173)

        chunk = await self.remove.call(port=5173)
        self.assertEqual(chunk.state, ToolResultState.RUNNING)
        self.assertEqual(self.ws.list_artifacts(), [])

        chunk = await self.remove.call(port=5173)
        self.assertEqual(chunk.state, ToolResultState.ERROR)
