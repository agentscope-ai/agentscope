# -*- coding: utf-8 -*-
"""Stdio MCP command allowlist test cases for ``POST /workspace/mcp``."""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

HEADERS = {"X-User-ID": "alice"}
PARAMS = {"agent_id": "agent-1", "session_id": "session-1"}


def _fake_storage() -> Any:
    """Build a fakeredis-backed storage and message bus."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    class _Storage(RedisStorage):
        """Storage sharing the in-memory client."""

        async def __aenter__(self) -> Any:
            self._client = redis
            return self

        async def aclose(self) -> None:
            self._client = None

    class _Bus(RedisMessageBus):
        """Message bus sharing the in-memory client."""

        async def __aenter__(self) -> Any:
            self._client = redis
            return self

        async def aclose(self) -> None:
            self._client = None

    return _Storage(), _Bus()


def _stdio_body(command: str) -> dict:
    """A request body registering a stdio MCP running *command*."""
    return {
        "name": "probe",
        "is_stateful": True,
        "mcp_config": {
            "type": "stdio_mcp",
            "command": command,
            "args": ["--version"],
        },
    }


class WorkspaceStdioAllowlistTest(IsolatedAsyncioTestCase):
    """``allowed_stdio_commands`` must gate which processes the service
    may be asked to spawn."""

    def _client(self, **kwargs: Any) -> TestClient:
        """Build an app with *kwargs* passed to ``create_app``."""
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        storage, bus = _fake_storage()
        app = create_app(
            storage=storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
            **kwargs,
        )
        # The workspace behind an allowed request does not exist in this
        # fixture, so only the status code of the gate is asserted.
        return self.enterContext(
            TestClient(app, raise_server_exceptions=False),
        )

    async def test_command_outside_allowlist_is_forbidden(self) -> None:
        """A command that is not listed answers 403 before it can run."""
        client = self._client(allowed_stdio_commands=["npx", "uvx"])
        response = client.post(
            "/workspace/mcp",
            params=PARAMS,
            json=_stdio_body("cmd"),
            headers=HEADERS,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("'cmd'", response.json()["detail"])

    async def test_empty_allowlist_forbids_every_stdio_command(self) -> None:
        """``[]`` is "no stdio MCPs", not "no restriction"."""
        client = self._client(allowed_stdio_commands=[])
        response = client.post(
            "/workspace/mcp",
            params=PARAMS,
            json=_stdio_body("npx"),
            headers=HEADERS,
        )
        self.assertEqual(response.status_code, 403)

    async def test_matching_is_exact_not_basename(self) -> None:
        """A path to a listed binary name is a different command."""
        client = self._client(allowed_stdio_commands=["npx"])
        for command in ("/tmp/evil/npx", "npx.exe", "NPX", "npx "):
            response = client.post(
                "/workspace/mcp",
                params=PARAMS,
                json=_stdio_body(command),
                headers=HEADERS,
            )
            self.assertEqual(response.status_code, 403, command)

    async def test_listed_command_passes_the_gate(self) -> None:
        """A listed command is not refused by the allowlist."""
        client = self._client(allowed_stdio_commands=["npx"])
        response = client.post(
            "/workspace/mcp",
            params=PARAMS,
            json=_stdio_body("npx"),
            headers=HEADERS,
        )
        self.assertNotEqual(response.status_code, 403)

    async def test_http_mcp_is_not_subject_to_the_allowlist(self) -> None:
        """The allowlist only concerns processes the server would spawn."""
        client = self._client(allowed_stdio_commands=[])
        response = client.post(
            "/workspace/mcp",
            params=PARAMS,
            json={
                "name": "remote",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "https://example.com/mcp",
                },
            },
            headers=HEADERS,
        )
        self.assertNotEqual(response.status_code, 403)

    async def test_default_stays_unrestricted(self) -> None:
        """Without the option the behaviour is unchanged (opt-in)."""
        client = self._client()
        response = client.post(
            "/workspace/mcp",
            params=PARAMS,
            json=_stdio_body("cmd"),
            headers=HEADERS,
        )
        self.assertNotEqual(response.status_code, 403)
