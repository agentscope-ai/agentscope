# -*- coding: utf-8 -*-
"""Tests for the Session Fork HTTP endpoint."""

import inspect
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agentscope.app._router._session import fork_session, session_router
from agentscope.app.deps import get_current_user_id, get_session_service
from agentscope.app.storage import (
    SessionForkConflictError,
    SessionForkCorruptedGraphError,
    SessionForkNotFoundError,
)


class TestSessionForkRouter(IsolatedAsyncioTestCase):
    """Verify HTTP mapping and the Router-to-Service boundary."""

    def setUp(self) -> None:
        self.service = SimpleNamespace(
            fork_session=AsyncMock(
                return_value=SimpleNamespace(id="forked-session"),
            ),
        )
        self.app = FastAPI()
        self.app.include_router(session_router)
        self.app.dependency_overrides[
            get_current_user_id
        ] = lambda: "authenticated-user"
        self.app.dependency_overrides[
            get_session_service
        ] = lambda: self.service
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    async def test_success_returns_session_id(self) -> None:
        """A successful Service call returns the new Session ID."""
        response = await fork_session(
            "source-session",
            agent_id="agent-1",
            user_id="user-1",
            session_service=self.service,
        )

        self.assertEqual(response.session_id, "forked-session")
        self.service.fork_session.assert_awaited_once_with(
            "user-1",
            "agent-1",
            "source-session",
        )

    async def test_http_success_uses_authenticated_user(self) -> None:
        """The real endpoint returns 201 and ignores a fake user query."""
        response = self.client.post(
            "/sessions/source-session/fork",
            params={"agent_id": "agent-1", "user_id": "attacker"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"session_id": "forked-session"})
        self.service.fork_session.assert_awaited_once_with(
            "authenticated-user",
            "agent-1",
            "source-session",
        )

    async def test_http_error_mappings(self) -> None:
        """The real endpoint preserves the 404/409/500 contract."""
        cases = (
            (SessionForkNotFoundError("not found"), 404),
            (SessionForkConflictError("conflict"), 409),
            (SessionForkCorruptedGraphError("corrupted"), 500),
        )
        for error, expected_status in cases:
            self.service.fork_session.reset_mock(side_effect=True)
            self.service.fork_session.side_effect = error
            response = self.client.post(
                "/sessions/source-session/fork",
                params={"agent_id": "agent-1"},
            )
            self.assertEqual(response.status_code, expected_status)

    async def test_route_is_service_only(self) -> None:
        """The endpoint has no Storage dependency."""
        parameters = inspect.signature(fork_session).parameters
        self.assertNotIn("storage", parameters)
        self.assertIn("session_service", parameters)

    async def test_not_found_maps_to_404(self) -> None:
        """NotFound is exposed as HTTP 404 without internal detail."""
        self.service.fork_session.side_effect = SessionForkNotFoundError(
            "internal ownership detail",
        )

        with self.assertRaises(HTTPException) as context:
            await fork_session(
                "source-session",
                agent_id="agent-1",
                user_id="user-1",
                session_service=self.service,
            )

        self.assertEqual(context.exception.status_code, 404)
        self.assertNotIn("internal", str(context.exception.detail))

    async def test_conflict_maps_to_409(self) -> None:
        """Conflict is exposed as HTTP 409."""
        self.service.fork_session.side_effect = SessionForkConflictError(
            "internal status detail",
        )

        with self.assertRaises(HTTPException) as context:
            await fork_session(
                "source-session",
                agent_id="agent-1",
                user_id="user-1",
                session_service=self.service,
            )

        self.assertEqual(context.exception.status_code, 409)

    @patch("agentscope.app._router._session.logger.exception")
    async def test_corrupted_graph_logs_and_maps_to_generic_500(
        self,
        log_exception: AsyncMock,
    ) -> None:
        """Corrupted Graph is logged and hidden behind HTTP 500."""
        self.service.fork_session.side_effect = SessionForkCorruptedGraphError(
            "internal Redis graph detail",
        )

        with self.assertRaises(HTTPException) as context:
            await fork_session(
                "source-session",
                agent_id="agent-1",
                user_id="user-1",
                session_service=self.service,
            )

        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(
            context.exception.detail,
            "Unable to fork the session.",
        )
        log_exception.assert_called_once()
