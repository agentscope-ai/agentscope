# -*- coding: utf-8 -*-
"""Test cases for :class:`LocalWorkspaceManager`."""

import os
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.workspace_manager import (
    IsolationPolicy,
    LocalWorkspaceManager,
)


class _FakeWorkspace:
    """Workspace double used by manager tests."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")

    async def initialize(self) -> None:
        """No-op — the manager only needs the object back."""

    async def close(self) -> None:
        """No-op."""


class TestLocalWorkspaceManager(IsolatedAsyncioTestCase):
    """The unbound-id fallback honours the isolation policy."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_local_workspace_manager.LocalWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_an_empty_workspace_id_stays_per_user(self) -> None:
        """Sessions persisted with ``workspace_id=""`` derive a binding
        from their own user, not from a blank one — under ``PER_USER``
        a blank owner would pool every user onto a single id."""
        manager = LocalWorkspaceManager(
            "/tmp/local-manager-test",
            isolation=IsolationPolicy.PER_USER,
        )

        alice = await manager.get_workspace("alice", "a1", "s", "")
        bob = await manager.get_workspace("bob", "a2", "s", "")

        self.assertIsNot(alice, bob)
        self.assertListEqual(
            [alice.kwargs["workspace_id"], bob.kwargs["workspace_id"]],
            ["982aa9b33217069a", "883053c3e4594c5b"],
        )

    async def test_agent_id_cannot_escape_the_basedir(self) -> None:
        """A caller-supplied ``agent_id`` may not point the workdir
        outside the base directory — the same containment the Docker
        manager applies to ``workspace_id`` (an absolute id must not
        replace the base entirely either)."""
        manager = LocalWorkspaceManager("/tmp/local-manager-test-escape")

        for agent_id in ("../../etc", "/etc", "a/../../../etc"):
            with self.assertRaises(ValueError):
                await manager.get_workspace("u1", agent_id, "s1", "fixed-id")

    async def test_nested_but_contained_agent_id_still_works(self) -> None:
        """An ``agent_id`` with separators that stays inside the base
        directory keeps its historical nested-workdir behaviour."""
        manager = LocalWorkspaceManager("/tmp/local-manager-test-nested")

        ws = await manager.get_workspace("u1", "team-a/agent-b", "s1")

        expected = os.path.join(
            os.path.realpath("/tmp/local-manager-test-nested"),
            "team-a",
            "agent-b",
        )
        self.assertEqual(
            os.path.realpath(ws.kwargs["workdir"]),
            os.path.realpath(expected),
        )
