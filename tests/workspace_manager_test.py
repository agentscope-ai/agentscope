# -*- coding: utf-8 -*-
"""Backend-independent workspace manager identity tests."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access

import os
from unittest import TestCase

from agentscope.app.workspace_manager import LocalWorkspaceManager


class TestLocalWorkspaceManagerIdentity(TestCase):
    def test_workdir_is_user_and_workspace_scoped(self) -> None:
        manager = LocalWorkspaceManager("/srv/workspaces")
        self.assertEqual(
            manager._workdir_for("user-1", "workspace-1"),
            os.path.join(
                "/srv/workspaces",
                "user-1",
                "workspace-1",
            ),
        )

    def test_same_workspace_id_has_distinct_user_cache_keys(self) -> None:
        manager = LocalWorkspaceManager("/srv/workspaces")
        manager._cache[("user-1", "shared-id")] = (object(), 1.0)
        manager._cache[("user-2", "shared-id")] = (object(), 1.0)
        self.assertEqual(len(manager._cache), 2)
