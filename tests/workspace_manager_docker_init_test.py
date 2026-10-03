# -*- coding: utf-8 -*-
"""Test failed Docker workspace initialization cleanup."""

import asyncio
import shutil
import sys
import tempfile
import types
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import DockerWorkspaceManager
from agentscope.workspace import DockerWorkspace


class TestDockerWorkspaceInitializationCleanup(IsolatedAsyncioTestCase):
    """Exercise manager-owned Docker workspace initialization failures."""

    def setUp(self) -> None:
        """Install an SDK double and isolate Docker-only operations.

        Returns:
            `None`:
                This method configures the test and returns no value.
        """
        self._basedir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._basedir)
        self.manager = DockerWorkspaceManager(self._basedir)

        self.container = types.SimpleNamespace(
            start=AsyncMock(),
            kill=AsyncMock(),
            delete=AsyncMock(),
        )
        self.client = types.SimpleNamespace(
            containers=types.SimpleNamespace(
                create_or_replace=AsyncMock(return_value=self.container),
            ),
            close=AsyncMock(),
        )
        fake_sdk = types.ModuleType("aiodocker")
        fake_sdk.Docker = lambda: self.client
        self.enterContext(patch.dict(sys.modules, {"aiodocker": fake_sdk}))

        self.gateway_setup = AsyncMock()
        for method, replacement in (
            ("_build_or_reuse_image", AsyncMock()),
            ("_restore_mcp_specs", AsyncMock(return_value={})),
            ("_ensure_workspace_layout", AsyncMock()),
            ("_setup_mcp_gateway", self.gateway_setup),
            ("_migrate_skill_layout", AsyncMock()),
            ("_setup_skills", AsyncMock()),
        ):
            self.enterContext(
                patch.object(DockerWorkspace, method, new=replacement),
            )
        self.enterContext(
            patch(
                "agentscope.workspace._docker._docker_backend."
                "DockerBackend.exec_shell",
                new=AsyncMock(),
            ),
        )

    async def asyncTearDown(self) -> None:
        """Close successful workspaces left in the manager cache.

        Returns:
            `None`:
                This method closes the workspaces and returns no value.
        """
        await self.manager.close_all()

    def _counts(self) -> dict[str, int]:
        """Return the complete mocked Docker lifecycle call counts.

        Returns:
            `dict[str, int]`:
                Calls that create, start, stop, delete, and close SDK objects.
        """
        return {
            "created": self.client.containers.create_or_replace.await_count,
            "started": self.container.start.await_count,
            "killed": self.container.kill.await_count,
            "deleted": self.container.delete.await_count,
            "client_closed": self.client.close.await_count,
        }

    async def test_failure_cleans_up_and_retry_is_cached(self) -> None:
        """Clean a failed initialization, then cache the successful retry.

        Returns:
            `None`:
                This test method returns no value.
        """
        initialization_error = RuntimeError("gateway initialization failed")
        self.gateway_setup.side_effect = [initialization_error, None]

        try:
            await self.manager.get_workspace(
                "user",
                "agent",
                "session",
                "workspace-id",
            )
        except RuntimeError as error:
            first_error_preserved = error is initialization_error
        else:
            first_error_preserved = False

        after_failure = {
            **self._counts(),
            "error_preserved": first_error_preserved,
        }
        workspace = await self.manager.get_workspace(
            "user",
            "agent",
            "session",
            "workspace-id",
        )
        cached_workspace = await self.manager.get_workspace(
            "user",
            "agent",
            "another-session",
            "workspace-id",
        )
        after_retry_and_cache_hit = {
            **self._counts(),
            "workspace_alive": workspace.is_alive,
            "cache_hit_reused_workspace": cached_workspace is workspace,
        }

        self.assertDictEqual(
            {
                "after_failure": after_failure,
                "after_retry_and_cache_hit": after_retry_and_cache_hit,
            },
            {
                "after_failure": {
                    "created": 1,
                    "started": 1,
                    "killed": 1,
                    "deleted": 1,
                    "client_closed": 1,
                    "error_preserved": True,
                },
                "after_retry_and_cache_hit": {
                    "created": 2,
                    "started": 2,
                    "killed": 1,
                    "deleted": 1,
                    "client_closed": 1,
                    "workspace_alive": True,
                    "cache_hit_reused_workspace": True,
                },
            },
        )

    async def test_cancelled_initialization_cleans_up(self) -> None:
        """Cancellation after container start closes the workspace.

        Returns:
            `None`:
                This test method returns no value.
        """
        gateway_started = asyncio.Event()
        hold_gateway = asyncio.Event()

        async def wait_for_gateway() -> None:
            """Pause gateway setup until the test cancels initialization.

            Returns:
                `None`:
                    This callback remains suspended until it is cancelled.
            """
            gateway_started.set()
            await hold_gateway.wait()

        self.gateway_setup.side_effect = wait_for_gateway
        task = asyncio.create_task(
            self.manager.get_workspace(
                "user",
                "agent",
                "session",
                "workspace-id",
            ),
        )
        await gateway_started.wait()
        cancellation_requested = task.cancel("initialization cancelled")
        try:
            await task
        except asyncio.CancelledError as error:
            cancellation_preserved = str(error) == "initialization cancelled"
        else:
            cancellation_preserved = False

        self.assertDictEqual(
            {
                **self._counts(),
                "cancellation_requested": cancellation_requested,
                "cancellation_preserved": cancellation_preserved,
            },
            {
                "created": 1,
                "started": 1,
                "killed": 1,
                "deleted": 1,
                "client_closed": 1,
                "cancellation_requested": True,
                "cancellation_preserved": True,
            },
        )

    async def test_cleanup_error_does_not_replace_initialization_error(
        self,
    ) -> None:
        """Preserve the first error when cleanup also raises.

        Returns:
            `None`:
                This test method returns no value.
        """
        initialization_error = RuntimeError("gateway initialization failed")
        cleanup_error = RuntimeError("workspace cleanup failed")
        self.gateway_setup.side_effect = [initialization_error, None]

        with patch.object(
            DockerWorkspace,
            "close",
            new=AsyncMock(side_effect=cleanup_error),
        ) as close_workspace:
            try:
                await self.manager.get_workspace(
                    "user",
                    "agent",
                    "session",
                    "workspace-id",
                )
            except RuntimeError as error:
                original_error_preserved = error is initialization_error
            else:
                original_error_preserved = False

        after_failure = {
            **self._counts(),
            "cleanup_calls": close_workspace.await_count,
            "original_error_preserved": original_error_preserved,
        }
        workspace = await self.manager.get_workspace(
            "user",
            "agent",
            "session",
            "workspace-id",
        )
        after_retry = {
            **self._counts(),
            "workspace_alive": workspace.is_alive,
        }

        self.assertDictEqual(
            {"after_failure": after_failure, "after_retry": after_retry},
            {
                "after_failure": {
                    "created": 1,
                    "started": 1,
                    "killed": 0,
                    "deleted": 0,
                    "client_closed": 0,
                    "cleanup_calls": 1,
                    "original_error_preserved": True,
                },
                "after_retry": {
                    "created": 2,
                    "started": 2,
                    "killed": 0,
                    "deleted": 0,
                    "client_closed": 0,
                    "workspace_alive": True,
                },
            },
        )
