# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test failed initialization with real cloud workspace lifecycles."""

import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import (
    DaytonaWorkspaceManager,
    OpenSandboxWorkspaceManager,
)
from agentscope.workspace import DaytonaWorkspace, OpenSandboxWorkspace


class TestCloudWorkspaceCleanup(IsolatedAsyncioTestCase):
    """Only sandboxes created by the failed attempt may be deleted."""

    async def test_gateway_failure_preserves_existing_sandboxes(self) -> None:
        """Keep existing data while disposing of newly created sandboxes."""
        await self._check_failed_initialization(TimeoutError("gateway"))

    async def test_cancelled_initialization_preserves_existing(self) -> None:
        """Cancellation must obey the same sandbox ownership rules."""
        await self._check_failed_initialization(asyncio.CancelledError())

    async def _check_failed_initialization(
        self,
        error: BaseException,
    ) -> None:
        """Exercise create and reattach paths with provider I/O mocked."""
        for workspace_type, manager_type in (
            (DaytonaWorkspace, DaytonaWorkspaceManager),
            (OpenSandboxWorkspace, OpenSandboxWorkspaceManager),
        ):
            for existing in (False, True):
                with self.subTest(
                    provider=workspace_type.__name__,
                    existing=existing,
                ):
                    sandbox = SimpleNamespace(
                        id="sandbox-with-user-data",
                        state="started",
                        refresh_data=AsyncMock(),
                        get_work_dir=AsyncMock(return_value="/home/user"),
                        get_user_home_dir=AsyncMock(
                            return_value="/home/user",
                        ),
                        is_running=AsyncMock(return_value=True),
                        kill=AsyncMock(),
                        pause=AsyncMock(),
                        stop=AsyncMock(),
                        close=AsyncMock(),
                    )
                    client = SimpleNamespace(
                        create=AsyncMock(return_value=sandbox),
                        delete=AsyncMock(),
                        close=AsyncMock(),
                    )

                    async def get_client(
                        workspace: DaytonaWorkspace,
                        sandbox_client: SimpleNamespace = client,
                    ) -> SimpleNamespace:
                        workspace._daytona = sandbox_client
                        return sandbox_client

                    with ExitStack() as stack:
                        for name, mock in (
                            (
                                "_find_existing_sandbox",
                                AsyncMock(
                                    return_value=sandbox if existing else None,
                                ),
                            ),
                            ("_restore_mcp_specs", AsyncMock(return_value={})),
                            ("_ensure_workspace_layout", AsyncMock()),
                            (
                                "_setup_mcp_gateway",
                                AsyncMock(side_effect=error),
                            ),
                        ):
                            stack.enter_context(
                                patch.object(workspace_type, name, mock),
                            )
                        if workspace_type is DaytonaWorkspace:
                            stack.enter_context(
                                patch.object(
                                    workspace_type,
                                    "_get_daytona_client",
                                    get_client,
                                ),
                            )
                            stack.enter_context(
                                patch.object(
                                    workspace_type,
                                    "_create_params",
                                    return_value=object(),
                                ),
                            )
                        else:
                            for name in (
                                "_attach_existing_sandbox",
                                "_create_sandbox",
                            ):
                                stack.enter_context(
                                    patch.object(
                                        workspace_type,
                                        name,
                                        AsyncMock(return_value=sandbox),
                                    ),
                                )

                        manager = manager_type()
                        with self.assertRaises(type(error)) as raised:
                            await manager.get_workspace("u", "a", "s", "ws")

                    is_daytona = workspace_type is DaytonaWorkspace
                    self.assertIs(raised.exception, error)
                    self.assertEqual(
                        {
                            "delete": client.delete.await_count,
                            "kill": sandbox.kill.await_count,
                            "stop": sandbox.stop.await_count,
                            "pause": sandbox.pause.await_count,
                            "client_close": client.close.await_count,
                            "sandbox_close": sandbox.close.await_count,
                            "cache": manager._cache,
                        },
                        {
                            "delete": int(is_daytona and not existing),
                            "kill": int(not is_daytona and not existing),
                            "stop": int(is_daytona and existing),
                            "pause": int(not is_daytona and existing),
                            "client_close": int(is_daytona),
                            "sandbox_close": int(not is_daytona),
                            "cache": {},
                        },
                    )
