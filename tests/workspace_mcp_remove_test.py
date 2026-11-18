# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for failure-safe removal of gateway-backed MCP registrations."""

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.tool import LocalBackend
from agentscope.workspace import DockerWorkspace
from agentscope.workspace._gateway_client import GatewayClient
from agentscope.workspace._mcp_gateway._mcp_gateway_app import (
    _State,
    _build_app,
)


class TestSandboxedMCPRemoval(IsolatedAsyncioTestCase):
    """Exercise real workspace persistence with a simulated gateway."""

    def setUp(self) -> None:
        """Bind the sandbox implementation to temporary local storage."""
        # unittest owns the directory context until test cleanup.
        # pylint: disable-next=consider-using-with
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.workspace = DockerWorkspace(host_workdir=directory)
        self.workspace.workdir = directory
        self.workspace._backend = LocalBackend()
        self.gateway = GatewayClient(self.workspace._backend, 5600)
        self.request = AsyncMock(side_effect=self._request)
        self.gateway.exec_request = self.request
        self.workspace._gateway = self.gateway
        self.mcp_file = Path(directory, ".mcp")
        self.scope = {"agent_id": "agent", "session_id": "session"}
        self.registered: set[tuple[str, str, str]] = set()
        self.delete_failure: tuple[int, bytes] | Exception | None = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> tuple[int, bytes]:
        """Simulate scoped registrations and injected DELETE failures."""
        agent_id = params["agent_id"]
        session_id = params["session_id"]
        if method == "POST":
            assert body is not None
            key = (agent_id, session_id, body["name"])
            if key in self.registered:
                return 409, b'{"detail":"already exists"}'
            self.registered.add(key)
            return 200, b'{"ok":true}'
        self.assertEqual(method, "DELETE")
        if isinstance(self.delete_failure, Exception):
            raise self.delete_failure
        if self.delete_failure is not None:
            return self.delete_failure
        name = path.removeprefix("/mcps/")
        key = (agent_id, session_id, name)
        if key not in self.registered:
            detail = (
                f"{name!r} not found for agent={agent_id!r} "
                f"session={session_id!r}"
            )
            return 404, json.dumps({"detail": detail}).encode("utf-8")
        self.registered.remove(key)
        return 200, b'{"ok":true}'

    @staticmethod
    def _spec(stateful: bool) -> MCPClient:
        """Return a configuration usable by either gateway client mode."""
        return MCPClient(
            name="demo",
            is_stateful=stateful,
            mcp_config=HttpMCPConfig(url="http://example.test/mcp"),
        )

    def _snapshot(self) -> dict[str, Any]:
        """Capture all local MCP state, including persisted bytes."""
        return {
            "instances": {
                key: dict(clients)
                for key, clients in self.workspace._mcp_instances.items()
            },
            "specs": {
                key: list(specs)
                for key, specs in self.workspace._mcp_specs.items()
            },
            "connected": {
                (*key, name): client.is_connected
                for key, clients in self.workspace._mcp_instances.items()
                for name, client in clients.items()
            },
            "last_used": dict(self.workspace._mcp_last_used),
            "persisted": self.mcp_file.read_bytes(),
            "registered": set(self.registered),
        }

    def _assert_removed(
        self,
        client: MCPClient,
        before: dict[str, Any],
    ) -> None:
        """Assert removal of this scope while preserving all other state."""
        key = (self.scope["agent_id"], self.scope["session_id"])
        expected = {
            **before,
            "instances": {**before["instances"], key: {}},
            "specs": {**before["specs"], key: []},
            "connected": dict(before["connected"]),
            "registered": before["registered"] - {(*key, client.name)},
            "persisted": json.loads(before["persisted"]),
        }
        expected["connected"].pop((*key, client.name))
        expected["persisted"]["mcps"][key[0]][key[1]] = []
        actual = self._snapshot()
        actual["persisted"] = json.loads(actual["persisted"])
        self.assertFalse(client.is_connected)
        self.assertEqual(actual, expected)

    async def test_failed_remove_preserves_state_and_can_retry(self) -> None:
        """HTTP and transport failures preserve both modes and scopes."""
        other_scopes = [
            {"agent_id": "other-agent", "session_id": "session"},
            {"agent_id": "agent", "session_id": "other-session"},
        ]
        for scope in other_scopes:
            await self.workspace.add_mcp(self._spec(True), **scope)
        failures = [
            (401, b'{"detail":"Unauthorized"}'),
            (403, b'{"detail":"Forbidden"}'),
            (404, b'{"detail":"Not Found"}'),
            (404, b'{"detail":"Not Found","request_id":"test"}'),
            (404, b"not json"),
            (404, b"[]"),
            (500, b'{"detail":"delete failed"}'),
            OSError("request failed"),
        ]
        for stateful in (True, False):
            for failure in failures:
                with self.subTest(stateful=stateful, failure=failure):
                    spec = self._spec(stateful)
                    client = await self.workspace.add_mcp(spec, **self.scope)
                    before = self._snapshot()
                    self.delete_failure = failure
                    expected_error = (
                        OSError
                        if isinstance(failure, OSError)
                        else RuntimeError
                    )

                    with self.assertRaises(expected_error):
                        await self.workspace.remove_mcp("demo", **self.scope)

                    self.assertEqual(self._snapshot(), before)
                    self.assertIs(
                        self.workspace._mcp_instances[("agent", "session")][
                            "demo"
                        ],
                        client,
                    )
                    self.delete_failure = None
                    await self.workspace.remove_mcp("demo", **self.scope)
                    self._assert_removed(client, before)
                    replacement = await self.workspace.add_mcp(
                        spec,
                        **self.scope,
                    )
                    self.assertIsNot(replacement, client)
                    self.assertEqual(self.registered, before["registered"])
                    await self.workspace.remove_mcp("demo", **self.scope)

    async def test_lost_delete_response_can_retry_after_remote_removal(
        self,
    ) -> None:
        """Scoped 404s permit retry, with or without response metadata."""
        with TestClient(_build_app(_State())) as http:
            missing = http.delete("/mcps/demo", params=self.scope)
        self.assertEqual(missing.status_code, 404)
        extras: list[dict[str, str]] = [{}, {"request_id": "test-request"}]
        for stateful in (True, False):
            for extra in extras:
                with self.subTest(stateful=stateful, extra=extra):
                    client = await self.workspace.add_mcp(
                        self._spec(stateful),
                        **self.scope,
                    )
                    self.registered.clear()
                    before = self._snapshot()
                    self.delete_failure = OSError("response lost")
                    with self.assertRaises(OSError):
                        await self.workspace.remove_mcp("demo", **self.scope)
                    self.assertEqual(self._snapshot(), before)

                    payload = {**missing.json(), **extra}
                    self.delete_failure = (
                        missing.status_code,
                        json.dumps(payload).encode("utf-8"),
                    )
                    await self.workspace.remove_mcp("demo", **self.scope)
                    self._assert_removed(client, before)

    async def test_manually_closed_client_can_be_removed(self) -> None:
        """The existing strict-close workaround remains compatible."""
        client = await self.workspace.add_mcp(self._spec(False), **self.scope)
        await client.close(ignore_errors=False)
        before = self._snapshot()
        self.request.reset_mock()

        await self.workspace.remove_mcp("demo", **self.scope)

        self.request.assert_not_awaited()
        self._assert_removed(client, before)

    async def test_bulk_close_remains_best_effort(self) -> None:
        """Bulk cleanup keeps declarations and tolerates DELETE failures."""
        for stateful in (True, False):
            for failure in ((500, b"failed"), OSError("request failed")):
                with self.subTest(stateful=stateful, failure=failure):
                    scope = {
                        **self.scope,
                        "session_id": str(len(self.registered)),
                    }
                    client = await self.workspace.add_mcp(
                        self._spec(stateful),
                        **scope,
                    )
                    before = self._snapshot()
                    self.delete_failure = failure

                    await self.workspace._close_all_mcp_instances()

                    self.assertFalse(client.is_connected)
                    self.assertEqual(self.workspace._mcp_instances, {})
                    self.assertEqual(
                        self.workspace._mcp_specs,
                        before["specs"],
                    )
                    self.assertEqual(
                        self.mcp_file.read_bytes(),
                        before["persisted"],
                    )

    async def test_unknown_name_does_not_change_state(self) -> None:
        """Removing an unknown name does not contact the gateway."""
        await self.workspace.add_mcp(self._spec(False), **self.scope)
        before = self._snapshot()
        self.request.reset_mock()

        await self.workspace.remove_mcp("missing", **self.scope)

        self.request.assert_not_awaited()
        self.assertEqual(self._snapshot(), before)
