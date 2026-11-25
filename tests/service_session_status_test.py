# -*- coding: utf-8 -*-
"""Session status must resolve user ownership before checking run locks."""
import importlib.util
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock

from agentscope.app._service import SessionService
from agentscope.app._service._session import SessionStatus
from agentscope.app.message_bus import MessageBus, MessageBusKeys
from agentscope.app.storage import SessionConfig, SessionRecord, StorageBase
from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState


def _make_service() -> (
    tuple[
        SessionService,
        AsyncMock,
        AsyncMock,
        SessionRecord,
    ]
):
    """Create a service with one session in user-scoped storage."""
    session = SessionRecord(
        id="session-id",
        user_id="owner",
        agent_id="agent-id",
        config=SessionConfig(workspace_id="workspace-id"),
    )
    storage = AsyncMock(spec=StorageBase)
    # Both production storage backends scope session lookup by user and
    # session ID. Keep their existing agent_id semantics in this fixture.
    storage.get_session.side_effect = lambda user_id, _agent_id, session_id: (
        session
        if user_id == session.user_id and session_id == session.id
        else None
    )
    bus = AsyncMock(spec=MessageBus)
    bus.is_locked.return_value = False
    return SessionService(storage, bus), storage, bus, session


class SessionStatusServiceTest(IsolatedAsyncioTestCase):
    """Exercise ownership and run-lock precedence through the service."""

    async def test_unavailable_sessions_ignore_run_locks(self) -> None:
        """Missing and other-user sessions have no caller-visible status."""
        for locked in (False, True):
            for user_id, session_id in (
                ("owner", "missing-session"),
                ("other-user", "session-id"),
            ):
                with self.subTest(
                    locked=locked,
                    user_id=user_id,
                    session_id=session_id,
                ):
                    service, storage, bus, _ = _make_service()
                    bus.is_locked.return_value = locked
                    result = await service.get_session_status(
                        user_id,
                        "agent-id",
                        session_id,
                    )
                    self.assertIsNone(result)
                    storage.get_session.assert_awaited_once_with(
                        user_id,
                        "agent-id",
                        session_id,
                    )
                    bus.is_locked.assert_not_awaited()

    async def test_owned_running_session_overrides_parked_state(self) -> None:
        """A valid live run takes precedence over a stale asking snapshot."""
        service, storage, bus, session = _make_service()
        bus.is_locked.return_value = True
        session.state.context = [
            AssistantMsg(
                name="agent",
                content=[
                    ToolCallBlock(
                        id="tool-call-id",
                        name="tool",
                        input="{}",
                        state=ToolCallState.ASKING,
                    ),
                ],
            ),
        ]
        self.assertEqual(
            await service.get_session_status(
                "owner",
                "agent-id",
                "session-id",
            ),
            SessionStatus.RUNNING,
        )
        storage.get_session.assert_awaited_once_with(
            "owner",
            "agent-id",
            "session-id",
        )
        bus.is_locked.assert_awaited_once_with(
            MessageBusKeys.session_lock("session-id"),
        )

    async def test_owned_unlocked_session_uses_parked_state(self) -> None:
        """The idle and two parked statuses remain unchanged."""
        for tool_state, expected in (
            (None, SessionStatus.IDLE),
            (ToolCallState.ASKING, SessionStatus.AWAITING_PERMISSION),
            (ToolCallState.SUBMITTED, SessionStatus.AWAITING_EXTERNAL_RESULT),
        ):
            with self.subTest(tool_state=tool_state):
                service, _, _, session = _make_service()
                if tool_state is not None:
                    session.state.context = [
                        AssistantMsg(
                            name="agent",
                            content=[
                                ToolCallBlock(
                                    id="tool-call-id",
                                    name="tool",
                                    input="{}",
                                    state=tool_state,
                                ),
                            ],
                        ),
                    ]
                self.assertEqual(
                    await service.get_session_status(
                        "owner",
                        "agent-id",
                        "session-id",
                    ),
                    expected,
                )


@skipUnless(
    importlib.util.find_spec("fastapi") is not None,
    "fastapi is required for session status endpoint tests",
)
class SessionStatusEndpointTest(TestCase):
    """Check the real status router without starting the full service."""

    def setUp(self) -> None:
        """Mount the session router with controlled storage and bus state."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from agentscope.app._router._session import session_router
        from agentscope.app.deps import get_session_service

        self.service, self.storage, self.bus, _ = _make_service()
        app = FastAPI()
        app.include_router(session_router)
        app.dependency_overrides[get_session_service] = lambda: self.service
        self.client = self.enterContext(TestClient(app))

    def test_unavailable_sessions_return_404(self) -> None:
        """A held lock cannot turn an unavailable session into HTTP 200."""
        for locked in (False, True):
            for user_id, session_id in (
                ("owner", "missing-session"),
                ("other-user", "session-id"),
            ):
                with self.subTest(
                    locked=locked,
                    user_id=user_id,
                    session_id=session_id,
                ):
                    self.bus.is_locked.return_value = locked
                    response = self.client.get(
                        f"/sessions/{session_id}/status",
                        params={"agent_id": "agent-id"},
                        headers={"X-User-ID": user_id},
                    )
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(
                        response.json(),
                        {"detail": f"Session '{session_id}' not found."},
                    )

    def test_owned_running_session_returns_200(self) -> None:
        """An owned session still reports its live running state."""
        self.bus.is_locked.return_value = True
        response = self.client.get(
            "/sessions/session-id/status",
            params={"agent_id": "agent-id"},
            headers={"X-User-ID": "owner"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"session_id": "session-id", "status": "running"},
        )
