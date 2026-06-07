# -*- coding: utf-8 -*-
"""Tests for chat service helpers."""

from types import SimpleNamespace
from unittest import TestCase

from agentscope.permission import PermissionContext, AdditionalWorkingDirectory
from agentscope.app._service._chat import _include_workspace_working_directory


class ChatServiceWorkspacePermissionTest(TestCase):
    """Workspace roots should become permission working directories."""

    def test_workspace_working_directory_is_added(self) -> None:
        """Add the workspace root when the session has no matching entry."""
        context = PermissionContext()

        _include_workspace_working_directory(
            context,
            SimpleNamespace(
                working_directory="/workspace",
            ),  # type: ignore[arg-type]
        )

        self.assertEqual(
            context.working_directories["/workspace"],
            AdditionalWorkingDirectory(
                path="/workspace",
                source="workspace",
            ),
        )

    def test_existing_working_directory_is_not_replaced(self) -> None:
        """Keep caller-provided permission sources intact."""
        context = PermissionContext(
            working_directories={
                "/workspace": AdditionalWorkingDirectory(
                    path="/workspace",
                    source="userSettings",
                ),
            },
        )

        _include_workspace_working_directory(
            context,
            SimpleNamespace(
                working_directory="/workspace",
            ),  # type: ignore[arg-type]
        )

        self.assertEqual(
            context.working_directories["/workspace"].source,
            "userSettings",
        )
