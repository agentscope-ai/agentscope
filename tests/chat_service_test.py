# -*- coding: utf-8 -*-
"""Tests for chat-service agent assembly helpers."""

from unittest import TestCase

from agentscope.app._service._chat import (
    _ensure_workspace_working_directory,
)
from agentscope.permission import AdditionalWorkingDirectory
from agentscope.state import AgentState


class _FakeWorkspace:
    """Minimal workspace stand-in for permission-context tests."""

    def __init__(self, working_directory: str) -> None:
        """Initialize the fake workspace."""
        self._working_directory = working_directory

    @property
    def working_directory(self) -> str:
        """Return the configured workspace root."""
        return self._working_directory


class TestEnsureWorkspaceWorkingDirectory(TestCase):
    """Workspace root permission injection tests."""

    def test_adds_workspace_root_to_permission_context(self) -> None:
        """The workspace root is included in working directories."""
        state = AgentState()

        _ensure_workspace_working_directory(
            state,
            _FakeWorkspace("/workspace"),  # type: ignore[arg-type]
        )

        self.assertEqual(
            state.permission_context.working_directories["/workspace"],
            AdditionalWorkingDirectory(
                path="/workspace",
                source="workspace",
            ),
        )

    def test_keeps_existing_workspace_root_entry(self) -> None:
        """Existing working-directory entries are not overwritten."""
        state = AgentState()
        state.permission_context.working_directories["/workspace"] = (
            AdditionalWorkingDirectory(
                path="/workspace",
                source="userSettings",
            )
        )

        _ensure_workspace_working_directory(
            state,
            _FakeWorkspace("/workspace"),  # type: ignore[arg-type]
        )

        self.assertEqual(
            state.permission_context.working_directories["/workspace"].source,
            "userSettings",
        )
