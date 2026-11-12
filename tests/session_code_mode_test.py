# -*- coding: utf-8 -*-
"""Exercise Code Mode session APIs on the TestClient's own event loop."""

import tempfile
from functools import partial
from typing import Any
from unittest import TestCase
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    RedisStorage,
    SessionConfig,
)
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
from agentscope.tool import BackendBase, LocalBackend

HEADERS = {"X-User-ID": "alice"}


class SessionCodeModeTest(TestCase):
    """Keep Redis and all application operations on a single event loop."""

    def setUp(self) -> None:
        """Start an isolated app with one agent and one disabled session."""
        # unittest closes entered contexts after each test.
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        recorded_states = []

        class Storage(RedisStorage):
            """Use an isolated Redis double and record state writes."""

            async def __aenter__(self) -> Any:
                self._client = fakeredis.aioredis.FakeRedis(
                    decode_responses=True,
                )
                return self

            async def aclose(self) -> None:
                await self._client.aclose()
                self._client = None

            async def upsert_session(self, *args: Any, **kwargs: Any) -> Any:
                recorded_states.append(kwargs.get("state"))
                return await super().upsert_session(*args, **kwargs)

        self.recorded_states = recorded_states
        self.storage = Storage()
        self.bus = InMemoryMessageBus()
        app = create_app(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
        )
        self.client = self.enterContext(TestClient(app))
        self.agent_id = self.client.portal.call(
            self.storage.upsert_agent,
            "alice",
            AgentRecord(user_id="alice", data=AgentData(name="ann")),
        )
        response = self._create()
        self.assertEqual(response.status_code, 201, response.text)
        self.session_id = response.json()["session_id"]
        self.recorded_states.clear()

    def _create(self, **config: Any) -> Any:
        """Create a session for the test agent with optional configuration."""
        return self.client.post(
            "/sessions/",
            headers=HEADERS,
            json={"agent_id": self.agent_id, **config},
        )

    def _patch(self, body: dict) -> Any:
        """Update only the session under test."""
        return self.client.patch(
            f"/sessions/{self.session_id}",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
            json=body,
        )

    def _listed(self) -> dict:
        """Read the saved session through the frontend's list API."""
        response = self.client.get(
            "/sessions/",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
        )
        self.assertEqual(response.status_code, 200)
        return next(
            view
            for view in response.json()["sessions"]
            if view["session"]["id"] == self.session_id
        )

    def test_code_mode_creation_and_patch_round_trip(self) -> None:
        """Persist enabled mode, preserve omission and allow disabling."""
        self.assertFalse(SessionConfig(workspace_id="legacy").code_mode)
        self.assertFalse(self._listed()["session"]["config"]["code_mode"])
        created = self._create(code_mode=True)
        self.assertEqual(created.status_code, 201, created.text)
        record = self.client.portal.call(
            self.storage.get_session,
            "alice",
            self.agent_id,
            created.json()["session_id"],
        )
        self.assertTrue(record.config.code_mode)
        self.session_id = record.id
        self.recorded_states.clear()
        cases: list[tuple[dict, bool]] = [
            ({"code_mode": True}, True),
            ({"name": "renamed"}, True),
            ({"code_mode": False}, False),
        ]
        for body, enabled in cases:
            with self.subTest(body=body):
                response = self._patch(body)
                self.assertEqual(
                    (
                        response.status_code,
                        response.json()["config"]["code_mode"],
                        self._listed()["session"]["config"]["code_mode"],
                    ),
                    (200, enabled, enabled),
                )
        self.assertEqual(self.recorded_states, [None, None, None])

    def test_code_mode_rejects_invalid_values(self) -> None:
        """The toggle accepts booleans, not null or string coercions."""
        for value in (None, "true", 1):
            with self.subTest(value=value):
                response = self._patch({"code_mode": value})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.recorded_states, [])

    def test_explore_conflicts_in_both_update_directions(self) -> None:
        """Validate the merged configuration and permit an atomic switch."""
        cases: list[tuple[dict, int, bool]] = [
            ({"permission_mode": "explore"}, 200, False),
            ({"code_mode": True}, 422, False),
            ({"permission_mode": "default", "code_mode": True}, 200, True),
            ({"permission_mode": "explore"}, 422, True),
            ({"permission_mode": "explore", "code_mode": False}, 200, False),
        ]
        for body, status, enabled in cases:
            with self.subTest(body=body):
                response = self._patch(body)
                self.assertEqual(
                    (
                        response.status_code,
                        self._listed()["session"]["config"]["code_mode"],
                    ),
                    (status, enabled),
                )
                if status == 422:
                    self.assertEqual(
                        response.json(),
                        {
                            "detail": "Code Mode is unavailable in "
                            "Explore mode.",
                        },
                    )

    def test_team_session_cannot_enable_code_mode(self) -> None:
        """Team membership rejects the toggle before any config write."""
        self.client.portal.call(
            self.storage.set_session_team_id,
            "alice",
            self.session_id,
            "team-under-test",
        )
        response = self._patch({"code_mode": True})
        self.assertEqual(
            (response.status_code, response.json(), self.recorded_states),
            (
                422,
                {"detail": "Code Mode is unavailable in team sessions."},
                [],
            ),
        )

    def test_code_mode_rejects_unsupported_environment(self) -> None:
        """Enabling fails before storage for unsupported server setups."""
        cases = [
            (
                "unsupported backend",
                patch.object(
                    LocalBackend,
                    "start_process",
                    BackendBase.start_process,
                ),
                422,
            ),
            (
                "missing dependency",
                patch(
                    "agentscope.app._service._toolkit.find_spec",
                    return_value=None,
                ),
                503,
            ),
        ]
        for environment, context, expected in cases:
            with self.subTest(environment=environment):
                with context:
                    updated = self._patch({"code_mode": True})
                    created = self._create(code_mode=True)
                self.assertEqual(
                    (
                        updated.status_code,
                        created.status_code,
                        self.recorded_states,
                        self._listed()["session"]["config"]["code_mode"],
                    ),
                    (expected, expected, [], False),
                )

    def test_code_mode_cannot_change_during_a_reply(self) -> None:
        """A running or parked reply must keep its original tool surface."""
        with patch.object(self.bus, "is_locked", AsyncMock(return_value=True)):
            self.assertEqual(self._patch({"code_mode": True}).status_code, 409)
        for state in (ToolCallState.ASKING, ToolCallState.SUBMITTED):
            with self.subTest(state=state):
                record = self.client.portal.call(
                    self.storage.get_session,
                    "alice",
                    self.agent_id,
                    self.session_id,
                )
                record.state.context = [
                    AssistantMsg(
                        name="ann",
                        content=[
                            ToolCallBlock(
                                id="pending-call",
                                name="pending",
                                input="{}",
                                state=state,
                            ),
                        ],
                    ),
                ]
                self.client.portal.call(
                    partial(
                        self.storage.upsert_session,
                        "alice",
                        self.agent_id,
                        record.config,
                        state=record.state,
                        session_id=self.session_id,
                    ),
                )
                self.recorded_states.clear()
                self.assertEqual(
                    (
                        self._patch({"code_mode": True}).status_code,
                        self.recorded_states,
                    ),
                    (409, []),
                )
