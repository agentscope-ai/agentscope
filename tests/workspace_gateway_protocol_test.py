# -*- coding: utf-8 -*-
"""Readiness checks for gateways in fresh and existing sandboxes."""
# pylint: disable=protected-access
import posixpath
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, patch

from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.tool import ExecResult, LocalBackend, MCPTool
from agentscope.workspace import BubblewrapWorkspace
from agentscope.workspace._e2b._e2b_workspace import E2BWorkspace
from agentscope.workspace._gateway_client import GatewayClient
from agentscope.workspace._mcp_gateway._mcp_gateway_app import (
    _build_app,
    _State,
)
from agentscope.workspace._utils import (
    _read_gateway_script_bytes,
    _read_glob_helper_bytes,
)


class GatewayProtocolTest(IsolatedAsyncioTestCase):
    """A resumed workspace requires the same protocol as a new workspace."""

    async def test_bubblewrap_refreshes_runtime_scripts_on_every_start(
        self,
    ) -> None:
        """A reusable bootstrap still launches the packaged gateway."""
        for ready in (False, True):
            with self.subTest(ready=ready):
                ws = BubblewrapWorkspace(gateway_port=5600)
                backend = AsyncMock()
                backend.join_path = posixpath.join
                backend.exec_shell.return_value = ExecResult(0, b"", b"")
                with (
                    patch.object(ws, "get_backend", return_value=backend),
                    patch.object(
                        ws,
                        "_bootstrap_is_ready",
                        return_value=ready,
                    ),
                    patch.object(
                        ws,
                        "_bootstrap_commands",
                        return_value=[],
                    ) as bootstrap,
                    patch.object(
                        ws,
                        "_wait_gateway_health",
                        return_value=True,
                    ),
                ):
                    await ws._setup_mcp_gateway()
                    self.assertEqual(bootstrap.call_count, int(not ready))
                    self.assertEqual(
                        backend.write_file.await_args_list,
                        [
                            call(
                                ws._glob_helper_path,
                                _read_glob_helper_bytes(),
                            ),
                            call(
                                ws._gateway_script,
                                _read_gateway_script_bytes(),
                            ),
                        ],
                    )

    async def test_bootstrap_and_resume(self) -> None:
        """Only first use installs files. Incompatible resumes fail early."""
        current = b'{"status":"ok","protocol_version":1}'
        for installed, body in (
            (False, current),
            (True, current),
            (True, b"ok"),
        ):
            with self.subTest(installed=installed, body=body):
                backend = AsyncMock(spec=LocalBackend)
                backend.join_path = posixpath.join
                backend.file_exists.return_value = installed
                backend.exec_shell.return_value = ExecResult(0, b"", b"")
                ws = E2BWorkspace()
                ws._backend = backend
                with (
                    patch.object(
                        ws,
                        "_bootstrap_commands",
                        return_value=[],
                    ) as bootstrap,
                    patch.object(
                        GatewayClient,
                        "exec_request",
                        new=AsyncMock(return_value=(200, body)),
                    ) as probe,
                ):
                    if body == current:
                        await ws._setup_mcp_gateway()
                        self.assertIsNotNone(ws._gateway)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Rebuild"):
                            await ws._setup_mcp_gateway()
                        self.assertIsNone(ws._gateway)
                    probe.assert_awaited_once()
                self.assertEqual(bootstrap.call_count, int(not installed))
                if installed:
                    backend.write_file.assert_not_awaited()
                else:
                    backend.write_file.assert_any_await(
                        ws._gateway_script,
                        _read_gateway_script_bytes(),
                    )

    def test_stale_runtime_cannot_advertise_readiness(self) -> None:
        """A new script requires a runtime that supports bound MCP calls."""
        with patch.object(MCPTool, "bind_for_dispatch", None):
            with self.assertRaisesRegex(RuntimeError, "Rebuild"):
                _build_app(_State())

    async def test_legacy_tool_list_is_rejected(self) -> None:
        """Discovery needs approval bindings for every advertised tool."""
        gateway = GatewayClient(AsyncMock(), 5600)
        proxy = gateway.make_client(
            MCPClient(
                name="echo",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="echo"),
            ).model_dump(),
        )
        with patch.object(
            gateway,
            "exec_request",
            new=AsyncMock(return_value=(200, b"[]")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Incompatible"):
                await proxy.list_raw_tools()
            self.assertIsNone(proxy._cached_tools)
